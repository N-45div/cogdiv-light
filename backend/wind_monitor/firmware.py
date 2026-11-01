"""Firmware the server can hand to devices over the air.

The PlatformIO build writes `firmware/dist/`: the app image and `manifest.json` with its version,
size and MD5. A device is offered the image only after someone asks for it
(POST /api/device/{id}/update); it downloads the image, checks the MD5 while writing it, and
restarts into it. If the new firmware can't reach the server, the board rolls back by itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import BACKEND_DIR

DEFAULT_FIRMWARE_DIR = BACKEND_DIR.parent / "firmware" / "dist"


@dataclass(frozen=True)
class FirmwareImage:
    version: str
    path: Path
    size: int
    md5: str


def firmware_dir() -> Path:
    return Path(os.environ.get("FIRMWARE_DIR", DEFAULT_FIRMWARE_DIR))


def load_image(directory: Path) -> FirmwareImage | None:
    """The image described by `directory/manifest.json`, or None if it's missing or doesn't
    match its manifest (a device must never be offered a broken image)."""
    manifest = directory / "manifest.json"
    try:
        stamp = manifest.stat().st_mtime_ns
    except OSError:
        return None
    return _load(manifest, stamp)


@lru_cache(maxsize=4)
def _load(manifest: Path, _stamp: int) -> FirmwareImage | None:
    try:
        info = json.loads(manifest.read_text())
        path = manifest.parent / Path(info["app"]).name
        data = path.read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    md5 = hashlib.md5(data).hexdigest()
    if len(data) != info.get("size") or md5 != info.get("md5"):
        return None
    return FirmwareImage(str(info.get("version", "dev")), path, len(data), md5)


def get_firmware() -> FirmwareImage | None:
    return load_image(firmware_dir())
