"""Keep the server's memory across restarts: the boards and their plans, the agent's run log and
the watcher's findings.

Production is one container that restarts on every deploy (and now and then on its own).
Without this, a restart forgets every board, the plan it is running and the record of what the
agent decided and why. With STATE_FILE set (a Modal Volume in production), a snapshot is
written every 30 seconds when something has changed, and at shutdown, and read back at start-up.

Pickle is fine here: only this server writes the file. A snapshot from older code still loads:
fields added since then get their defaults. If it can't be read at all, the server starts
empty, as it did before.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import pickle
from pathlib import Path

from .agent.runner import AgentRun
from .agent.runs import RunLog, get_run_log
from .device import DeviceRegistry, DeviceState, Proposal, get_registry
from .watcher import Finding, Watcher, get_watcher

log = logging.getLogger(__name__)

FORMAT = 1
SAVE_EVERY_SECONDS = 30


def _complete(obj, cls) -> bool:
    """Give an unpickled dataclass any fields added since it was saved (default_factory ones
    are not class attributes, so they would be missing). False if it isn't usable."""
    if not isinstance(obj, cls):
        return False
    for f in dataclasses.fields(cls):
        if f.name in obj.__dict__ or f.default is not dataclasses.MISSING:
            continue
        if f.default_factory is dataclasses.MISSING:
            return False
        setattr(obj, f.name, f.default_factory())
    return True


class Persistence:
    def __init__(
        self,
        path: Path,
        registry: DeviceRegistry | None = None,
        runs: RunLog | None = None,
        watcher: Watcher | None = None,
    ) -> None:
        self.path = path
        self.registry = registry or get_registry()
        self.runs = runs or get_run_log()
        self.watcher = watcher or get_watcher()
        self._saved: tuple | None = None

    def _signature(self) -> tuple:
        """Changes when anything worth keeping changes (not on every poll's last_seen)."""
        devices = tuple(sorted((s.device_id, s.version, len(s.logs)) for s in self.registry.all()))
        newest_run = self.runs.recent(1)
        findings = self.watcher.recent(1)
        return (
            devices,
            newest_run[0].id if newest_run else None,
            findings[0]["id"] if findings else None,
        )

    def snapshot(self) -> bytes:
        with self.registry._lock:
            devices = pickle.dumps(self.registry._devices)
        with self.runs._lock:
            runs = pickle.dumps(list(self.runs._runs))
        with self.watcher._lock:
            findings = pickle.dumps((list(self.watcher.findings), self.watcher.last))
        return pickle.dumps(
            {"format": FORMAT, "devices": devices, "runs": runs, "findings": findings}
        )

    def save(self, force: bool = False) -> bool:
        """Write the snapshot if something changed. True if written."""
        signature = self._signature()
        if not force and signature == self._saved:
            return False
        data = self.snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(data)
        os.replace(temp, self.path)  # never leave a half-written snapshot
        self._saved = signature
        return True

    def load(self) -> bool:
        """Restore the last snapshot, if there is a usable one. True if restored."""
        if not self.path.exists():
            return False
        try:
            outer = pickle.loads(self.path.read_bytes())
            if outer.get("format") != FORMAT:
                raise ValueError(f"snapshot format {outer.get('format')}, expected {FORMAT}")
            devices = pickle.loads(outer["devices"])
            runs = pickle.loads(outer["runs"])
            findings, last = pickle.loads(outer["findings"])
        except Exception as exc:  # an unreadable snapshot must not stop the server
            log.warning("Could not restore %s, starting empty: %s", self.path, exc)
            return False

        restored = {}
        for device_id, state in devices.items():
            if not _complete(state, DeviceState):
                continue
            for name in ("proposal", "active"):
                p = getattr(state, name)
                if p is not None and not _complete(p, Proposal):
                    setattr(state, name, None)
            state.say = None  # its clip is gone; the board has heard it already
            restored[device_id] = state
        with self.registry._lock:
            self.registry._devices.update(restored)
        good_runs = [r for r in runs if _complete(r, AgentRun)]
        with self.runs._lock:
            self.runs._runs.extend(good_runs)
        with self.watcher._lock:
            self.watcher.findings.extend(f for f in findings if _complete(f, Finding))
            self.watcher.last = last
        self._saved = self._signature()
        log.info(
            "Restored %d devices, %d agent runs and %d watcher findings from %s",
            len(restored),
            len(good_runs),
            len(self.watcher.findings),
            self.path,
        )
        return True

    async def loop(self, every: float = SAVE_EVERY_SECONDS) -> None:
        """Runs in the app's background: save every `every` seconds when something changed."""
        while True:
            await asyncio.sleep(every)
            try:
                await asyncio.to_thread(self.save)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Saving the server state failed; trying again shortly")
