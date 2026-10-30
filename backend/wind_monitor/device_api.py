"""HTTP routes for the ESP32: read what to show, send button presses and telemetry.

A device polls GET /api/device/{id}/state every few seconds. If DEVICE_TOKENS is set
("desk-1:secret,porch:other"), each device must send its token in the X-Device-Token header;
without it (local development) any device id is accepted.
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path

from . import outlook as ol
from .agent.runs import RunLog, get_run_log
from .config import UK_TZ
from .device import DeviceError, DeviceRegistry, DeviceState, get_registry, relay_on, screen
from .planning_api import ClockDep, PlanningDataDep
from .schemas import (
    DEVICE_ID_PATTERN,
    DeviceEvent,
    DeviceSummary,
    DeviceView,
    ProposeRequest,
)
from .store import WindData

router = APIRouter(prefix="/api")

POLL_SECONDS = 5
DeviceId = Annotated[str, Path(pattern=DEVICE_ID_PATTERN)]
RegistryDep = Annotated[DeviceRegistry, Depends(get_registry)]
RunLogDep = Annotated[RunLog, Depends(get_run_log)]


def device_tokens() -> dict[str, str]:
    pairs = (p.split(":", 1) for p in os.environ.get("DEVICE_TOKENS", "").split(",") if ":" in p)
    return {device.strip(): token.strip() for device, token in pairs}


def authorised_device(
    device_id: DeviceId, x_device_token: Annotated[str | None, Header()] = None
) -> str:
    tokens = device_tokens()
    if tokens and tokens.get(device_id) != x_device_token:
        raise HTTPException(401, "Unknown device or wrong X-Device-Token")
    return device_id


AuthorisedDevice = Annotated[str, Depends(authorised_device)]


def view(state: DeviceState, now, data: WindData) -> dict:
    return {
        "device_id": state.device_id,
        "now": now.isoformat(),
        "now_uk": now.tz_convert(UK_TZ).strftime("%H:%M"),
        "version": state.version,
        "relay": relay_on(state.active, now),
        "proposal_id": state.proposal.id if state.proposal else None,
        "poll_seconds": POLL_SECONDS,
        **screen(state, now, ol.latest_actual(data, now)),
    }


@router.get("/device/{device_id}/state", response_model=DeviceView)
def device_state(
    device_id: AuthorisedDevice, clock: ClockDep, data: PlanningDataDep, registry: RegistryDep
) -> dict:
    """What the device should show, and whether its load should be on right now."""
    now = clock.now()
    return view(registry.seen(device_id, now), now, data)


@router.post("/device/{device_id}/event", response_model=DeviceView)
def device_event(
    device_id: AuthorisedDevice,
    event: DeviceEvent,
    clock: ClockDep,
    data: PlanningDataDep,
    registry: RegistryDep,
) -> dict:
    """A button press (approve / reject / stop) or telemetry from the device."""
    now = clock.now()
    try:
        state = registry.event(device_id, event.model_dump(exclude_none=True), now)
    except DeviceError as exc:
        raise HTTPException(409, str(exc)) from exc
    return view(state, now, data)


@router.post("/device/{device_id}/propose", response_model=DeviceView)
def propose(
    device_id: DeviceId,
    request: ProposeRequest,
    clock: ClockDep,
    data: PlanningDataDep,
    registry: RegistryDep,
    log: RunLogDep,
) -> dict:
    """Send an agent run's plan to a device, where it waits for the button."""
    run = log.get(request.run_id)
    if run is None:
        raise HTTPException(404, "No such agent run")
    now = clock.now()
    try:
        state = registry.propose(device_id, run, now)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return view(state, now, data)


@router.get("/devices", response_model=list[DeviceSummary])
def devices(registry: RegistryDep) -> list[dict]:
    return [
        {
            "device_id": s.device_id,
            "last_seen": s.last_seen,
            "version": s.version,
            "proposal_id": s.proposal.id if s.proposal else None,
            "active_id": s.active.id if s.active else None,
            "telemetry": s.telemetry,
            "events": list(s.events)[:10],
        }
        for s in registry.all()
    ]
