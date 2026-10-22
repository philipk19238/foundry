import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class NewLearning(BaseModel, frozen=True):
    """A learning as an agent saves it. `name` is a short slug, `about` what it applies to (`platform:planetbids`,
    `buyer:us/ca/menifee`, `general`), `description` the one line the index shows."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    about: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)


class LearningView(BaseModel, frozen=True):
    model_config = ConfigDict(from_attributes=True)

    name: str
    about: str
    description: str
    body: str
    run_id: uuid.UUID | None
    updated_at: dt.datetime

    @property
    def file(self) -> str:
        """The learning as the file an agent reads."""
        return f"# {self.name}\n\nAbout: {self.about}\nUpdated: {self.updated_at:%Y-%m-%d}\n\n{self.body}\n"

    @property
    def line(self) -> str:
        """The learning's line in the index every run starts with."""
        return f"- `{self.name}` ({self.about}): {self.description}"
