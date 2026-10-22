import datetime as dt
import enum
import json
import uuid
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, JsonValue

from foundry.app.core.models import Key
from foundry.app.db.models.catalog import SourceStatus
from foundry.core.models import FileLink, Listing, SavedFile


class StoredFile(BaseModel, frozen=True):
    """An attachment as stored: `file_id` is its link's id (see `ScrapedNotice.files_key`), `sha256` the hash of the
    bytes when the download worked (and the file's key in the bucket), `etag` the portal's own ETag for it, if it
    sent one."""

    file_id: str
    name: str
    url: str
    sha256: str | None
    etag: str | None
    content_type: str | None
    size: int

    @classmethod
    def of(cls, link: FileLink, saved: SavedFile | None) -> "StoredFile":
        """A link as stored: with the bytes a crawl downloaded, or without a hash when its download failed."""
        file_id = ScrapedNotice.file_id(link)
        if saved is None:
            return cls(
                file_id=file_id, name=link.name, url=link.url, sha256=None, etag=None, content_type=None, size=0
            )  # fmt: skip
        return cls(
            file_id=file_id, name=link.name, url=link.url, sha256=saved.sha256, etag=None,
            content_type=saved.content_type, size=saved.size,
        )  # fmt: skip


class ScrapedNotice(BaseModel, frozen=True):
    """A listing from one crawl, ready to store. The ids follow formal/foundry_rules: the opportunity from the portal
    record alone, the notice from all of its content, with files as a sorted set of digests. A file's digest is the
    id of its link (URL and name), so ids are known before anything is downloaded and a refresh downloads only the
    attachments of new notices."""

    source_id: uuid.UUID
    listing: Listing

    @staticmethod
    def file_id(link: FileLink) -> str:
        return Key.of("file", [link.key, link.name]).digest

    def stored_files(self, saved: Mapping[str, SavedFile]) -> tuple[StoredFile, ...]:
        """Its attachments as stored, from `saved`, the downloads by unsigned URL."""
        return tuple(StoredFile.of(link, saved.get(link.key)) for link in self.listing.files)

    @property
    def anchor(self) -> tuple[str, str]:
        """What ties scrapes of one opportunity together (formal/foundry_rules `kindId`, `kindTitle`): the portal's own
        id, or, when it shows none, the title with case and spacing evened out, so a retitled listing then counts as a
        new opportunity."""
        if self.listing.external_id is not None:
            return ("id", self.listing.external_id)
        return ("title", " ".join(self.listing.title.lower().split()))

    @property
    def opportunity_id(self) -> str:
        return Key.of("opportunity", [str(self.source_id), *self.anchor]).digest

    @property
    def canonical_metadata(self) -> str:
        return json.dumps(self.listing.metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @property
    def files_key(self) -> tuple[str, ...]:
        return tuple(sorted({self.file_id(link) for link in self.listing.files}))

    @property
    def notice_id(self) -> str:
        listing = self.listing
        due_at = listing.due_at.isoformat() if listing.due_at is not None else ""
        fields = [str(self.source_id), *self.anchor, listing.title, due_at, listing.description]
        return Key.of("notice", [*fields, self.canonical_metadata, *self.files_key]).digest

    @property
    def buyer_id(self) -> str | None:
        if self.listing.buyer is None:
            return None
        return Key.of("buyer", [" ".join(self.listing.buyer.lower().split())]).digest


class Known(BaseModel, frozen=True):
    """Which of a batch's notice and opportunity ids the catalog already has."""

    notices: frozenset[str]
    opportunities: frozenset[str]


class Change(enum.StrEnum):
    UNCHANGED = "unchanged"
    AMENDMENT = "amendment"
    NEW = "new"

    @classmethod
    def of(cls, notice: ScrapedNotice, known: Known) -> "Change":
        """formal/foundry_rules `classify`: a stored notice id is a repeat; a stored opportunity is an edit."""
        if notice.notice_id in known.notices:
            return cls.UNCHANGED
        if notice.opportunity_id in known.opportunities:
            return cls.AMENDMENT
        return cls.NEW


class IngestCounts(BaseModel, frozen=True):
    """What one crawl changed: notices for opportunities we had never seen, edits of known ones, and repeats."""

    new: int
    amended: int
    unchanged: int


class SourceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    name: str
    status: SourceStatus
    site: str | None
    heals: int
    previous_valid: int
    scraper_version: int | None
    created_at: dt.datetime


class FileView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: str
    name: str
    url: str
    sha256: str | None
    etag: str | None
    content_type: str | None
    size: int


class NoticeView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notice_id: str
    opportunity_id: str
    external_id: str | None
    url: str
    title: str
    buyer: str | None
    due_at: dt.datetime | None
    description: str
    metadata: dict[str, JsonValue]
    first_seen_at: dt.datetime
    files: list[FileView]


class OpportunityRow(BaseModel):
    """An opportunity as the Opportunities page lists it: its newest snapshot, the source it came from, and how many
    snapshots the portal's edits have made."""

    notice: NoticeView
    source_id: uuid.UUID
    source: str
    snapshots: int

    @property
    def edits(self) -> int:
        """How many times the portal changed the opportunity after it was first seen."""
        return self.snapshots - 1
