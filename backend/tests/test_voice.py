"""The device's voice: push-to-talk in, spoken plan and announcements out."""

import io
import math
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
from wind_monitor.voice import (
    ADPCM_MAGIC,
    Voice,
    adpcm_decode,
    adpcm_encode,
    compact,
    get_voice,
    pcm16,
    spoken,
    wav_info,
)

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


def test_spoken_text_sounds_like_a_person():
    text = "**Charge 21:30-00:00**: 14 kWh at 7 kW, wind 7.5 GW (+12% vs plug-in)."
    assert spoken(text) == (
        "Charge from 9:30 pm to midnight: 14 kilowatt hours at 7 kilowatts, wind 7.5 gigawatts "
        "(+12 percent compared with plug-in)."
    )
    agent = "Charge 19:00 to 22:00. Expected wind is 6968 MW, 12.7% more than at 07:00."
    assert spoken(agent) == (
        "Charge from 7 pm to 10 pm. Expected wind is about 7 gigawatts, "
        "about 13 percent more than at 7 am."
    )
    assert spoken("Start at 12:00 or 00:30.") == "Start at noon or 12:30 am."


def test_a_spoken_night(world):
    client, clock, voice, synth = world
    assert _heard_last(client) is None  # nothing to say yet

    run = client.post("/api/agent/ask", json={"question": QUESTION, "device_id": "desk-1"}).json()
    say_ = _heard_last(client)
    assert synth.spoken[-1].startswith("Here's my plan: charge from 2 am to 4 am, when the wind")
    assert synth.spoken[-1].endswith("Hold the button for yes, or tap for no.")
    assert say_["path"] == f"/api/device/desk-1/speech/{say_['id']}"
    clip = client.get(say_["path"])
    assert clip.status_code == 200 and wav_info(clip.content)[0] == 22050

    # The same news is said once: the next poll keeps the same clip.
    assert _heard_last(client)["id"] == say_["id"] and len(synth.spoken) == 1

    approve = {"type": "approve", "proposal_id": run["id"]}
    after = client.post("/api/device/desk-1/event", json=approve).json()
    assert synth.spoken[-1] == "Great, it's booked. I'll start charging at 2 am."
    assert after["say"]["id"] > say_["id"]

    clock.set(at=NOW.replace(day=9, hour=2, minute=15))
    _heard_last(client)
    assert synth.spoken[-1].startswith("Charging now. The wind across Britain is about ")
    assert synth.spoken[-1].endswith("gigawatts.")

    clock.set(at=NOW.replace(day=9, hour=4))
    _heard_last(client)
    assert synth.spoken[-1] == "All done! 14 kilowatt hours, charged in the windiest hours."

    events = [e["event"] for e in client.get("/api/devices").json()[0]["events"]]
    assert any(e.startswith("said: All done!") for e in events)


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
    assert state["mode"] == "proposal" and synth.spoken[-1].startswith("Here's my plan:")
    events = [e["event"] for e in client.get("/api/devices").json()[0]["events"]]
    assert f"heard: {QUESTION}" in events


def test_unclear_speech_gets_a_spoken_retry(world):
    client, _, voice, synth = world
    voice.transcriber.transcript = ""
    reply = client.post("/api/device/desk-1/voice", content=make_wav()).json()
    assert reply["transcript"] == "" and reply["proposed"] is False
    _heard_last(client)
    assert synth.spoken[-1].startswith("Sorry, I didn't quite catch that.")


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


def test_device_audio_is_compact_and_decodes_back():
    rate = 22050
    t = [i / rate for i in range(rate)]  # one second of a 440 Hz tone
    tone = [int(8000 * math.sin(2 * math.pi * 440 * x)) for x in t]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(v.to_bytes(2, "little", signed=True) for v in tone))
    data = compact(buffer.getvalue())
    assert data[:4] == ADPCM_MAGIC
    assert int.from_bytes(data[4:8], "little") == 16000
    count = int.from_bytes(data[8:12], "little")
    assert count == 16000 and len(data) == 12 + 8000  # 4 bits a sample: 8 KB per second
    original = pcm16(buffer.getvalue()).tolist()
    decoded = adpcm_decode(data[12:], count)
    error = sum((a - b) ** 2 for a, b in zip(original, decoded, strict=True)) / count
    power = sum(a * a for a in original) / count
    assert power / error > 30  # better than 15 dB: clear enough for speech


def test_adpcm_round_trip_follows_the_signal():
    samples = [0, 1000, 4000, 9000, 4000, -3000, -12000, -2000, 0, 500] * 50
    decoded = adpcm_decode(adpcm_encode(samples), len(samples))
    tail = range(len(samples) // 2, len(samples))
    assert sum(abs(decoded[i] - samples[i]) for i in tail) / len(tail) < 2500
    assert max(decoded) > 6000 and min(decoded) < -8000


def test_the_device_downloads_compact_speech(world):
    client = world[0]
    client.post("/api/agent/ask", json={"question": QUESTION, "device_id": "desk-1"})
    say_ = client.get("/api/device/desk-1/state").json()["say"]
    small = client.get(say_["path"] + "?format=adpcm")
    assert small.status_code == 200 and small.content[:4] == ADPCM_MAGIC
    assert len(small.content) < len(client.get(say_["path"]).content) / 3


def test_stop_charging_by_voice(world):
    client, clock, voice, synth = world
    run = client.post("/api/agent/ask", json={"question": QUESTION, "device_id": "desk-1"}).json()
    client.post("/api/device/desk-1/event", json={"type": "approve", "proposal_id": run["id"]})
    voice.transcriber.transcript = "Stop charging please."
    reply = client.post("/api/device/desk-1/voice", content=make_wav()).json()
    assert reply["stopped"] is True
    state = client.get("/api/device/desk-1/state").json()
    assert state["mode"] == "idle" and synth.spoken[-1] == "Okay, I've stopped charging."
    # Nothing left to stop: it says so.
    client.post("/api/device/desk-1/voice", content=make_wav())
    client.get("/api/device/desk-1/state")
    assert synth.spoken[-1].startswith("Nothing is charging right now")


def test_speech_needs_the_device_token(world, monkeypatch):
    client = world[0]
    client.post("/api/device/desk-1/voice", content=make_wav())
    monkeypatch.setenv("DEVICE_TOKENS", "desk-1:s3cret")
    assert client.post("/api/device/desk-1/voice", content=make_wav()).status_code == 401
    assert client.get("/api/device/desk-1/speech/1").status_code == 401
    ok = client.get("/api/device/desk-1/speech/1", headers={"X-Device-Token": "s3cret"})
    assert ok.status_code in (200, 404)
