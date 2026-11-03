"""Deploy the API (and the built website, if present) on Modal.

    modal deploy deploy/modal_app.py            # from the repo root

Runs as exactly ONE always-on container: the device registry, the agent's run log and the
replay clock live in memory, so every request must reach the same process. The app's keys come
from the Modal secret "wind-buddy" (MODEL_API_KEY, BASETEN_API_KEY, DEVICE_TOKENS, APP_TOKEN,
CLOCK_MODE), which the GitHub deploy workflow writes from the repository's secrets.
"""

from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
SITE = ROOT / "frontend" / "dist"  # built by `npm run build`; optional
FIRMWARE = ROOT / "firmware" / "dist"  # committed; offered to boards over the air

# The device's speaking voice: Piper (open source text-to-speech; Meta's API has none), with a
# British voice from rhasspy/piper-voices. Speech-to-text is Meta's Muse Voice Transcribe.
PIPER_VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium"
VOICE = "en_GB-alan-medium.onnx"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements(str(BACKEND / "requirements.txt"))
    .pip_install("piper-tts==1.3.0")
    .run_commands(
        "mkdir -p /app/voices",
        *(
            f"python -c \"import urllib.request as u; "
            f"u.urlretrieve('{PIPER_VOICE_URL}/{name}', '/app/voices/{name}')\""
            for name in (VOICE, f"{VOICE}.json")
        ),
    )
    .env(
        {
            "SNAPSHOT_DIR": "/app/data",
            "STATIC_DIR": "/app/static",
            "FIRMWARE_DIR": "/app/firmware",
            "PIPER_VOICE": f"/app/voices/{VOICE}",
            "PYTHONPATH": "/app",
        }
    )
    .add_local_dir(BACKEND / "wind_monitor", "/app/wind_monitor")
    .add_local_dir(BACKEND / "data", "/app/data")
    .add_local_dir(FIRMWARE, "/app/firmware")
)
if SITE.is_dir():
    image = image.add_local_dir(SITE, "/app/static")

app = modal.App("wind-buddy", image=image)


@app.function(
    secrets=[modal.Secret.from_name("wind-buddy")],
    min_containers=1,  # always warm: the device polls every few seconds
    max_containers=1,  # one process, one shared state
    timeout=180,  # an agent answer takes ~5 s; leave room for slow models
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    from wind_monitor.main import create_app

    return create_app()
