from pydantic import BaseModel

from foundry.app.catalog.models import SourceView
from foundry.app.runs.models import RunView


class NewSource(BaseModel):
    url: str


class Onboarding(BaseModel):
    source: SourceView
    run: RunView
