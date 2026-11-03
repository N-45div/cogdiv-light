"""The grid brief: a live sentence or two on where and when GB power is cleanest, for the site.

Every half-hour on the planning clock, code works out the facts from NESO's carbon forecast: the
GB figure now, the cleanest and dirtiest regions now, the cleanest two hours across GB in the
next day, and what that means for an example compute job (8 GPUs for 4 hours, planned like any
other). The watcher's open-weight model (Muse Spark Open on Baseten, or Muse Glimmer on our own
Modal GPU) writes them up. Its text is fact-checked like every agent answer; if it fails the
check, or no model is set up, a sentence written by code is used instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import asdict, dataclass, field

import pandas as pd

from .agent import factcheck
from .agent.llm import ChatClient, ModelUnavailableError
from .agent.tools import Toolbox, uk_label
from .carbon import GB_REGION, CarbonFeed, CarbonUnavailableError
from .store import WindData

log = logging.getLogger(__name__)

BRIEF_PROMPT = """\
You write the live note on a website about when and where electricity in Great Britain is \
cleanest. You get the facts as JSON. Write one or two plain sentences, under 230 characters in \
all: where the grid is cleanest now, and the best time (and place) for flexible work like the \
example compute job. Quote figures exactly as given (you may round); never calculate new ones. \
Times are UK local time, as HH:MM. No hype, no emoji, no markdown.
"""
EXAMPLE_JOB = {"gpus": 8, "hours": 4}
EMPTY_WIND = WindData(
    pd.DataFrame(columns=["start_time", "generation_mw"]),
    pd.DataFrame(columns=["publish_time", "start_time", "generation_mw"]),
    "none",
    pd.Timestamp(0, tz="UTC").to_pydatetime(),
)


@dataclass
class Brief:
    at: str  # the clock's half-hour, UTC ISO
    text: str
    by: str  # the model that wrote it, or "code"
    check: str  # the fact check's summary, or why the model's text wasn't used
    facts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def brief_facts(now: pd.Timestamp, carbon) -> dict:
    """The facts, from the same tools the agent uses (so the fact check knows every figure)."""
    tools = Toolbox(EMPTY_WIND, now, carbon=lambda: carbon)
    outlook = tools.get_carbon_outlook(hours=24)
    job = tools.plan_compute_job(**EXAMPLE_JOB)
    ranked = outlook["regions_now_cleanest_first"]
    facts = {
        "now": outlook["now"],
        "gb_now_g_per_kwh": outlook["gb_now_g_per_kwh"],
        "cleanest_region_now": ranked[0] if ranked else None,
        "dirtiest_region_now": ranked[-1] if ranked else None,
    }
    gb = carbon.region(GB_REGION).set_index("start_time")["intensity_g"]
    ahead = gb[(gb.index >= now.floor("30min")) & (gb.index < now + pd.Timedelta(hours=24))]
    two_hours = ahead.rolling(4).mean().dropna()  # each value: the 2 hours ending at that slot
    if len(two_hours):
        last = two_hours.idxmin()
        facts["gb_cleanest_two_hours"] = {
            "from": uk_label(last - pd.Timedelta(minutes=90)),
            "to": uk_label(last + pd.Timedelta(minutes=30)),
            "average_g_per_kwh": int(round(two_hours.min())),
        }
    if job.get("feasible"):
        facts["example_compute_job"] = {
            "gpus": job["gpus"],
            "hours": job["run_hours"],
            "best_region": job["best"]["region"],
            "run_windows": job["best"]["run_windows"],
            "pct_less_co2_than_starting_now_in_london": job["pct_less_co2_than_baseline"],
        }
    return facts


def plain_brief(facts: dict) -> str:
    """The code-written version: always correct."""
    clean, dirty = facts.get("cleanest_region_now"), facts.get("dirtiest_region_now")
    parts = []
    if clean and dirty:
        parts.append(
            f"Right now the grid is cleanest in {clean['region']} ({clean['g_per_kwh']} g/kWh) "
            f"and dirtiest in {dirty['region']} ({dirty['g_per_kwh']} g/kWh)."
        )
    job = facts.get("example_compute_job")
    if job and job["run_windows"]:
        first = job["run_windows"][0]
        parts.append(
            f"An {job['gpus']}-GPU job is best run in {job['best_region']} from "
            f"{first['from'][-5:]}: {job['pct_less_co2_than_starting_now_in_london']:g}% less CO2 "
            "than starting now in London."
        )
    return " ".join(parts) or "No carbon forecast just now."


def write_brief(facts: dict, client: ChatClient | None) -> tuple[str | None, str, str]:
    """The model's text if it passes the fact check: (text, check summary, model)."""
    if client is None or not client.available():
        return None, "no model set up", ""
    evidence = [{"name": "grid_facts", "result": facts}]
    messages = [
        {"role": "system", "content": BRIEF_PROMPT},
        {"role": "user", "content": json.dumps(facts)},
    ]
    try:
        for _ in range(2):
            result = client.chat(messages)
            text = (result.message.get("content") or "").strip()
            report = factcheck.check(text, evidence)
            if text and report.passed and len(text) <= 320:
                return text, report.summary, result.model
            bad = ", ".join(f"'{c.text}'" for c in report.unsupported) or "length"
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"These don't match the facts: {bad}. Rewrite."},
            ]
    except ModelUnavailableError as exc:
        return None, f"model unavailable: {exc}", ""
    return None, "the model's text failed the fact check", ""


def make_brief(now: pd.Timestamp, carbon, client: ChatClient | None) -> Brief:
    facts = brief_facts(now, carbon)
    text, check, model = write_brief(facts, client)
    at = now.floor("30min").isoformat()
    if text:
        return Brief(at, text, model, check, facts)
    return Brief(at, plain_brief(facts), "code", check, facts)


class BriefDesk:
    """Holds the newest brief; the background loop writes a new one each half-hour."""

    def __init__(self) -> None:
        self.latest: Brief | None = None
        self._lock = threading.Lock()

    def due(self, now: pd.Timestamp) -> bool:
        return self.latest is None or self.latest.at != now.floor("30min").isoformat()

    def refresh(self, now: pd.Timestamp, feed: CarbonFeed, client: ChatClient | None) -> Brief:
        with self._lock:
            if self.due(now):
                self.latest = make_brief(now, feed.get(now), client)
            return self.latest

    def quick(self, now: pd.Timestamp, feed: CarbonFeed) -> Brief:
        """The newest brief, or (before the first one is written) the code version now."""
        latest = self.latest
        if latest is not None:
            return latest
        return make_brief(now, feed.get(now), None)


_desk = BriefDesk()


def get_brief_desk() -> BriefDesk:
    return _desk


async def brief_loop(feed_getter, clock_getter, client_getter, every: float = 60) -> None:
    """Runs in the app's background: a fresh brief each half-hour of the planning clock."""
    desk = get_brief_desk()
    while True:
        try:
            now = clock_getter().now()
            if desk.due(now):
                await asyncio.to_thread(desk.refresh, now, feed_getter(), client_getter())
        except asyncio.CancelledError:
            raise
        except CarbonUnavailableError as exc:
            log.warning("No grid brief this time: %s", exc)
        except Exception:
            log.exception("Writing the grid brief failed; trying again shortly")
        await asyncio.sleep(every)
