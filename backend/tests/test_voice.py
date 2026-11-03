"""The device's voice: push-to-talk in, spoken plan and announcements out."""

import io
import wave
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_agent_api import Planner
from test_agent_runner import GOOD, PLAN_CALL, say
from wind_fixtures import NOW, synthetic_wind

from wind_monitor import agent_api, planning_api
from wind_monitor.agent.runs import RunLog, get_run_log
from wind_monitor.clock import ReplayClock, get_clock
from wind_monitor.config import Settings
from wind_monitor.device import DeviceRegistry, get_registry
from wind_monitor.firmware import get_firmware
from wind_monitor.main import create_app
from wind_monitor.voice import Voice, get_voice, spoken, wav_info

QUESTION = "I need 14 kWh at 7 kW, when should I charge?"


def make_wav(seconds=0.5, rate=16000, channels=1, width=2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(b"\0" * int(seconds * rate) * channels * width)
    return buffer.getvalue()


class FakeTranscriber:
    def __init__(self, transcript):
        self.transcript = transcript
        self.heard = []

    def available(self):
        return True

    def transcribe(self, wav_bytes):
        self.heard.append(len(wav_bytes))
        return self.transcript


class FakeSynthesizer:
    def __init__(self):
        self.spoken = []

    def available(self):
        return True

    def synthesize(self, text):
        self.spoken.append(text)
        return make_wav(0.1, rate=22050)


@pytest.fixture
def world():
    clock = ReplayClock(NOW)
    data, log, registry = synthetic_wind(), RunLog(), DeviceRegistry()
    synth = FakeSynthesizer()
    voice = Voice(FakeTranscriber(QUESTION), synth)
    planner = Planner(PLAN_CALL, say(GOOD), say("I can't plan that."))
    app = create_app(replace(Settings.from_env(), static_dir=None))
    app.dependency_overrides[planning_api.planning_data] = lambda: data
    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[agent_api.get_planner] = lambda: planner
    app.dependency_overrides[get_run_log] = lambda: log
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_firmware] = lambda: None
    app.dependency_overrides[get_voice] = lambda: voice
    return TestClient(app), clock, voice, synth


def _heard_last(client):
    state = client.get("/api/device/desk-1/state").json()
    return state["say"]


def test_spoken_text_reads_units_and_times():
    text = "**Charge 21:30-00:30**: 14 kWh at 7 kW, wind 7.5 GW (+12% vs plug-in)."
    assert spoken(text) == (
        "Charge 21:30 to 00:30: 14 kilowatt hours at 7 kilowatts, wind 7.5 gigawatts "
        "(+12 percent compared with plug-in)."
    )


def test_a_spoken_night(world):
    client, clock, voice, synth = world
    assert _heard_last(client) is None  # nothing to say yet

    run = client.post("/api/agent/ask", json={"question": QUESTION, "device_id": "desk-1"}).json()
    say_ = _heard_last(client)
    assert synth.spoken[-1].startswith("New charging plan.")
    assert synth.spoken[-1].endswith("Hold the button to approve, or tap to skip.")
    assert say_["path"] == f"/api/device/desk-1/speech/{say_['id']}"
    clip = client.get(say_["path"])
    assert clip.status_code == 200 and wav_info(clip.content)[0] == 22050

    # The same news is said once: the next poll keeps the same clip.
    assert _heard_last(client)["id"] == say_["id"] and len(synth.spoken) == 1

    approve = {"type": "approve", "proposal_id": run["id"]}
    after = client.post("/api/device/desk-1/event", json=approve).json()
    assert synth.spoken[-1] == "Approved. Charging starts at 02:00."
    assert after["say"]["id"] > say_["id"]

    clock.set(at=NOW.replace(day=9, hour=2, minute=15))
    _heard_last(client)
    assert synth.spoken[-1].startswith("Charging now. Wind is ")
    assert synth.spoken[-1].endswith("gigawatts.")

    clock.set(at=NOW.replace(day=9, hour=4))
    _heard_last(client)
    assert synth.spoken[-1] == "Charge done: 14 kilowatt hours, all in windy hours."

    events = [e["event"] for e in client.get("/api/devices").json()[0]["events"]]
    assert any(e.startswith("said: Charge done") for e in events)


def test_a_relay_test_is_not_announced(world):
    client, _, _, synth = world
    client.post("/api/device/desk-1/test", json={"relay": True, "seconds": 5})
    _heard_last(client)
    assert synth.spoken == []


def test_push_to_talk_becomes_a_spoken_proposal(world):
    client, _, voice, synth = world
    reply = client.post(
        "/api/device/desk-1/voice", content=make_wav(), headers={"Content-Type": "audio/wav"}
    ).json()
    assert reply["transcript"] == QUESTION and reply["proposed"] is True
    assert reply["verified"] is True and reply["run_id"]
    state = client.get("/api/device/desk-1/state").json()
    assert state["mode"] == "proposal" and synth.spoken[-1].startswith("New charging plan.")
    events = [e["event"] for e in client.get("/api/devices").json()[0]["events"]]
    assert f"heard: {QUESTION}" in events


def test_unclear_speech_gets_a_spoken_retry(world):
    client, _, voice, synth = world
    voice.transcriber.transcript = ""
    reply = client.post("/api/device/desk-1/voice", content=make_wav()).json()
    assert reply["transcript"] == "" and reply["proposed"] is False
    _heard_last(client)
    assert synth.spoken[-1].startswith("Sorry, I didn't catch that.")


@pytest.mark.parametrize(
    ("audio", "status"),
    [
        (b"not audio", 400),
        (make_wav(rate=44100), 400),
        (make_wav(channels=2), 400),
        (make_wav(seconds=16), 413),
    ],
)
def test_bad_audio_is_rejected(world, audio, status):
    client = world[0]
    assert client.post("/api/device/desk-1/voice", content=audio).status_code == status


def test_speech_needs_the_device_token(world, monkeypatch):
    client = world[0]
    client.post("/api/device/desk-1/voice", content=make_wav())
    monkeypatch.setenv("DEVICE_TOKENS", "desk-1:s3cret")
    assert client.post("/api/device/desk-1/voice", content=make_wav()).status_code == 401
    assert client.get("/api/device/desk-1/speech/1").status_code == 401
    ok = client.get("/api/device/desk-1/speech/1", headers={"X-Device-Token": "s3cret"})
    assert ok.status_code in (200, 404)
