"""The resources a listener hears about, and one look at where every run had got to."""

import uuid

from pydantic import BaseModel

from foundry.app.runs.models import RunMark

QUESTIONS = "foundry://questions"


def run_uri(run_id: uuid.UUID) -> str:
    return f"foundry://runs/{run_id}"


def chat_uri(run_id: uuid.UUID) -> str:
    return f"foundry://runs/{run_id}/chat"


def source_uri(source_id: uuid.UUID) -> str:
    return f"foundry://sources/{source_id}"


class Look(BaseModel, frozen=True):
    """Where every run still going (or ended lately) had got to, and the newest line's id, at one look."""

    marks: dict[uuid.UUID, RunMark]
    after: int

    def changed(self, later: "Look", written: dict[uuid.UUID, int]) -> list[str]:
        uris = [chat_uri(run_id) for run_id in written]
        for mark in later.marks.values():
            before = self.marks.get(mark.id)
            if before == mark:
                continue
            uris += [run_uri(mark.id), source_uri(mark.source_id)]
            if before is None or before.status != mark.status:
                uris.append(QUESTIONS)
        return list(dict.fromkeys(uris))
