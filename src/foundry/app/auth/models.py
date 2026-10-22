import datetime as dt
import hashlib
import secrets
import uuid

from pydantic import BaseModel, ConfigDict

from foundry.app.db.models.auth import Role


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    created_at: dt.datetime

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


class Token(BaseModel, frozen=True):
    """A bearer secret handed out once. Only its hash is stored, so a database leak gives no working token."""

    value: str

    @classmethod
    def mint(cls) -> "Token":
        return cls(value=secrets.token_urlsafe(32))

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.value.encode()).hexdigest()


class TokenView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token_hash: str
    name: str
    created_at: dt.datetime
