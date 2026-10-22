"""The Lean proofs in formal/foundry_rules describe the code only if the code computes what the model computes. These
compare the Python ids and health rules with the VECTOR lines the model prints (formal/foundry_rules/vectors.txt)."""

import datetime as dt
import uuid
from pathlib import Path

from foundry.app.catalog.models import ScrapedNotice
from foundry.app.core.models import Key
from foundry.app.db.models.catalog import SourceStatus
from foundry.app.scrapers.models import Crawl, Health, Verdict
from foundry.core.models import FileLink, Listing

VECTORS = dict(
    line.removeprefix("VECTOR ").split(" ", 1)
    for line in (Path(__file__).parents[1] / "formal/foundry_rules/vectors.txt").read_text().splitlines()
    if line.startswith("VECTOR ")
)


SAMPLE = ScrapedNotice(
    source_id=uuid.UUID("6f1c2a9e-0b7d-4e55-9a7e-1d2f3c4b5a69"),
    listing=Listing(
        external_id="RFP-25-001",
        url="https://example.gov/1",
        title="Road salt",
        due_at=dt.datetime(2026, 11, 3, 14, tzinfo=dt.timezone(dt.timedelta(hours=-6))),
        description="Bulk road salt",
        metadata={"naics": "212390"},
        files=(
            FileLink(url="https://example.gov/1/b.pdf", name="b.pdf"),
            FileLink(url="https://example.gov/1/a.pdf", name="a.pdf"),
            FileLink(url="https://example.gov/1/b.pdf", name="b.pdf"),
        ),
    ),
)


def test_opportunity_key_matches_the_model() -> None:
    key = Key.of("opportunity", [str(SAMPLE.source_id), "id", "RFP-25-001"])
    assert key.encoded.hex() == VECTORS["opportunity"]
    assert key.digest == SAMPLE.opportunity_id


def test_a_listing_without_a_portal_id_is_anchored_by_its_title_as_in_the_model() -> None:
    untitled = ScrapedNotice(
        source_id=SAMPLE.source_id,
        listing=SAMPLE.listing.model_copy(update={"external_id": None, "title": "  Road   SALT "}),
    )
    key = Key.of("opportunity", [str(SAMPLE.source_id), "title", "road salt"])
    assert key.encoded.hex() == VECTORS["untitled"]
    assert key.digest == untitled.opportunity_id


def test_notice_key_matches_the_model_with_files_sorted_and_deduplicated() -> None:
    listing = SAMPLE.listing
    assert listing.due_at is not None
    fields = [
        str(SAMPLE.source_id),
        "id",
        "RFP-25-001",
        listing.title,
        listing.due_at.isoformat(),
        listing.description,
    ]
    key = Key.of("notice", [*fields, SAMPLE.canonical_metadata, *SAMPLE.files_key])
    assert key.encoded.hex() == VECTORS["notice"]
    assert key.digest == SAMPLE.notice_id


def test_silent_breakage_is_a_heal_as_in_the_model() -> None:
    crawl = Crawl(auth_failed=False, crashed=False, invalid=0, valid=0, previous_valid=12)
    assert VECTORS["verdict"] == "0,0,0,0,12 Foundry.Verdict.heal"
    assert crawl.verdict == Verdict.HEAL


def test_an_exhausted_relogin_asks_a_person_as_in_the_model() -> None:
    logged_out = Crawl(auth_failed=True, crashed=False, invalid=0, valid=0, previous_valid=12)
    assert VECTORS["after"] == "3,relogin { status := Foundry.Status.needsHuman, heals := 3 }"
    assert Health(status=SourceStatus.RELOGIN, heals=3).after(logged_out) == Health(
        status=SourceStatus.NEEDS_HUMAN, heals=3
    )
