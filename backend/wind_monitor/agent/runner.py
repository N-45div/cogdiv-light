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
from dataclasses import asdict, dataclass, field

import pandas as pd

from ..flex import blocks
from ..store import WindData
from . import factcheck
from .llm import ChatClient, ModelUnavailableError
from .tools import TOOLS, Toolbox, uk_label

SYSTEM_PROMPT = """\
You plan a household's flexible electricity use in Great Britain (an EV charger, a hot water \
tank) so it runs when GB wind generation is high.

Rules:
- Use the tools for every fact about wind, forecasts or plans. Never guess.
- Quote figures exactly as the tools give them. You may round (6,628 MW -> about 6,600 MW or \
6.6 GW) but never calculate new numbers: the tools already give every comparison you need.
- Times are UK local time; write them as HH:MM.
- If the user wants to charge or run something, call plan_charge. Without a deadline from the \
user, leave it out and the tool uses the next 07:00.
- If a tool returns an error, fix the arguments and call it again.
- Answer in at most three short sentences: it may be shown on a small screen.
"""
REWRITE_PROMPT = (
    "These figures in your answer don't match the tool results: {figures}. Rewrite the answer "
    "using only figures that appear in the tool results (rounding is fine)."
)
MAX_ROUNDS = 6
MAX_REWRITES = 1


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
    plan: dict | None = None  # the last plan_charge result, if any
    # That plan's charging windows as exact times (UTC ISO), for a device to act on.
    schedule: list[dict] = field(default_factory=list)
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
) -> AgentRun:
    start = time.monotonic()
    run = AgentRun(question=question, as_of=now.isoformat(), mode=mode)
    toolbox = Toolbox(data, now)
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
    plans = [c["result"] for c in toolbox.calls if c["name"] == "plan_charge"]
    run.plan = next((p for p in reversed(plans) if "error" not in p), None)
    if run.plan is not None and toolbox.last_plan is not None:
        run.schedule = [
            {
                "start": b["start"].isoformat(),
                "end": b["end"].isoformat(),
                "energy_kwh": round(b["energy_kwh"], 3),
            }
            for b in blocks(toolbox.last_plan.slots)
        ]
    run.seconds = round(time.monotonic() - start, 2)
    return run
