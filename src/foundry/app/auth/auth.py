import datetime as dt

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from foundry.app import exceptions
from foundry.app.auth import models, store
from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.auth import Role

LOGIN_LIFETIME = dt.timedelta(days=14)


class Users:
    """Who may sign in, and with which password. Passwords are kept only as argon2 hashes."""

    def __init__(self, sessionmaker: Sessionmaker, hasher: PasswordHasher) -> None:
        self._sessionmaker = sessionmaker
        self._hasher = hasher

    async def add(self, email: str, password: str, role: Role) -> models.UserView:
        async with self._sessionmaker.begin() as session:
            user = await store.add_user(session, email.lower(), self._hasher.hash(password), role, now())
            return models.UserView.model_validate(user)

    async def ensure(self, email: str, password: str, role: Role) -> models.UserView:
        """Adds the user unless one with this email exists; an existing user's password is left as it is."""
        async with self._sessionmaker() as session:
            user = await store.by_email(session, email.lower())
        if user is not None:
            return models.UserView.model_validate(user)
        return await self.add(email, password, role)

    async def all(self) -> list[models.UserView]:
        async with self._sessionmaker() as session:
            return [models.UserView.model_validate(user) for user in await store.users(session)]


class Logins:
    """Signed-in browsers and API tokens, both kept as hashes of the secret the holder sends."""

    def __init__(self, sessionmaker: Sessionmaker, hasher: PasswordHasher) -> None:
        self._sessionmaker = sessionmaker
        self._hasher = hasher

    async def sign_in(self, email: str, password: str) -> models.Token:
        async with self._sessionmaker.begin() as session:
            user = await store.by_email(session, email.lower())
            if user is None:
                raise exceptions.WrongPassword()
            try:
                self._hasher.verify(user.password_hash, password)
            except VerificationError as error:
                raise exceptions.WrongPassword() from error
            token = models.Token.mint()
            await store.add_login(session, token.hash, user.id, now() + LOGIN_LIFETIME)
            return token

    async def sign_out(self, token: models.Token) -> None:
        async with self._sessionmaker.begin() as session:
            await store.sign_out(session, token.hash)

    async def by_login(self, token: models.Token) -> models.UserView:
        async with self._sessionmaker() as session:
            user = await store.by_login(session, token.hash, now())
        if user is None:
            raise exceptions.NotSignedIn()
        return models.UserView.model_validate(user)

    async def by_token(self, token: models.Token) -> models.UserView:
        async with self._sessionmaker() as session:
            user = await store.by_token(session, token.hash)
        if user is None:
            raise exceptions.NotSignedIn()
        return models.UserView.model_validate(user)

    async def mint(self, user: models.UserView, name: str) -> models.Token:
        token = models.Token.mint()
        async with self._sessionmaker.begin() as session:
            await store.add_token(session, token.hash, user.id, name, now())
        return token

    async def tokens(self, user: models.UserView) -> list[models.TokenView]:
        async with self._sessionmaker() as session:
            return [models.TokenView.model_validate(token) for token in await store.tokens(session, user.id)]

    async def revoke(self, user: models.UserView, token_hash: str) -> None:
        async with self._sessionmaker.begin() as session:
            await store.revoke(session, token_hash, user.id)
