"""The device's voice: what the person says, and what the device says back.

- Listening: the board records a push-to-talk WAV; Meta's Muse Voice Transcribe
  (POST https://api.meta.ai/v1/asr/transcribe, 16 kHz mono 16-bit WAV) turns it into text, which
  goes to the same agent as the web page (tools, fact check, proposal to the device).
- Speaking: Meta's API has no text-to-speech, so the server synthesizes speech with Piper (open
  source, runs on our own CPU, British voice). Clips are kept in memory; the device is told the
  newest clip's id on its next poll and downloads it.

The words are written by code from the planner's results, or are the agent's fact-checked
answer, so the device never says a number the checker hasn't seen.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import wave
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache

import httpx

ASR_URL = "https://api.meta.ai/v1/asr/transcribe"
ASR_MODEL = "muse-voice-transcribe-1.0"
MAX_CLIPS = 20
# Plan-related words the transcriber should expect (vocabulary biasing).
KEYWORDS = ["wind", "charge", "charging", "kilowatt hours", "kWh", "EV", "car", "heat pump"]


class VoiceUnavailable(RuntimeError):
    """No transcription key or no speech voice on this server."""


class Transcriber:
    """Meta Muse Voice Transcribe over HTTPS."""

    def __init__(self, key: str | None, client: httpx.Client | None = None) -> None:
        self.key = key
        self.client = client or httpx.Client(timeout=30)

    def available(self) -> bool:
        return bool(self.key)

    def transcribe(self, wav_bytes: bytes) -> str:
        if not self.key:
            raise VoiceUnavailable("Set MODEL_API_KEY to transcribe speech")
        settings = json.dumps(
            {
                "model": ASR_MODEL,
                "audioEncoding": "WAV",
                "mode": "PUSH_TO_TALK",
                "languageBias": ["English"],
                "keywords": KEYWORDS,
            }
        )
        response = self.client.post(
            ASR_URL,
            headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"},
            files={
                "request": (None, settings, "application/json"),
                "audio": ("speech.wav", wav_bytes, "audio/wav"),
            },
        )
        if response.status_code != 200:
            raise VoiceUnavailable(f"Transcription failed: HTTP {response.status_code}")
        return (response.json().get("transcript") or "").strip()


class PiperSynthesizer:
    """Piper text-to-speech with one voice model (PIPER_VOICE: path to the .onnx file)."""

    def __init__(self, model_path: str | None) -> None:
        self.model_path = model_path
        self._voice = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return bool(self.model_path) and os.path.exists(self.model_path)

    def synthesize(self, text: str) -> bytes:
        if not self.available():
            raise VoiceUnavailable("Set PIPER_VOICE to a Piper voice model to speak")
        with self._lock:  # one voice, one synthesis at a time
            if self._voice is None:
                from piper import PiperVoice  # optional dependency, only on the server image

                self._voice = PiperVoice.load(self.model_path)
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                if hasattr(self._voice, "synthesize_wav"):  # piper-tts 1.3+
                    self._voice.synthesize_wav(text, wav)
                else:  # piper-tts 1.2
                    self._voice.synthesize(text, wav)
            return buffer.getvalue()


def wav_info(data: bytes) -> tuple[int, int, int, float]:
    """(sample rate, channels, bits, seconds) of a PCM WAV; raises ValueError if it isn't one."""
    try:
        with wave.open(io.BytesIO(data)) as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            return rate, channels, width * 8, wav.getnframes() / rate
    except (wave.Error, EOFError, ZeroDivisionError) as exc:
        raise ValueError("Not a PCM WAV file") from exc


def _clock(match: re.Match) -> str:
    """24-hour time as people say it: 19:00 -> 7 pm, 00:00 -> midnight, 07:30 -> 7:30 am."""
    hour, minute = int(match.group(1)), int(match.group(2))
    if minute == 0 and hour in (0, 12):
        return "midnight" if hour == 0 else "noon"
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return f"{hour12} {suffix}" if minute == 0 else f"{hour12}:{minute:02d} {suffix}"


def _megawatts(match: re.Match) -> str:
    gw = float(match.group(1)) / 1000
    return f"about {gw:.1f}".rstrip("0").rstrip(".") + " gigawatts"


def _percent(match: re.Match) -> str:
    return f"about {round(float(match.group(1)))} percent"


_SPEECH = [
    (r"(\d{1,2}:\d{2})\s*(?:[-–]|to)\s*(\d{1,2}:\d{2})", r"from \1 to \2"),
    (r"\bfrom from\b", "from"),
    (r"\b([01]?\d|2[0-3]):([0-5]\d)\b", _clock),
    (r"(\d+(?:\.\d+)?)\s*MW\b", _megawatts),
    (r"(\d+\.\d+)\s*%", _percent),
    (r"(\d)\s*kWh\b", r"\1 kilowatt hours"),
    (r"(\d)\s*kW\b", r"\1 kilowatts"),
    (r"(\d)\s*GW\b", r"\1 gigawatts"),
    (r"(\d)\s*%", r"\1 percent"),
    (r"\bvs\.?\b", "compared with"),
    (r"\bUK\b", "U K"),
]


def spoken(text: str) -> str:
    """Text as it should be read aloud: times and units as people say them, no markdown."""
    text = re.sub(r"[*_#`>]+", "", text)
    for pattern, replacement in _SPEECH:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Clip:
    id: int
    text: str
    wav: bytes


class Voice:
    """Transcription in, speech clips out (kept in memory, newest MAX_CLIPS)."""

    def __init__(self, transcriber: Transcriber, synthesizer: PiperSynthesizer) -> None:
        self.transcriber = transcriber
        self.synthesizer = synthesizer
        self._clips: OrderedDict[int, Clip] = OrderedDict()
        self._next_id = 1
        self._lock = threading.Lock()

    def can_speak(self) -> bool:
        return self.synthesizer.available()

    def speak(self, text: str) -> Clip:
        wav_bytes = self.synthesizer.synthesize(spoken(text))
        with self._lock:
            clip = Clip(self._next_id, text, wav_bytes)
            self._next_id += 1
            self._clips[clip.id] = clip
            while len(self._clips) > MAX_CLIPS:
                self._clips.popitem(last=False)
            return clip

    def clip(self, clip_id: int) -> Clip | None:
        with self._lock:
            return self._clips.get(clip_id)


@lru_cache(maxsize=1)
def get_voice() -> Voice:
    return Voice(
        Transcriber(os.environ.get("MODEL_API_KEY")),
        PiperSynthesizer(os.environ.get("PIPER_VOICE")),
    )
