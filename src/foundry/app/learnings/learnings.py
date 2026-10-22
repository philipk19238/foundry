import uuid
from pathlib import Path

from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.learnings import Learning
from foundry.app.learnings import models, store


class Learnings:
    """What agents learned, shared by every run: each run starts with the index in its instructions and every
    learning as a file it can open, and saves what it learns with its `remember` tool."""

    def __init__(self, sessionmaker: Sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def all(self) -> list[models.LearningView]:
        async with self._sessionmaker() as session:
            return [models.LearningView.model_validate(row) for row in await store.learnings(session)]

    async def remember(self, run_id: uuid.UUID, learning: models.NewLearning) -> None:
        async with self._sessionmaker() as session, session.begin():
            await store.keep(session, Learning(**learning.model_dump(), run_id=run_id, updated_at=now()))

    async def write_out(self, folder: Path) -> str:
        """Writes every learning into `folder`, one file each, and returns the index."""
        known = await self.all()
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.md"):
            old.unlink()
        for learning in known:
            (folder / f"{learning.name}.md").write_text(learning.file)
        return "\n".join(learning.line for learning in known) or "(nothing yet)"
