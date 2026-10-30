"""Check every figure in the agent's answer against what its tools returned.

A language model can misquote, round badly or invent numbers. Before an answer is shown, or a
plan is sent to the device, each figure in it must match a value from this turn's tool results
or the user's own question:

- times ("18:00", "7am") must be times that appear in a tool result or the question;
- numbers with a unit (MW, GW, kW, kWh, %, hours) must equal a tool or question number,
  allowing honest rounding: "6,600 MW" or "6.6 GW" for 6,628, but not "7,200";
- bare small numbers (up to 12: "3 windows", "two sentences") are not checked; bare larger
  numbers are.

Dates ("30 Oct", "2026-01-09") and years are not figures and are skipped.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass

# Rounding may move a value by at most this much (relative), e.g. 6,628 -> "6,600" is 0.4%.
MAX_ROUNDING = 0.05
SMALL_BARE_NUMBER = 12

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DATES = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}(?:T\d{{2}}:\d{{2}}(?::\d{{2}})?Z?)?"
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})[a-z]*\b"
    rf"|\b(?:{_MONTHS})[a-z]*\s+\d{{1,2}}\b"
    rf"|\b20\d\d\b",
    re.IGNORECASE,
)
_CLOCK = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b(?:\s*([ap])\.?m\.?\b)?", re.IGNORECASE)
_AMPM = re.compile(r"\b(1[0-2]|0?[1-9])\s*([ap])\.?m\.?\b", re.IGNORECASE)
_NUMBER = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
    r"(?:\s*(?:(GW|MW|kWh|kW|hours?|hrs?|h)\b|(%)|(per\s?cent)\b))?",
    re.IGNORECASE,
)


@dataclass
class Claim:
    text: str  # as written: "6,628 MW", "18:00"
    kind: str  # "time" or "number"
    supported: bool
    source: str | None = None  # what it matched, e.g. "plan_charge.expected_wind_while_charging_mw"


@dataclass
class FactCheck:
    claims: list[Claim]

    @property
    def passed(self) -> bool:
        return all(c.supported for c in self.claims)

    @property
    def unsupported(self) -> list[Claim]:
        return [c for c in self.claims if not c.supported]

    @property
    def summary(self) -> str:
        if not self.claims:
            return "no figures to check"
        if self.passed:
            return f"facts checked {len(self.claims)}/{len(self.claims)}"
        n = len(self.unsupported)
        return "1 fact doesn't match" if n == 1 else f"{n} facts don't match"

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "claims": [vars(c) for c in self.claims],
        }


# ---- reading figures ---------------------------------------------------------------------


def _hhmm(hour: int, minute: int, meridiem: str | None) -> str:
    if meridiem:
        hour = hour % 12 + (12 if meridiem.lower() == "p" else 0)
    return f"{hour:02d}:{minute:02d}"


def _times(text: str) -> tuple[list[tuple[str, str]], str]:
    """(written, "HH:MM") for each time in `text`, and the text with times blanked out."""
    found: list[tuple[int, str, str]] = []

    def clock(m: re.Match) -> str:
        found.append((m.start(), m.group(0), _hhmm(int(m.group(1)), int(m.group(2)), m.group(3))))
        return " " * len(m.group(0))

    def ampm(m: re.Match) -> str:
        found.append((m.start(), m.group(0), _hhmm(int(m.group(1)), 0, m.group(2))))
        return " " * len(m.group(0))

    text = _CLOCK.sub(clock, text)
    text = _AMPM.sub(ampm, text)
    return [(written, value) for _, written, value in sorted(found)], text


def _numbers(text: str) -> Iterator[tuple[str, float, int, str]]:
    """(written, value, decimals, unit) for each number, after dates and times are removed."""
    for m in _NUMBER.finditer(text):
        whole, decimals = m.group(1), m.group(2)
        unit = (m.group(3) or "").lower() or ("%" if m.group(4) or m.group(5) else "")
        value = float(whole.replace(",", "") + (f".{decimals}" if decimals else ""))
        yield m.group(0).strip(), value, len(decimals or ""), unit


def figures(text: str) -> tuple[list[tuple[str, str]], list[tuple[str, float, int, str]]]:
    """Times and numbers in `text`, with dates and years skipped."""
    text = _DATES.sub(lambda m: " " * len(m.group(0)), text)
    times, rest = _times(text)
    return times, list(_numbers(rest))


# ---- what the tools said -------------------------------------------------------------------


def _flatten(value, path: str) -> Iterator[tuple[str, object]]:
    """(path, value) for every leaf, plus each dict key as text: keys carry figures too
    (e.g. "6-12h ahead": 89)."""
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield child, str(key)
            yield from _flatten(item, child)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from _flatten(item, f"{path}[{i}]")
    else:
        yield path, value


class Evidence:
    """Every number and time the agent may quote: tool results plus the user's question."""

    def __init__(self, tool_calls: list[dict], question: str = "") -> None:
        self.numbers: list[tuple[str, float]] = []
        self.times: dict[str, str] = {}
        for call in tool_calls:
            for path, value in _flatten(call.get("result"), call.get("name", "tool")):
                if isinstance(value, bool) or value is None:
                    continue
                if isinstance(value, int | float):
                    self.numbers.append((path, float(value)))
                elif isinstance(value, str):
                    self._add_text(value, path)
        self._add_text(question, "question")

    def _add_text(self, text: str, source: str) -> None:
        times, numbers = figures(text)
        for _, hhmm in times:
            self.times.setdefault(hhmm, source)
        for _, value, _, _ in numbers:
            self.numbers.append((source, value))

    def match_number(self, value: float, decimals: int, unit: str) -> str | None:
        for source, known in self.numbers:
            if _rounds_to(known * (0.001 if unit == "gw" else 1), value, decimals):
                return source
        return None


def _rounds_to(known: float, written: float, decimals: int) -> bool:
    """Is `written` an honest rounding of `known`?"""
    if math.isclose(known, written, rel_tol=1e-9, abs_tol=1e-9):
        return True
    if abs(known - written) > MAX_ROUNDING * max(abs(known), 1e-9):
        return False
    if decimals > 0:
        return round(known, decimals) == written
    # Whole numbers: rounded to the place of their last non-zero digit (6,600 -> hundreds).
    step = 1
    while written and written % (step * 10) == 0:
        step *= 10
    return round(known / step) * step == written or round(known) == written


def check(answer: str, tool_calls: list[dict], question: str = "") -> FactCheck:
    evidence = Evidence(tool_calls, question)
    times, numbers = figures(answer)
    claims = [
        Claim(written, "time", hhmm in evidence.times, evidence.times.get(hhmm))
        for written, hhmm in times
    ]
    for written, value, decimals, unit in numbers:
        if not unit and value <= SMALL_BARE_NUMBER and decimals == 0:
            continue
        source = evidence.match_number(value, decimals, unit)
        claims.append(Claim(written, "number", source is not None, source))
    return FactCheck(claims)
