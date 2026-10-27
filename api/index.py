"""Vercel entry point: exposes the FastAPI app as one serverless function.

vercel.json rewrites every /api/* request here; the React site itself is served
by Vercel's CDN from frontend/dist.
"""

import sys
from pathlib import Path

# The app lives in backend/; make it importable from this file's location.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from wind_monitor.main import app  # noqa: E402, F401
