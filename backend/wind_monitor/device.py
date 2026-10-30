"""The device side: what each ESP32 shows, and whether its load should be on right now.

A verified agent plan becomes a *proposal* for a device. The device shows it and asks
"hold: yes / tap: no". Once approved, the plan's windows drive the relay: on inside a
charging window, off outside, by the planning clock (so a replay fast-forward drives the
real relay). Screen text is built here from the planner's results, never by the model, so the
device shows exactly what the tested planner said.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field

import pandas as pd

from .agent.runner import AgentRun
from .config import UK_TZ

# Characters per line on the 128x160 ST7735 at text size 1 (6x8 px font).
SCREEN_WIDTH = 21
MAX_WINDOW_LINES = 3


@dataclass
class Proposal:
    id: str  # the agent run it came from
    question: str
    answer: str
    schedule: list[dict]  # [{"start", "end" (UTC ISO), "energy_kwh"}]
    plan: dict  # the plan_charge result
    facts: str  # fact-check summary, e.g. "facts checked 4/4"

    @classmethod
    def from_run(cls, run: AgentRun) -> Proposal:
        """Only a verified run with a feasible plan may become a proposal."""
        if not run.verified:
            raise ValueError("Only fact-checked answers can be sent to a device")
        if not run.plan or not run.schedule or not run.plan.get("feasible"):
            raise ValueError("This run has no feasible charging plan")
        return cls(
            run.id,
            run.question,
            run.answer or "",
            run.schedule,
            run.plan,
            run.factcheck["summary"] if run.factcheck else "",
        )

    def windows(self) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        return [(pd.Timestamp(w["start"]), pd.Timestamp(w["end"])) for w in self.schedule]


@dataclass
class DeviceState:
    device_id: str
    proposal: Proposal | None = None  # waiting for the button
    active: Proposal | None = None  # approved: drives the relay
    version: int = 0  # bumps on every change, so the device knows to redraw
    last_seen: str | None = None
    telemetry: dict = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=50))

    def changed(self, what: str, now: pd.Timestamp) -> None:
        self.version += 1
        self.events.appendleft({"at": now.isoformat(), "event": what})


class DeviceError(ValueError):
    """A device event that doesn't fit the current state (e.g. approving a stale proposal)."""


def _hhmm(moment: pd.Timestamp) -> str:
    return moment.tz_convert(UK_TZ).strftime("%H:%M")


def _gw(mw: float | None) -> str:
    return "n/a" if mw is None else f"{mw / 1000:.1f} GW"


def _fit(line: str) -> str:
    return line if len(line) <= SCREEN_WIDTH else line[: SCREEN_WIDTH - 1] + "~"


def relay_on(active: Proposal | None, now: pd.Timestamp) -> bool:
    return bool(active) and any(start <= now < end for start, end in active.windows())


def screen(state: DeviceState, now: pd.Timestamp, latest: dict | None) -> dict:
    """What the device shows: a mode, a title, up to six lines and a button prompt."""
    wind_now = f"Now {_gw(latest['actual_mw'])}" if latest else "Now: no data"
    if state.proposal:
        p = state.proposal
        windows = p.windows()
        lines = [
            f"{_hhmm(s)}-{_hhmm(e)} {w['energy_kwh']:g}kWh"
            for (s, e), w in zip(windows[:MAX_WINDOW_LINES], p.schedule, strict=False)
        ]
        if len(windows) > MAX_WINDOW_LINES:
            lines.append(f"+{len(windows) - MAX_WINDOW_LINES} more windows")
        lines.append(f"Wind ~{_gw(p.plan.get('expected_wind_while_charging_mw'))}")
        gain = p.plan.get("more_wind_than_charging_straight_away_pct")
        if gain is not None:
            lines.append(f"{gain:+g}% vs plug-in")
        lines.append(p.facts)
        return _view("proposal", "Charge plan?", lines, "hold:yes  tap:no")

    if state.active:
        windows = state.active.windows()
        expected = f"Plan ~{_gw(state.active.plan.get('expected_wind_while_charging_mw'))}"
        current = next(((s, e) for s, e in windows if s <= now < e), None)
        if current:
            return _view(
                "charging",
                "CHARGING",
                [f"until {_hhmm(current[1])}", expected, wind_now],
                "hold:stop",
            )
        upcoming = [(s, e) for s, e in windows if s > now]
        if upcoming:
            s, e = upcoming[0]
            return _view(
                "waiting",
                "Waiting for wind",
                [f"Next {_hhmm(s)}-{_hhmm(e)}", expected, wind_now],
                "hold:stop",
            )
        kwh = sum(w["energy_kwh"] for w in state.active.schedule)
        return _view("done", "Charge done", [f"{kwh:g} kWh", expected, wind_now], None)

    at = f"at {_hhmm(pd.Timestamp(latest['time']))}" if latest else ""
    return _view("idle", "Wind now", [wind_now.replace("Now ", ""), at, "Ask me to plan"], None)


def _view(mode: str, title: str, lines: list[str], prompt: str | None) -> dict:
    return {
        "mode": mode,
        "title": _fit(title),
        "lines": [_fit(line) for line in lines if line][:6],
        "prompt": prompt,
    }


class DeviceRegistry:
    """Every device's state, in memory (one process)."""

    def __init__(self) -> None:
        self._devices: dict[str, DeviceState] = {}
        self._lock = threading.Lock()

    def _get(self, device_id: str) -> DeviceState:
        if device_id not in self._devices:
            self._devices[device_id] = DeviceState(device_id)
        return self._devices[device_id]

    def all(self) -> list[DeviceState]:
        with self._lock:
            return list(self._devices.values())

    def propose(self, device_id: str, run: AgentRun, now: pd.Timestamp) -> DeviceState:
        proposal = Proposal.from_run(run)
        with self._lock:
            state = self._get(device_id)
            state.proposal = proposal
            state.changed(f"proposed {proposal.id}", now)
            return state

    def event(self, device_id: str, event: dict, now: pd.Timestamp) -> DeviceState:
        kind = event.get("type")
        with self._lock:
            state = self._get(device_id)
            state.last_seen = now.isoformat()
            if kind in ("approve", "reject"):
                pending = state.proposal
                if pending is None or event.get("proposal_id") != pending.id:
                    raise DeviceError("No such proposal waiting (it may have been replaced)")
                state.proposal = None
                if kind == "approve":
                    state.active = pending
                state.changed(f"{kind}d {pending.id}", now)
            elif kind == "stop":
                if state.active is not None:
                    state.changed(f"stopped {state.active.id}", now)
                    state.active = None
            elif kind == "telemetry":
                state.telemetry = {k: v for k, v in event.items() if k != "type"}
            else:
                raise DeviceError(f"Unknown event type {kind!r}")
            return state

    def seen(self, device_id: str, now: pd.Timestamp) -> DeviceState:
        with self._lock:
            state = self._get(device_id)
            state.last_seen = now.isoformat()
            return state


_registry = DeviceRegistry()


def get_registry() -> DeviceRegistry:
    return _registry
