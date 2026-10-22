import hashlib
import uuid
from collections.abc import Sequence

from pydantic import BaseModel


class Key(BaseModel, frozen=True):
    """Fields that identify something, hashed into its id. Each field is encoded as an 8-byte big-endian length then
    its UTF-8 bytes, so no two field lists encode alike (formal/foundry_rules `encode_injective`)."""

    fields: tuple[str, ...]

    @classmethod
    def of(cls, tag: str, fields: Sequence[str]) -> "Key":
        return cls(fields=(tag, *fields))

    @property
    def encoded(self) -> bytes:
        return b"".join(len(data).to_bytes(8, "big") + data for data in (field.encode() for field in self.fields))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.encoded).hexdigest()


class Lease(BaseModel, frozen=True):
    """A run's hold on a source. Every write a run makes on the source's behalf checks the fence, so a run that lost
    its lease (expired, then taken by another run) changes nothing."""

    source_id: uuid.UUID
    run_id: uuid.UUID
    fence: int
