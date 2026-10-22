import datetime as dt
import uuid

from foundry.app.db.db import Sessionmaker
from foundry.app.runs import models, store


class RunChanges:
    """Where runs have got to, compared look by look by whoever tells listeners what changed."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def marks(self, since: dt.datetime) -> list[models.RunMark]:
        """Every run that hasn't ended, or ended after `since`."""
        async with self._sessionmaker() as session:
            return list(await store.marks(session, since))

    async def written(self, after: int) -> dict[uuid.UUID, int]:
        """The runs with lines after event `after`, each with its newest line's id."""
        async with self._sessionmaker() as session:
            return await store.written(session, after)

    async def last_line(self) -> int:
        async with self._sessionmaker() as session:
            return await store.last_event(session)
