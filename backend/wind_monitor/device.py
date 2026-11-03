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

PAST = {"approve": "approved", "reject": "rejected"}

# Characters per line on the 128x160 ST7735 at text size 1 (6x8 px font).
SCREEN_WIDTH = 21
MAX_WINDOW_LINES = 3
# Log lines the device sent, kept per device so it can be debugged without a USB cable.
MAX_LOG_LINES = 200


@dataclass
class Proposal:
    id: str  # the agent run it came from
    question: str
    answer: str
    schedule: list[dict]  # [{"start", "end" (UTC ISO), "energy_kwh"}]
    plan: dict  # the plan_charge result
    facts: str  # fact-check summary, e.g. "facts checked 4/4"
    job: dict | None = None  # energy, power, deadline: what the watcher re-plans
    title: str = "Charge plan?"

    @classmethod
    def from_run(cls, run: AgentRun, title: str = "Charge plan?") -> Proposal:
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
            run.job,
            title,
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
    alert: str | None = None  # one short line from the watcher, e.g. "Wind 18% below plan"
    # A remote relay test (real time, not the planning clock) overrides the plan while it runs.
    test_relay: bool = False
    test_until: pd.Timestamp | None = None
    # Firmware version the device was asked to install; cleared once it reports running it.
    update_to: str | None = None
    logs: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    # Speech: sentences waiting to be spoken, what has been announced, and the newest clip.
    to_say: deque = field(default_factory=lambda: deque(maxlen=5))
    said: dict = field(default_factory=dict)
    say: dict | None = None  # {"id": clip id, "text": ...}

    def testing(self, real_now: pd.Timestamp) -> bool:
        return self.test_until is not None and real_now < self.test_until

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


def effective_relay(state: DeviceState, now: pd.Timestamp, real_now: pd.Timestamp) -> bool:
    """A running relay test wins; otherwise the approved plan decides, by the planning clock."""
    if state.testing(real_now):
        return state.test_relay
    return relay_on(state.active, now)


def relay_on(active: Proposal | None, now: pd.Timestamp) -> bool:
    return bool(active) and any(start <= now < end for start, end in active.windows())


def _gw_words(mw: float | None) -> str:
    return "hard to say right now" if mw is None else f"about {mw / 1000:.1f} gigawatts"


def news(state: DeviceState, now: pd.Timestamp, wind_mw: float | None) -> str | None:
    """The next thing the device should say, if anything (and mark it said).

    Queued sentences first (button confirmations, voice answers); then a new proposal; then the
    approved plan switching the load on or off (by the planning clock, so a fast replay is heard
    too; relay tests are not announced); then a new or cleared watcher alert."""
    if state.to_say:
        return state.to_say.popleft()
    said = state.said
    if state.proposal and said.get("proposal") != state.proposal.id:
        said["proposal"] = state.proposal.id
        p = state.proposal
        if p.title.startswith("Better"):
            return (
                f"Good news, the forecast has changed. {p.answer or ''} "
                "Want to switch? Hold the button for yes, or tap for no."
            )
        return (
            f"Here's my plan. {p.answer or ''} "
            "Shall I go ahead? Hold the button for yes, or tap for no."
        )
    plan_on = relay_on(state.active, now)
    if plan_on != said.get("relay", False):
        said["relay"] = plan_on
        if plan_on:
            return f"Charging now. The wind across Britain is {_gw_words(wind_mw)}."
        if state.active and not any(s > now for s, _ in state.active.windows()):
            kwh = sum(w["energy_kwh"] for w in state.active.schedule)
            return f"All done! {kwh:g} kilowatt hours, charged in the windiest hours."
        return "Pausing for now. I'll pick up again in the next windy window."
    if state.alert != said.get("alert"):
        said["alert"] = state.alert
        if state.alert:
            return f"Heads up. {state.alert}. I'm keeping an eye on it."
    return None


def screen(
    state: DeviceState,
    now: pd.Timestamp,
    latest: dict | None,
    real_now: pd.Timestamp | None = None,
) -> dict:
    """What the device shows: a mode, a title, up to six lines and a button prompt."""
    wind_now = f"Now {_gw(latest['actual_mw'])}" if latest else "Now: no data"
    if real_now is not None and state.testing(real_now):
        left = max(1, int((state.test_until - real_now).total_seconds()))
        relay = "ON" if state.test_relay else "OFF"
        lines = [f"Relay {relay} for {left} s", "(from the server)"]
        return _view("test", "Relay test", lines, None)
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
        better = p.plan.get("more_wind_than_current_plan_pct")
        gain = p.plan.get("more_wind_than_charging_straight_away_pct")
        if better is not None:
            lines.append(f"{better:+g}% vs old plan")
        elif gain is not None:
            lines.append(f"{gain:+g}% vs plug-in")
        lines.append(p.facts)
        return _view("proposal", p.title, lines, "hold:yes  tap:no")

    if state.active:
        windows = state.active.windows()
        expected = f"Plan ~{_gw(state.active.plan.get('expected_wind_while_charging_mw'))}"
        current = next(((s, e) for s, e in windows if s <= now < e), None)
        alert = [state.alert] if state.alert else []
        if current:
            return _view(
                "charging",
                "CHARGING",
                [f"until {_hhmm(current[1])}", expected, wind_now, *alert],
                "hold:stop",
            )
        upcoming = [(s, e) for s, e in windows if s > now]
        if upcoming:
            s, e = upcoming[0]
            return _view(
                "waiting",
                "Waiting for wind",
                [f"Next {_hhmm(s)}-{_hhmm(e)}", expected, wind_now, *alert],
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

    def propose(
        self, device_id: str, run: AgentRun, now: pd.Timestamp, title: str = "Charge plan?"
    ) -> DeviceState:
        proposal = Proposal.from_run(run, title)
        self.propose_proposal(device_id, proposal, now)
        with self._lock:
            return self._get(device_id)

    def event(self, device_id: str, event: dict, now: pd.Timestamp) -> DeviceState:
        kind = event.get("type")
        with self._lock:
            state = self._get(device_id)
            state.last_seen = now.isoformat()
            received = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
            for line in event.get("log") or []:
                state.logs.append({"at": received, "line": line})
            if kind in ("approve", "reject"):
                pending = state.proposal
                if pending is None or event.get("proposal_id") != pending.id:
                    raise DeviceError("No such proposal waiting (it may have been replaced)")
                state.proposal = None
                if kind == "approve":
                    state.active = pending
                    state.alert = None
                    starts = [s for s, _ in pending.windows() if s > now]
                    when = (
                        f"I'll start charging at {_hhmm(starts[0])}." if starts else "Charging now."
                    )
                    state.to_say.append(f"Great, it's booked. {when}")
                else:
                    state.to_say.append("No problem, I've dropped that plan.")
                state.changed(f"{PAST[kind]} {pending.id}", now)
            elif kind == "stop":
                if state.active is not None:
                    state.changed(f"stopped {state.active.id}", now)
                    state.active = None
                    state.alert = None
                    state.said["relay"] = False
                    state.to_say.append("Okay, I've stopped charging.")
            elif kind == "telemetry":
                state.telemetry = {k: v for k, v in event.items() if k not in ("type", "log")}
                if state.update_to and state.telemetry.get("firmware") == state.update_to:
                    state.changed(f"updated to {state.update_to}", now)
                    state.update_to = None
            else:
                raise DeviceError(f"Unknown event type {kind!r}")
            return state

    def with_active_plans(self) -> list[DeviceState]:
        with self._lock:
            return [s for s in self._devices.values() if s.active is not None]

    def propose_proposal(self, device_id: str, proposal: Proposal, now: pd.Timestamp) -> None:
        with self._lock:
            state = self._get(device_id)
            state.proposal = proposal
            state.changed(f"proposed {proposal.id}", now)

    def set_alert(self, device_id: str, alert: str | None, now: pd.Timestamp) -> None:
        with self._lock:
            state = self._get(device_id)
            if state.alert != alert:
                state.alert = alert
                state.changed(f"alert: {alert}" if alert else "alert cleared", now)

    def relay_test(
        self, device_id: str, relay: bool, seconds: int, real_now: pd.Timestamp
    ) -> DeviceState:
        with self._lock:
            state = self._get(device_id)
            state.test_relay = relay
            state.test_until = real_now + pd.Timedelta(seconds=seconds)
            state.changed(f"relay test: {'on' if relay else 'off'} for {seconds} s", real_now)
            return state

    def request_update(self, device_id: str, version: str | None, now: pd.Timestamp) -> DeviceState:
        """Offer firmware `version` to the device on its next poll (None cancels the offer)."""
        with self._lock:
            state = self._get(device_id)
            state.update_to = version
            state.changed(f"update to {version} requested" if version else "update cancelled", now)
            return state

    def queue_say(self, device_id: str, text: str) -> None:
        with self._lock:
            self._get(device_id).to_say.append(text)

    def next_news(self, device_id: str, now: pd.Timestamp, wind_mw: float | None) -> str | None:
        with self._lock:
            return news(self._get(device_id), now, wind_mw)

    def set_say(self, device_id: str, clip_id: int, text: str, now: pd.Timestamp) -> None:
        with self._lock:
            state = self._get(device_id)
            state.say = {"id": clip_id, "text": text}
            state.events.appendleft({"at": now.isoformat(), "event": f"said: {text[:80]}"})

    def heard(self, device_id: str, transcript: str, now: pd.Timestamp) -> None:
        with self._lock:
            event = {"at": now.isoformat(), "event": f"heard: {transcript[:80] or '(nothing)'}"}
            self._get(device_id).events.appendleft(event)

    def seen(self, device_id: str, now: pd.Timestamp) -> DeviceState:
        with self._lock:
            state = self._get(device_id)
            state.last_seen = now.isoformat()
            return state


_registry = DeviceRegistry()


def get_registry() -> DeviceRegistry:
    return _registry
