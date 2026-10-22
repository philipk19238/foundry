from typing import Protocol


class Bucket(Protocol):
    """Where attachment bytes live, each under the SHA-256 of its bytes, so a file posted on many notices is kept
    once."""

    async def put(self, digest: str, content: bytes, content_type: str | None) -> None: ...

    async def link(self, digest: str, name: str) -> str:
        """A URL that downloads the file as ``name``, for a signed-in person."""
        ...
