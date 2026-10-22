"""Bronze: everything a portal sent, kept beside the listings made from it. A live crawl keeps every response as a
HAR (the bodies under their SHA-256 in the bucket, the HAR beside them); an agent's run keeps the browser recordings
it made while finding the portal's API. Each notice names the crawl that stored it, so its bronze is one step away."""

import asyncio
import hashlib
import os
import uuid
from pathlib import Path

from pydantic import ValidationError

from foundry.app.bronze.models import RECORDINGS, BronzeLink, Recording, RecordingLink
from foundry.app.catalog.catalog import Catalog, Opportunities
from foundry.app.files.models import Bucket
from foundry.app.runs.runs import RunHistory
from foundry.app.scrapers.models.crawl import BronzeRecord
from foundry.app.scrapers.scrapers import HAR_TYPE
from foundry.core.bronze import Secrets
from foundry.core.models import Exchange

SKIPPED = {".git", ".venv", "node_modules", "__pycache__", ".cache", ".ruff_cache", ".pytest_cache"}
HAR_SUFFIX = ".har"
RECORDER_SUFFIX = ".jsonl"
RECORDER_TYPE = "application/x-ndjson"


class Bronze:
    """Where the bronze is: a crawl's HAR, by its run or by a notice it stored, and an agent run's recordings, each
    as a short-lived link from the bucket."""

    def __init__(self, history: RunHistory, catalog: Catalog, opportunities: Opportunities, bucket: Bucket) -> None:
        self._history = history
        self._catalog = catalog
        self._opportunities = opportunities
        self._bucket = bucket

    async def har(self, run_id: uuid.UUID) -> BronzeLink | None:
        """The HAR of a crawl's run; None for a run that kept none (an agent's, a replay, one from before bronze)."""
        run = await self._history.run(run_id)
        kept = (run.stats or {}).get("bronze")
        if kept is None:
            return None
        record = BronzeRecord.model_validate(kept)
        source = await self._catalog.source(run.source_id)
        url = await self._bucket.link(record.har, f"{source.name}-{run.id}.har")
        return BronzeLink(run_id=run.id, url=url, entries=record.entries, crawled_at=run.finished_at or run.created_at)

    async def of_notice(self, notice_id: str) -> BronzeLink | None:
        """The HAR of the crawl that stored a notice."""
        run_id = await self._opportunities.run_of(notice_id)
        return None if run_id is None else await self.har(run_id)

    async def recordings(self, run_id: uuid.UUID) -> list[RecordingLink]:
        """The browser recordings an agent's run kept, with links."""
        run = await self._history.run(run_id)
        kept = RECORDINGS.validate_python((run.stats or {}).get("recordings") or [])
        return [
            RecordingLink(
                run_id=run.id, name=recording.name, sha256=recording.sha256, size=recording.size,
                url=await self._bucket.link(recording.sha256, Path(recording.name).name),
            )
            for recording in kept
        ]  # fmt: skip

    async def recording(self, run_id: uuid.UUID, sha256: str) -> RecordingLink | None:
        return next((link for link in await self.recordings(run_id) if link.sha256 == sha256), None)


class Recordings:
    """Keeps the browser recordings an agent made in its clone: every HAR, and every JSON lines file whose first line
    is a recorded exchange, with the portal's secrets hidden (the agent typed its password into that browser), put in
    the bucket under the SHA-256 of its bytes."""

    def __init__(self, bucket: Bucket) -> None:
        self._bucket = bucket

    async def keep(self, checkout: Path, secrets: Secrets) -> list[Recording]:
        kept = []
        for path in await asyncio.to_thread(self.found, checkout):
            content = secrets.hide_bytes(await asyncio.to_thread(path.read_bytes))
            digest = hashlib.sha256(content).hexdigest()
            kind = HAR_TYPE if path.suffix == HAR_SUFFIX else RECORDER_TYPE
            await self._bucket.put(digest, content, kind)
            kept.append(Recording(name=str(path.relative_to(checkout)), sha256=digest, size=len(content)))
        return kept

    @classmethod
    def found(cls, checkout: Path) -> list[Path]:
        """The recordings in the clone, outside git's and tools' own folders."""
        paths = []
        for folder, folders, files in os.walk(checkout):
            folders[:] = sorted(name for name in folders if name not in SKIPPED)
            for name in sorted(files):
                path = Path(folder) / name
                if path.suffix == HAR_SUFFIX or (path.suffix == RECORDER_SUFFIX and cls.recorded(path)):
                    paths.append(path)
        return paths

    @staticmethod
    def recorded(path: Path) -> bool:
        """Whether a JSON lines file is the recorder's: its first line is an exchange."""
        with path.open(errors="replace") as lines:
            first = lines.readline()
        try:
            Exchange.model_validate_json(first)
        except ValidationError:
            return False
        return True


class RunRecordings:
    """The browser recordings the run's agent made, as kept in the bucket when it last finished, for the run's
    stats."""

    def __init__(self, folder: Path) -> None:
        self._file = folder / "recordings.json"

    def read(self) -> list[Recording]:
        return RECORDINGS.validate_json(self._file.read_text()) if self._file.exists() else []

    def keep(self, recordings: list[Recording]) -> None:
        self._file.write_bytes(RECORDINGS.dump_json(recordings))
