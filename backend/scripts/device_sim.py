"""A stand-in for the ESP32: polls the device API, draws the screen in the terminal, presses the
button, switches a pretend relay and reports telemetry and log lines, as the firmware does.

Usage:
    python backend/scripts/device_sim.py --api http://localhost:8000 --device desk-1 \
        [--token SECRET] [--approve] [--seconds 120] [--until done] [--firmware 0.2.0]

--approve holds the button (approves) whenever a proposal appears.
When the server offers a firmware update, the simulator downloads it, checks its size and MD5
like the board does, and then reports the new version (without the restart).
"""

from __future__ import annotations

import argparse
import hashlib
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
    parser.add_argument("--firmware", default="sim", help="version to report")
    args = parser.parse_args()

    headers = {"X-Device-Token": args.token} if args.token else {}
    api = httpx.Client(base_url=args.api, headers=headers, timeout=30)
    path = f"/api/device/{args.device}"
    relay, last_drawn, polls, firmware = False, None, 0, args.firmware
    log = [f"sim {firmware} started"]
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
        offer = state.get("update")
        if offer and offer["version"] != firmware:
            firmware, line = install(api, offer, firmware)
            print(f"*** {line}")
            log.append(line)
        if args.approve and state["mode"] == "proposal":
            print(f"*** button held: approving {state['proposal_id']}")
            api.post(f"{path}/event", json={"type": "approve", "proposal_id": state["proposal_id"]})
            continue
        if polls % 6 == 1 or log:  # every 6th poll, and whenever there's something to log
            telemetry = {"type": "telemetry", "relay": relay, "firmware": firmware, "log": log}
            if api.post(f"{path}/event", json=telemetry).status_code == 200:
                log = []
        if args.until and state["mode"] == args.until:
            break
        time.sleep(args.poll or state["poll_seconds"])
    return 0


def install(api: httpx.Client, offer: dict, running: str) -> tuple[str, str]:
    """Download the offered image and check it as the board does; returns the version now
    running and a log line."""
    response = api.get(offer["path"])
    if response.status_code != 200:
        return running, f"update {offer['version']} failed: HTTP {response.status_code}"
    length = response.headers.get("content-length")
    if length is None or int(length) != offer["size"]:
        return running, f"update {offer['version']} failed: size {length}, expected {offer['size']}"
    if hashlib.md5(response.content).hexdigest() != offer["md5"]:
        return running, f"update {offer['version']} failed: MD5 mismatch"
    return offer["version"], f"updated {running} -> {offer['version']} ({offer['size']} bytes ok)"


if __name__ == "__main__":
    sys.exit(main())
