"""Check the model providers the agent will use, end to end.

Usage:
    python backend/scripts/model_check.py [--api http://localhost:8000]

For each provider whose key is set in the environment (others are skipped):
  1. the model is listed,
  2. a short reply comes back,
  3. the model calls a tool when it needs data,
  4. given the tool's result (a real plan from our API if --api is up, else a sample), it answers.

Keys are read from the environment and never printed. Prints Markdown (for the GitHub job
summary) and exits non-zero if a provider with a key fails a check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx


@dataclass(frozen=True)
class Provider:
    name: str
    key_env: str
    base_url: str
    model: str


PROVIDERS = [
    Provider(
        "Meta Model API: Muse Spark",
        "MODEL_API_KEY",
        "https://api.meta.ai/v1",
        os.environ.get("META_MODEL", "muse-spark-1.3"),
    ),
    Provider(
        "Baseten: Muse Spark Open",
        "BASETEN_API_KEY",
        "https://inference.baseten.co/v1",
        os.environ.get("BASETEN_MODEL", "meta-models/muse-spark-open-1026"),
    ),
]

PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "plan_ev_charge",
        "description": (
            "Schedule an EV charge into the windiest half-hours before a deadline, using the "
            "GB wind forecast corrected for its usual error. Returns the charging times and the "
            "expected wind while charging."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "energy_kwh": {"type": "number", "description": "Energy needed"},
                "power_kw": {"type": "number", "description": "Charger power"},
            },
            "required": ["energy_kwh", "power_kw"],
        },
    },
}
QUESTION = (
    "My car needs 14 kWh and my charger does 7 kW. When should I charge to use the most wind "
    "power? Use the tool, then answer in two sentences with the times and the expected wind."
)
SAMPLE_PLAN = {
    "blocks": [{"start": "2026-10-30T01:00:00Z", "end": "2026-10-30T03:00:00Z", "energy_kwh": 14}],
    "wind": {"planned": {"expected_mw": 9850}, "asap": {"expected_mw": 7120}},
    "notes": [],
}


class Checks:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.failed += not ok
        print(f"- {'✅' if ok else '❌'} {name}{f': {detail}' if detail else ''}")


def _plan(api: str | None, args: dict) -> dict:
    """A real plan from our API if it is running, else a sample."""
    if api:
        # The tool has no deadline parameter; give the plan a 12-hour window.
        job = {**args, "deadline": (datetime.now(UTC) + timedelta(hours=12)).isoformat()}
        try:
            response = httpx.post(f"{api}/api/plan", json=job, timeout=60)
            if response.status_code == 200:
                body = response.json()
                return {k: body[k] for k in ("as_of", "blocks", "wind", "notes", "feasible")}
        except httpx.HTTPError:
            pass
    return SAMPLE_PLAN


def _short(text: str | None, limit: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def check_provider(p: Provider, api: str | None, checks: Checks) -> None:
    key = os.environ.get(p.key_env)
    print(f"\n## {p.name} (`{p.model}`)\n")
    if not key:
        print(f"Skipped: `{p.key_env}` is not set.")
        return
    http = httpx.Client(
        base_url=p.base_url, headers={"Authorization": f"Bearer {key}"}, timeout=120
    )

    response = http.get("/models")
    ids = [m.get("id") for m in response.json().get("data", [])] if response.is_success else []
    listed = f"HTTP {response.status_code}, {len(ids)} models"
    checks.check("model is listed", p.model in ids, listed)

    def chat(messages: list[dict], **extra) -> tuple[dict | None, float, str]:
        start = time.monotonic()
        r = http.post("/chat/completions", json={"model": p.model, "messages": messages, **extra})
        seconds = time.monotonic() - start
        if not r.is_success:
            return None, seconds, f"HTTP {r.status_code}: {_short(r.text, 200)}"
        return r.json(), seconds, ""

    body, seconds, error = chat([{"role": "user", "content": "Reply with exactly: OK"}])
    reply = body["choices"][0]["message"] if body else {}
    checks.check(
        "short reply",
        bool(body) and "OK" in (reply.get("content") or ""),
        error or f"{_short(reply.get('content'), 40)!r} in {seconds:.1f} s",
    )
    if body:
        extra_fields = sorted(set(reply) - {"role", "content", "tool_calls"})
        print(f"  - message fields besides content: {extra_fields or 'none'}")
        print(f"  - usage: {body.get('usage')}")

    messages = [{"role": "user", "content": QUESTION}]
    body, seconds, error = chat(messages, tools=[PLAN_TOOL], tool_choice="auto")
    message = body["choices"][0]["message"] if body else {}
    calls = message.get("tool_calls") or []
    call = calls[0] if calls else None
    try:
        args = json.loads(call["function"]["arguments"]) if call else None
    except (TypeError, ValueError):
        args = None
    checks.check(
        "calls the tool",
        bool(call) and call["function"]["name"] == "plan_ev_charge" and args is not None,
        error
        or (
            f"{call['function']['name']}({args}) in {seconds:.1f} s"
            if call
            else f"no tool call; said {_short(message.get('content'), 120)!r}"
        ),
    )
    if not call or args is None:
        return

    result = _plan(api, args)
    messages += [
        {"role": "assistant", "content": message.get("content"), "tool_calls": calls},
        {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)},
    ]
    body, seconds, error = chat(messages, tools=[PLAN_TOOL])
    answer = body["choices"][0]["message"].get("content") if body else None
    checks.check("answers from the tool result", bool(answer), error or f"{seconds:.1f} s")
    print(f"\n> {_short(answer, 600)}\n")
    print(f"Tool result given: `{_short(json.dumps(result), 400)}`")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", help="Our API's base URL, for a real plan as the tool result")
    args = parser.parse_args()
    checks = Checks()
    for provider in PROVIDERS:
        check_provider(provider, args.api, checks)
    verdict = f"{checks.failed} check(s) failed" if checks.failed else "All checks passed"
    print(f"\n**{verdict}**")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    sys.exit(main())
