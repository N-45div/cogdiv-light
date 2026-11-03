"""The agent loop: ask a model, run the tools it calls, check its answer, rewrite once if needed.

1. The model gets the question, the current UK time and the tools.
2. Each tool call runs against the data as of the clock (no look-ahead in replay).
3. The final answer is fact-checked against this turn's tool results. If a figure doesn't
   match, the model is told which ones and asked to rewrite once. If it still doesn't match,
   the answer is marked unverified, and a plan from an unverified answer is never dispatched.
4. Everything is recorded: models used, each tool call and result, timings and the check.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import pandas as pd

from ..carbon import CarbonForecast
from ..flex import blocks
from ..store import WindData
from . import factcheck
from .llm import ChatClient, ModelUnavailableError
from .tools import TOOLS, Toolbox, uk_label

SYSTEM_PROMPT = """\
You plan flexible electricity use in Great Britain so it runs on the windiest, cleanest power: \
a household's EV charger or hot water tank, and flexible compute jobs (AI training, \
fine-tuning, batch inference) that a data centre can move in time and place.

Rules:
- Use the tools for every fact about wind, carbon, forecasts or plans. Never guess.
- Quote figures exactly as the tools give them. You may round (6,628 MW -> about 6,600 MW or \
6.6 GW) but never calculate new numbers: the tools already give every comparison you need.
- Times are UK local time; write them as HH:MM.
- If the user wants to charge or run a household load, call plan_charge (goal "carbon" if they \
ask for the cleanest or lowest-carbon time). Without a deadline from the user, leave it out and \
the tool uses the next 07:00.
- For a compute job (GPUs, training, inference, a data centre), call plan_compute_job with the \
GPUs and hours (or GPU-hours) the user gives. Say where and when it should run and how much \
less CO2 that is than starting now in London.
- For how clean the grid is, or which region is cleanest, call get_carbon_outlook.
- If a tool returns an error, fix the arguments and call it again.
- Answer in at most three short sentences: it may be shown on a small screen.
"""
REWRITE_PROMPT = (
    "These figures in your answer don't match the tool results: {figures}. Rewrite the answer "
    "using only figures that appear in the tool results (rounding is fine)."
)
MAX_ROUNDS = 6
MAX_REWRITES = 1
# Tools whose result is a plan a device can act on, and the kind of plan.
PLAN_TOOLS = {"plan_charge": "charge", "plan_compute_job": "compute"}


@dataclass
class AgentRun:
    question: str
    as_of: str
    mode: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    answer: str | None = None
    verified: bool = False
    factcheck: dict | None = None
    models: list[str] = field(default_factory=list)  # model used for each model call, in order
    tool_calls: list[dict] = field(default_factory=list)
    plan: dict | None = None  # the last plan_charge or plan_compute_job result, if any
    kind: str = "charge"  # "charge" (a household load) or "compute" (a compute job)
    # That plan's charging windows as exact times (UTC ISO), for a device to act on.
    schedule: list[dict] = field(default_factory=list)
    # The job behind that plan (energy, power, earliest, deadline as UTC ISO), so the watcher
    # can re-plan what is left of it.
    job: dict | None = None
    rewrites: int = 0
    seconds: float = 0.0
    error: str | None = None
    failed_models: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def run_agent(
    question: str,
    data: WindData,
    now: pd.Timestamp,
    client: ChatClient,
    mode: str = "replay",
    max_rounds: int = MAX_ROUNDS,
    carbon: Callable[[], CarbonForecast] | None = None,
) -> AgentRun:
    start = time.monotonic()
    run = AgentRun(question=question, as_of=now.isoformat(), mode=mode)
    toolbox = Toolbox(data, now, carbon)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{question}\n\n(It is now {uk_label(now, True)} UK time.)"},
    ]
    try:
        for _ in range(max_rounds):
            result = client.chat(messages, tools=TOOLS)
            run.models.append(result.model)
            run.failed_models += result.failed
            message = result.message
            calls = message.get("tool_calls") or []
            if calls:
                messages.append(
                    {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
                )
                for call in calls:
                    function = call.get("function") or {}
                    output = toolbox.run(function.get("name", ""), function.get("arguments"))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(output),
                        }
                    )
                continue

            run.answer = (message.get("content") or "").strip()
            check = factcheck.check(run.answer, toolbox.calls, question)
            run.factcheck = check.as_dict()
            if check.passed or run.rewrites >= MAX_REWRITES:
                run.verified = check.passed
                break
            run.rewrites += 1
            figures = ", ".join(f"'{c.text}'" for c in check.unsupported)
            messages += [
                {"role": "assistant", "content": run.answer},
                {"role": "user", "content": REWRITE_PROMPT.format(figures=figures)},
            ]
        else:
            run.error = f"No final answer after {max_rounds} model calls"
    except ModelUnavailableError as exc:
        run.error = str(exc)
        run.failed_models += exc.failed

    run.tool_calls = toolbox.calls
    plans = [c for c in toolbox.calls if c["name"] in PLAN_TOOLS and "error" not in c["result"]]
    if plans:
        run.plan = plans[-1]["result"]
        run.kind = PLAN_TOOLS[plans[-1]["name"]]
    if run.plan is not None and run.kind == "charge" and toolbox.last_plan is not None:
        plan = toolbox.last_plan
        run.schedule = schedule(plan.slots)
        run.job = {
            "kind": "charge",
            "goal": run.plan.get("goal", "wind"),
            "energy_kwh": plan.job.energy_kwh,
            "power_kw": plan.job.power_kw,
            "earliest": plan.job.earliest.isoformat(),
            "deadline": plan.job.deadline.isoformat(),
            "interruptible": plan.job.interruptible,
        }
    compute = toolbox.last_compute
    if run.plan is not None and run.kind == "compute" and compute and run.plan.get("feasible"):
        job = compute.job
        run.schedule = schedule(compute.best.slots)
        run.job = {
            "kind": "compute",
            "region_id": compute.best.region_id,
            "regions": [s.region_id for s in compute.sites],
            "baseline_region_id": compute.baseline.region_id,
            "gpus": job.gpus,
            "hours": job.hours,
            "energy_kwh": job.energy_kwh,
            "power_kw": job.power_kw,
            "earliest": job.earliest.isoformat(),
            "deadline": job.deadline.isoformat(),
            "interruptible": job.interruptible,
        }
    run.seconds = round(time.monotonic() - start, 2)
    return run


def schedule(slots: pd.DataFrame) -> list[dict]:
    """A plan's "on" windows as exact times (UTC ISO), for a device to act on."""
    return [
        {
            "start": b["start"].isoformat(),
            "end": b["end"].isoformat(),
            "energy_kwh": round(b["energy_kwh"], 3),
        }
        for b in blocks(slots)
    ]
