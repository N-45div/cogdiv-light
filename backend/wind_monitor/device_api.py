"""HTTP routes for the ESP32: read what to show, send button presses and telemetry.

A device polls GET /api/device/{id}/state every few seconds. If DEVICE_TOKENS is set
("desk-1:secret,porch:other"), each device must send its token in the X-Device-Token header;
without it (local development) any device id is accepted.

Remote care: devices send log lines with their events (GET /api/device/{id}/logs), and
POST /api/device/{id}/update offers them the server's firmware image over the air.

Voice: POST /api/device/{id}/voice takes a push-to-talk WAV, transcribes it (Meta Muse Voice
Transcribe) and asks the agent; what the device should say is announced on its next poll
("say" in the state) and downloaded from /api/device/{id}/speech/{clip}.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from . import outlook as ol
from .agent.runner import run_agent
from .agent.runs import RunLog, get_run_log
from .agent_api import CarbonFeedDep, PlannerDep
from .config import UK_TZ
from .device import (
    DeviceError,
    DeviceRegistry,
    DeviceState,
    effective_relay,
    get_registry,
    screen,
)
from .firmware import FirmwareImage, get_firmware
from .planning_api import ClockDep, PlanningDataDep, require_app_token
from .schemas import (
    DEVICE_ID_PATTERN,
    DeviceEvent,
    DeviceSummary,
    DeviceView,
    FirmwareOut,
    ProposeRequest,
    RelayTest,
    UpdateRequest,
    VoiceReply,
)
from .store import WindData
from .voice import Voice, VoiceUnavailable, get_voice, wav_info

router = APIRouter(prefix="/api")

POLL_SECONDS = 5
DeviceId = Annotated[str, Path(pattern=DEVICE_ID_PATTERN)]
RegistryDep = Annotated[DeviceRegistry, Depends(get_registry)]
RunLogDep = Annotated[RunLog, Depends(get_run_log)]
FirmwareDep = Annotated[FirmwareImage | None, Depends(get_firmware)]
VoiceDep = Annotated[Voice, Depends(get_voice)]
SUMMARY_LOG_LINES = 20
MAX_VOICE_SECONDS = 15
STOP_WORDS = re.compile(r"^\W*(please\W+)?(stop|cancel|halt|pause)\b", re.IGNORECASE)
log = logging.getLogger(__name__)


def device_tokens() -> dict[str, str]:
    pairs = (p.split(":", 1) for p in os.environ.get("DEVICE_TOKENS", "").split(",") if ":" in p)
    return {device.strip(): token.strip() for device, token in pairs}


def authorised_device(
    device_id: DeviceId, x_device_token: Annotated[str | None, Header()] = None
) -> str:
    tokens = device_tokens()
    if tokens and not hmac.compare_digest(tokens.get(device_id, ""), x_device_token or "\0"):
        raise HTTPException(401, "Unknown device or wrong X-Device-Token")
    return device_id


AuthorisedDevice = Annotated[str, Depends(authorised_device)]


def offer(state: DeviceState, image: FirmwareImage | None) -> dict | None:
    """The firmware to install: only the version asked for, and only until the device runs it."""
    if image is None or state.update_to != image.version:
        return None
    if state.telemetry.get("firmware") == image.version:
        return None
    return {
        "version": image.version,
        "size": image.size,
        "md5": image.md5,
        "path": f"/api/device/{state.device_id}/firmware",
    }


def announce(
    registry: DeviceRegistry, device_id: str, now, data: WindData, voice: Voice | None
) -> None:
    """Turn the device's next piece of news into a speech clip (one per poll)."""
    if voice is None or not voice.can_speak():
        return
    latest = ol.latest_actual(data, now)
    text = registry.next_news(device_id, now, latest["actual_mw"] if latest else None)
    if not text:
        return
    try:
        clip = voice.speak(text)
    except Exception as exc:  # speech must never break the device's poll
        log.warning("Could not synthesize speech: %s", exc)
        return
    registry.set_say(device_id, clip.id, text, now)


def view(state: DeviceState, now, data: WindData, image: FirmwareImage | None = None) -> dict:
    real_now = pd.Timestamp.now(tz="UTC")
    say = state.say
    return {
        "device_id": state.device_id,
        "now": now.isoformat(),
        "now_uk": now.tz_convert(UK_TZ).strftime("%H:%M"),
        "version": state.version,
        "relay": effective_relay(state, now, real_now),
        "proposal_id": state.proposal.id if state.proposal else None,
        "poll_seconds": POLL_SECONDS,
        "update": offer(state, image),
        "say": None
        if say is None
        else {"id": say["id"], "path": f"/api/device/{state.device_id}/speech/{say['id']}"},
        **screen(state, now, ol.latest_actual(data, now), real_now),
    }


@router.get("/device/{device_id}/state", response_model=DeviceView)
def device_state(
    device_id: AuthorisedDevice,
    clock: ClockDep,
    data: PlanningDataDep,
    registry: RegistryDep,
    image: FirmwareDep,
    voice: VoiceDep,
) -> dict:
    """What the device should show and say, and whether its load should be on right now."""
    now = clock.now()
    registry.seen(device_id, now)
    announce(registry, device_id, now, data, voice)
    return view(registry.seen(device_id, now), now, data, image)


@router.post("/device/{device_id}/event", response_model=DeviceView)
def device_event(
    device_id: AuthorisedDevice,
    event: DeviceEvent,
    clock: ClockDep,
    data: PlanningDataDep,
    registry: RegistryDep,
    image: FirmwareDep,
    voice: VoiceDep,
) -> dict:
    """A button press (approve / reject / stop) or telemetry from the device, with any log
    lines it has collected since its last event."""
    now = clock.now()
    try:
        state = registry.event(device_id, event.model_dump(exclude_none=True), now)
    except DeviceError as exc:
        raise HTTPException(409, str(exc)) from exc
    if event.type != "telemetry":
        announce(registry, device_id, now, data, voice)  # confirm the press out loud
    return view(state, now, data, image)


@router.post("/device/{device_id}/voice", response_model=VoiceReply)
async def device_voice(
    device_id: AuthorisedDevice,
    request: Request,
    clock: ClockDep,
    data: PlanningDataDep,
    registry: RegistryDep,
    voice: VoiceDep,
    planner: PlannerDep,
    log_: RunLogDep,
    carbon: CarbonFeedDep,
) -> dict:
    """Push-to-talk: a WAV (16 kHz mono 16-bit) in the request body. It is transcribed with
    Meta Muse Voice Transcribe and sent to the agent; a verified plan is proposed to this
    device (and read out on its next poll), otherwise the answer is read out."""
    audio = await request.body()
    try:
        rate, channels, bits, seconds = wav_info(audio)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if (rate, channels, bits) not in ((16000, 1, 16), (24000, 1, 16)):
        raise HTTPException(400, "Send 16 or 24 kHz mono 16-bit WAV")
    if seconds > MAX_VOICE_SECONDS:
        raise HTTPException(413, f"At most {MAX_VOICE_SECONDS} s of speech")
    now = clock.now()
    # Transcribing and planning take ~25 s of blocking network calls: run them in a worker
    # thread, so the one container keeps answering polls and the website meanwhile.
    try:
        transcript = await run_in_threadpool(voice.transcriber.transcribe, audio)
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    registry.heard(device_id, transcript, now)
    reply = {"transcript": transcript, "answer": None, "verified": False, "proposed": False}
    if len(transcript) < 3:
        registry.queue_say(
            device_id, "Sorry, I didn't quite catch that. Hold the button and try again."
        )
        return reply
    if STOP_WORDS.search(transcript) and len(transcript.split()) <= 6:
        # "Stop charging" is a command, not a question for the planner.
        if any(s.device_id == device_id and s.active for s in registry.all()):
            registry.event(device_id, {"type": "stop"}, now)
        else:
            registry.queue_say(
                device_id, "Nothing is charging right now, so there's nothing to stop."
            )
        reply["stopped"] = True
        return reply
    if not planner.available():
        registry.queue_say(device_id, "Sorry, no planning model is set up on the server.")
        return reply
    run = await run_in_threadpool(
        run_agent,
        transcript,
        data,
        now,
        planner,
        mode=clock.mode,
        carbon=lambda: carbon.get(now),
    )
    log_.add(run)
    reply.update(answer=run.answer, verified=run.verified, run_id=run.id)
    try:
        registry.propose(device_id, run, now)  # read out with "hold to approve" on next poll
        reply["proposed"] = True
    except ValueError:
        registry.queue_say(device_id, run.answer or "Sorry, I couldn't work that out.")
    return reply


@router.get("/device/{device_id}/speech/{clip_id}")
def device_speech(
    device_id: AuthorisedDevice, clip_id: int, voice: VoiceDep, format: str = "wav"
) -> Response:
    """A speech clip for the device to play: WAV, or with ?format=adpcm the compact stream the
    board plays as it downloads (16 kHz IMA ADPCM, about 8 KB per second of speech)."""
    clip = voice.clip(clip_id)
    if clip is None:
        raise HTTPException(404, "No such clip (the server keeps the newest few)")
    if format == "adpcm":
        return Response(voice.device_audio(clip_id), media_type="application/octet-stream")
    return Response(clip.wav, media_type="audio/wav")


@router.post(
    "/device/{device_id}/propose",
    response_model=DeviceView,
    dependencies=[Depends(require_app_token)],
)
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


@router.post(
    "/device/{device_id}/test",
    response_model=DeviceView,
    dependencies=[Depends(require_app_token)],
)
def relay_test(
    device_id: DeviceId,
    request: RelayTest,
    clock: ClockDep,
    data: PlanningDataDep,
    registry: RegistryDep,
) -> dict:
    """Hardware check: switch the device's relay on (or off) for a few real seconds,
    whatever its plan says. The device picks it up on its next poll."""
    state = registry.relay_test(
        device_id, request.relay, request.seconds, pd.Timestamp.now(tz="UTC")
    )
    return view(state, clock.now(), data)


@router.get("/firmware", response_model=FirmwareOut | None)
def firmware(image: FirmwareDep) -> dict | None:
    """The firmware image this server can install over the air (null if it has none)."""
    if image is None:
        return None
    return {"version": image.version, "size": image.size, "md5": image.md5}


@router.post(
    "/device/{device_id}/update",
    response_model=DeviceView,
    dependencies=[Depends(require_app_token)],
)
def request_update(
    device_id: DeviceId,
    request: UpdateRequest,
    clock: ClockDep,
    data: PlanningDataDep,
    registry: RegistryDep,
    image: FirmwareDep,
) -> dict:
    """Offer the server's firmware image to a device. It installs it on its next poll and
    restarts; if the new firmware can't reach the server, the board rolls back by itself."""
    now = clock.now()
    if request.cancel:
        return view(registry.request_update(device_id, None, now), now, data, image)
    if image is None:
        raise HTTPException(404, "This server has no firmware image")
    if request.version and request.version != image.version:
        raise HTTPException(409, f"This server has firmware {image.version}, not {request.version}")
    return view(registry.request_update(device_id, image.version, now), now, data, image)


@router.get("/device/{device_id}/firmware", response_class=FileResponse)
def firmware_image(device_id: AuthorisedDevice, image: FirmwareDep) -> FileResponse:
    """The app image itself, for the device (needs its token when DEVICE_TOKENS is set)."""
    if image is None:
        raise HTTPException(404, "This server has no firmware image")
    return FileResponse(
        image.path,
        media_type="application/octet-stream",
        headers={"X-Firmware-Version": image.version, "X-Firmware-MD5": image.md5},
    )


@router.get("/device/{device_id}/logs", response_model=list[dict])
def device_logs(device_id: DeviceId, registry: RegistryDep) -> list[dict]:
    """Everything the device has logged lately, oldest first."""
    state = next((s for s in registry.all() if s.device_id == device_id), None)
    if state is None:
        raise HTTPException(404, "No such device")
    return list(state.logs)


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
            "update_to": s.update_to,
            "logs": list(s.logs)[-SUMMARY_LOG_LINES:],
        }
        for s in registry.all()
    ]
