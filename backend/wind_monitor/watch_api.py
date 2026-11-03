"""HTTP routes and the background loop for the watcher (see watcher.py)."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool

from .agent.llm import ChatClient, client_for
from .carbon import CarbonFeed, CarbonUnavailableError, get_carbon_feed
from .clock import get_clock
from .device import DeviceRegistry, get_registry
from .planning_api import ClockDep, PlanningDataDep, planning_data, require_app_token
from .schemas import WatchFindingOut
from .watcher import Watcher, get_watcher, is_compute

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# How often the background loop looks at the planning clock (real seconds). The watcher itself
# runs every 30 minutes of planning-clock time, so in a fast replay it fires often.
LOOP_SECONDS = 5


@lru_cache(maxsize=1)
def get_watch_client() -> ChatClient:
    """The watcher's model: Muse Glimmer on Modal if configured, else Muse Spark Open."""
    return client_for("watcher")


WatcherDep = Annotated[Watcher, Depends(get_watcher)]
RegistryDep = Annotated[DeviceRegistry, Depends(get_registry)]
WatchClientDep = Annotated[ChatClient, Depends(get_watch_client)]
CarbonFeedDep = Annotated[CarbonFeed, Depends(get_carbon_feed)]


def carbon_now(feed: CarbonFeed, now):
    """The carbon forecast for compute jobs, or None if NESO isn't answering (they are skipped)."""
    try:
        return feed.get(now)
    except CarbonUnavailableError as exc:
        log.warning("Watcher: no carbon forecast, compute jobs not checked: %s", exc)
        return None


@router.post(
    "/watch/run", response_model=list[WatchFindingOut], dependencies=[Depends(require_app_token)]
)
def run_watch(
    watcher: WatcherDep,
    registry: RegistryDep,
    data: PlanningDataDep,
    clock: ClockDep,
    client: WatchClientDep,
    feed: CarbonFeedDep,
) -> list[dict]:
    """Re-check every approved plan now (normally this happens every 30 minutes)."""
    now = clock.now()
    carbon = carbon_now(feed, now) if any(is_compute(s) for s in registry.all()) else None
    return [f.as_dict() for f in watcher.run(now, registry, data, client, carbon)]


@router.get("/watch", response_model=list[WatchFindingOut])
def recent_findings(
    watcher: WatcherDep, limit: Annotated[int, Query(ge=1, le=200)] = 20
) -> list[dict]:
    return watcher.recent(limit)


async def watch_loop() -> None:
    """Runs in the app's background: check active plans every 30 planning-clock minutes."""
    watcher, registry = get_watcher(), get_registry()
    while True:
        await asyncio.sleep(LOOP_SECONDS)
        try:
            clock = get_clock()
            now = clock.now()
            if not registry.with_active_plans() or not watcher.due(now):
                continue
            data = await run_in_threadpool(planning_data, clock)
            carbon = None
            if any(is_compute(s) for s in registry.with_active_plans()):
                carbon = await run_in_threadpool(carbon_now, get_carbon_feed(), now)
            await run_in_threadpool(watcher.run, now, registry, data, get_watch_client(), carbon)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Watcher loop failed; trying again shortly")
