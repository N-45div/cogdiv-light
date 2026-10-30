"""A log of recent agent runs, newest first: the record of who decided what, and why."""

from __future__ import annotations

import threading
from collections import deque

from .runner import AgentRun


class RunLog:
    def __init__(self, size: int = 200) -> None:
        self._runs: deque[AgentRun] = deque(maxlen=size)
        self._lock = threading.Lock()

    def add(self, run: AgentRun) -> None:
        with self._lock:
            self._runs.appendleft(run)

    def get(self, run_id: str) -> AgentRun | None:
        with self._lock:
            return next((r for r in self._runs if r.id == run_id), None)

    def recent(self, limit: int = 20) -> list[AgentRun]:
        with self._lock:
            return list(self._runs)[:limit]


_log = RunLog()


def get_run_log() -> RunLog:
    return _log
