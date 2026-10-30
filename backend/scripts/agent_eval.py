"""Evaluate the real agent night by night on the January replay.

Usage (needs a key for each model tried):
    MODEL_API_KEY=... BASETEN_API_KEY=... python backend/scripts/agent_eval.py \
        --models meta:muse-spark-1.3 baseten:meta-models/muse-spark-open-1026

For each night, at 18:00, the agent is asked (in rotating wordings) to plan a 21 kWh charge at
7 kW, done by 07:00. Measured per model:
    verified       every figure in the answer matched a tool result (after at most one rewrite)
    rewrites       answers that needed the rewrite
    right plan     the agent's plan equals the planner's own plan for that job, i.e. it called
                   the tool with the right numbers
    seconds        time per question
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wind_monitor.agent.llm import DEFAULT_REASONING_EFFORT, ChatClient, ModelRef  # noqa: E402
from wind_monitor.agent.runner import run_agent  # noqa: E402
from wind_monitor.agent.tools import Toolbox  # noqa: E402
from wind_monitor.config import Settings  # noqa: E402
from wind_monitor.store import load_snapshot  # noqa: E402

QUESTIONS = [
    "I've just plugged in my EV. It needs 21 kWh and charges at 7 kW. It must be ready by 07:00. "
    "When should it charge?",
    "Need 21 kWh in the car by 7am, charger is 7 kW. What's the windiest time to charge tonight?",
    "Plan my 21 kWh EV charge on a 7 kW charger, finished by 07:00, with as much wind as possible.",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--models", nargs="+", default=["meta:muse-spark-1.3"])
    parser.add_argument("--first", default="2026-01-05")
    parser.add_argument("--nights", type=int, default=27)
    parser.add_argument("--show", action="store_true", help="print every answer")
    args = parser.parse_args()

    data = load_snapshot(Settings.from_env().snapshot_dir)
    first = date.fromisoformat(args.first)
    nights = [first + timedelta(days=i) for i in range(args.nights)]
    rows = {}
    for text in args.models:
        client = ChatClient([ModelRef.parse(text)], reasoning_effort=DEFAULT_REASONING_EFFORT)
        verified = rewrites = right = answered = 0
        seconds, problems = [], []
        for i, night in enumerate(nights):
            now = pd.Timestamp(f"{night}T18:00", tz="Europe/London").tz_convert("UTC")
            question = QUESTIONS[i % len(QUESTIONS)]
            run = run_agent(question, data, now, client)
            reference = Toolbox(data, now).plan_charge(21, 7, deadline="07:00")
            answered += run.answer is not None
            verified += run.verified
            rewrites += run.rewrites
            plan_ok = bool(run.plan) and run.plan["charge_windows"] == reference["charge_windows"]
            right += plan_ok
            seconds.append(run.seconds)
            if not (run.verified and plan_ok):
                claims = (run.factcheck or {}).get("claims", [])
                bad = [c["text"] for c in claims if not c["supported"]]
                problems.append(f"{night}: verified={run.verified} plan_ok={plan_ok} {bad}")
            if args.show:
                summary = run.factcheck and run.factcheck["summary"]
                print(f"[{text} {night}] {summary}: {run.answer}")
        n = len(nights)
        rows[text] = {
            "answered": f"{answered}/{n}",
            "verified": f"{verified}/{n}",
            "needed a rewrite": f"{rewrites}/{n}",
            "right plan": f"{right}/{n}",
            "median seconds": round(statistics.median(seconds), 1),
            "slowest seconds": round(max(seconds), 1),
        }
        for line in problems:
            print(f"  {text} | {line}")
    print(f"\n## Agent on the January replay: {len(nights)} nights, 18:00 plug-in\n")
    print(pd.DataFrame(rows).T.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
