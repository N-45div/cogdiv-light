"""A stand-in for the ESP32: polls the device API, draws the screen in the terminal, presses the
button, switches a pretend relay and reports telemetry, exactly as the firmware does.

Usage:
    python backend/scripts/device_sim.py --api http://localhost:8000 --device desk-1 \
        [--token SECRET] [--approve] [--seconds 120] [--until done]

--approve holds the button (approves) whenever a proposal appears.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

WIDTH = 21


def draw(state: dict) -> str:
    border = "+" + "-" * (WIDTH + 2) + "+"
    rows = [state["title"], "-" * WIDTH, *state["lines"]]
    if state.get("prompt"):
        rows += ["", state["prompt"]]
    relay = "RELAY ON " if state["relay"] else "relay off"
    body = "\n".join(f"| {row:<{WIDTH}} |" for row in rows)
    return f"{border}\n{body}\n{border}  {state['now_uk']} UK  {relay}  [{state['mode']}]"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--device", default="desk-1")
    parser.add_argument("--token")
    parser.add_argument("--approve", action="store_true", help="approve proposals (hold)")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--until", help="stop when the device reaches this mode, e.g. done")
    parser.add_argument("--poll", type=float, help="seconds between polls (default: from API)")
    args = parser.parse_args()

    headers = {"X-Device-Token": args.token} if args.token else {}
    api = httpx.Client(base_url=args.api, headers=headers, timeout=30)
    path = f"/api/device/{args.device}"
    relay, last_drawn, polls = False, None, 0
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        response = api.get(f"{path}/state")
        if response.status_code != 200:
            print(f"! {response.status_code}: {response.text[:120]}")
            time.sleep(args.poll or 5)
            continue
        state = response.json()
        polls += 1
        if state["relay"] != relay:
            relay = state["relay"]
            print(f">>> {state['now_uk']} UK: relay switched {'ON' if relay else 'OFF'}")
        key = (state["version"], state["mode"], state["relay"], tuple(state["lines"]))
        if key != last_drawn:
            print(draw(state))
            last_drawn = key
        if args.approve and state["mode"] == "proposal":
            print(f"*** button held: approving {state['proposal_id']}")
            api.post(f"{path}/event", json={"type": "approve", "proposal_id": state["proposal_id"]})
            continue
        if polls % 6 == 1:
            api.post(f"{path}/event", json={"type": "telemetry", "relay": relay, "firmware": "sim"})
        if args.until and state["mode"] == args.until:
            break
        time.sleep(args.poll or state["poll_seconds"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
