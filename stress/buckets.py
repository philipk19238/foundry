"""Buckets for the harness: one in memory that counts every upload, and one that is down, as R2 in an outage."""

from collections import Counter

from stress.exceptions import BucketDown


class MemoryBucket:
    """Keeps every file in memory under its digest, and how many times each was put, so a scenario can tell a file
    uploaded twice."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self._puts: Counter[str] = Counter()

    async def put(self, digest: str, content: bytes, content_type: str | None) -> None:
        self._files[digest] = content
        self._puts[digest] += 1

    async def link(self, digest: str, name: str) -> str:
        return f"memory://{digest}/{name}"

    @property
    def reuploads(self) -> int:
        return sum(1 for count in self._puts.values() if count > 1)


class DownBucket(MemoryBucket):
    """A bucket every upload to fails."""

    async def put(self, digest: str, content: bytes, content_type: str | None) -> None:
        raise BucketDown()
