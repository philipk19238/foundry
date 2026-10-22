"""The Lean proofs of the scraper's stage and status rules (formal/foundry_rules, sections 7 to 10) describe the code
only if the code computes what the model computes. These replay every case the model prints
(formal/foundry_rules/stage_vectors.txt) through `Stage.of` and `Standing`."""

from pathlib import Path

from foundry.app.db.models.catalog import SourceStatus
from foundry.app.db.models.runs import RunKind
from foundry.app.progress.models import Signals, Stage
from foundry.app.scrapers.models import Standing

CASES = [
    line.split()
    for line in (Path(__file__).parents[1] / "formal/foundry_rules/stage_vectors.txt").read_text().splitlines()
    if line and not line.startswith("#")
]


def flag(value: str) -> bool:
    return value == "1"


def test_stage_matches_the_model_for_every_status_and_signal() -> None:
    cases = [case[1:] for case in CASES if case[0] == "stage"]
    wrong = [
        (status, working, waiting, reviewing, expected)
        for status, working, waiting, reviewing, expected in cases
        if Stage.of(
            SourceStatus(status), Signals(working=flag(working), waiting=flag(waiting), reviewing=flag(reviewing))
        )
        != Stage(expected)
    ]
    assert len(cases) == 56
    assert wrong == []


def test_a_job_sets_the_status_the_model_says_when_it_starts() -> None:
    cases = [case[1:] for case in CASES if case[0] == "working"]
    wrong = [
        (kind, live, expected)
        for kind, live, expected in cases
        if Standing(live=flag(live), proposed=False).working(RunKind(kind))
        != (None if expected == "none" else SourceStatus(expected))
    ]
    assert len(cases) == 8
    assert wrong == []


def test_a_job_leaves_the_status_the_model_says_when_it_ends() -> None:
    cases = [case[1:] for case in CASES if case[0] == "settled"]
    wrong = []
    for kind, rejected, live, proposed, expected in cases:
        standing = Standing(live=flag(live), proposed=flag(proposed))
        got = standing.rejected(RunKind(kind)) if flag(rejected) else standing.settled()
        if got != SourceStatus(expected):
            wrong.append((kind, rejected, live, proposed, expected, got))
    assert len(cases) == 32
    assert wrong == []
