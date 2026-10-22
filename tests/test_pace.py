"""The Lean proofs of how a live crawl slows down and recovers (formal/foundry_rules, section 11) describe the kit only
if `Pace` computes what the model computes. These replay every case the model prints
(formal/foundry_rules/resume_vectors.txt) through `Pace`'s public calls: `pace` lines are a run of limits and
successes from the base, and `limit` lines one limit from the base (lines from another delay are reached by the
runs)."""

import asyncio
from pathlib import Path

import pytest
from scrapy.statscollectors import MemoryStatsCollector

from foundry.core.pace import Pace

SITE = "portal.test"
LINES = [
    line.split()
    for line in (Path(__file__).parents[1] / "formal/foundry_rules/resume_vectors.txt").read_text().splitlines()
    if line and not line.startswith("#")
]


class Stats(MemoryStatsCollector):
    def __init__(self) -> None:
        self._stats = {}


def seconds(ms: str) -> float | None:
    return int(ms) / 1000 or None


def run(base: str, events: list[str]) -> float:
    async def after() -> float:
        pace = Pace(int(base) / 1000, 1, Stats())
        for event in events:
            if event == "ok":
                pace.went_through(SITE)
            else:
                pace.limited(SITE, seconds(event.removeprefix("limit ")))
        return pace._delays[SITE]

    return asyncio.run(after())


def test_every_run_of_limits_and_successes_ends_at_the_models_delay() -> None:
    cases = [(base, after, " ".join(rest).split(",")) for kind, base, after, *rest in LINES if kind == "pace"]
    wrong = [
        (base, events, after, delay * 1000)
        for base, after, events in cases
        if abs((delay := run(base, events)) * 1000 - int(after)) > events.count("ok")
    ]
    assert len(cases) == 14
    assert wrong == []


@pytest.mark.parametrize("line", [line for line in LINES if line[0] == "limit" and line[1] == line[2]])
def test_one_limit_from_the_base_sets_the_models_pace_and_wait(line: list[str]) -> None:
    _, base, _, retry_after, pace_ms, wait_ms = line

    async def limited() -> tuple[float, float]:
        pace = Pace(int(base) / 1000, 1, Stats())
        wait = pace.limited(SITE, seconds(retry_after))
        return pace._delays[SITE], wait

    delay, wait = asyncio.run(limited())
    assert (round(delay * 1000), round(wait * 1000)) == (int(pace_ms), int(wait_ms))
