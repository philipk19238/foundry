import asyncio
import datetime as dt
import uuid

from foundry.app import exceptions
from foundry.app.auth.auth import Users
from foundry.app.auth.models import UserView
from foundry.app.db.db import Sessionmaker, now
from foundry.app.db.models.help import HelpStatus
from foundry.app.help import models, store
from foundry.app.runs.models import EventKind
from foundry.app.runs.runs import Pauses, Queue, RunHistory, Timeline
from foundry.app.settings import settings

WAIT = dt.timedelta(minutes=45)
POLL = dt.timedelta(seconds=3)
PAGE = 100
TAKEOVER = (
    "I'd like to take over your browser. Finish the step you're on, then call request_help saying you're pausing "
    "for me, and wait for me to hand it back."
)


class RunChat:
    """A run's chat, from the people's side: what they write reaches the agent, and answers its request when it is
    waiting on one."""

    def __init__(self, sessionmaker: Sessionmaker, timeline: Timeline, history: RunHistory, queue: Queue) -> None:
        self._sessionmaker = sessionmaker
        self._timeline = timeline
        self._history = history
        self._queue = queue

    async def say(self, run_id: uuid.UUID, user: UserView, text: str) -> None:
        """A person writes to the agent. While the agent waits on a request this person may answer, the message
        answers it; while it works, the message reaches its running turn; once the run has ended, the message reopens
        it and the agent carries on in the same conversation."""
        run = await self._history.run(run_id)
        if not run.has_agent:
            raise exceptions.NoAgent(run_id)
        if not run.is_active and not user.is_admin:
            raise exceptions.NotAllowed("start the agent again")
        async with self._sessionmaker.begin() as session:
            answered = await store.answer(session, run_id, user.id, text, now())
        await self._timeline.wrote(run_id, EventKind.ANSWER if answered else EventKind.PERSON, user.email, text)
        if not run.is_active:
            await self._queue.reopen(run_id)

    async def ask_to_take_over(self, run_id: uuid.UUID, user: UserView) -> None:
        """Asks the agent to pause and open a help request, which this person can then claim."""
        await self.say(run_id, user, TAKEOVER)


class HelpQueue:
    """The people's side of help requests: what is open, and claiming, giving back and finishing one. Only the person
    who claimed a request sees the agent's browser."""

    def __init__(self, sessionmaker: Sessionmaker, timeline: Timeline) -> None:
        self._sessionmaker = sessionmaker
        self._timeline = timeline

    async def request(self, help_id: uuid.UUID) -> models.HelpView:
        async with self._sessionmaker() as session:
            request = await store.one(session, help_id)
        if request is None:
            raise exceptions.HelpNotFound(help_id)
        return models.HelpView.model_validate(request)

    async def live(self) -> list[models.HelpView]:
        async with self._sessionmaker() as session:
            return [models.HelpView.model_validate(request) for request in await store.open_requests(session, now())]

    async def recent(self) -> list[models.HelpView]:
        async with self._sessionmaker() as session:
            return [models.HelpView.model_validate(request) for request in await store.recent(session, PAGE)]

    async def of_run(self, run_id: uuid.UUID) -> list[models.HelpView]:
        async with self._sessionmaker() as session:
            return [models.HelpView.model_validate(request) for request in await store.of_run(session, run_id)]

    async def claim(self, help_id: uuid.UUID, user: UserView) -> models.HelpView:
        async with self._sessionmaker.begin() as session:
            claimed = await store.claim(session, help_id, user.id, now())
        request = await self.request(help_id)
        if not claimed and not request.is_held_by(user):
            raise exceptions.HelpTaken()
        if claimed:
            await self._timeline.log(request.run_id, EventKind.NOTE, f"{user.email} took over the browser")
        return request

    async def give_back(self, help_id: uuid.UUID, user: UserView) -> None:
        async with self._sessionmaker.begin() as session:
            given = await store.give_back(session, help_id, user.id)
        if not given:
            raise exceptions.HelpTaken()
        request = await self.request(help_id)
        await self._timeline.log(request.run_id, EventKind.NOTE, f"{user.email} let go of the browser")

    async def done(self, help_id: uuid.UUID, user: UserView, note: str) -> None:
        async with self._sessionmaker.begin() as session:
            finished = await store.done(session, help_id, user.id, note, now())
        if not finished:
            raise exceptions.HelpTaken()
        request = await self.request(help_id)
        await self._timeline.log(request.run_id, EventKind.NOTE, f"{user.email} handed the browser back")
        if note:
            await self._timeline.wrote(request.run_id, EventKind.ANSWER, user.email, note)


class HelpDesk:
    """Where a stuck agent asks a person to take over its browser. While a request is live the agent only waits, so
    the person and the agent never drive the browser at once (formal/runs `OneDriver`)."""

    def __init__(
        self,
        sessionmaker: Sessionmaker,
        users: Users,
        notifier: models.Notifier,
        pauses: Pauses,
        timeline: Timeline,
        queue: HelpQueue,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._users = users
        self._notifier = notifier
        self._pauses = pauses
        self._timeline = timeline
        self._queue = queue

    async def ask(self, run_id: uuid.UUID, reason: str, live_view_url: str) -> models.Answer:
        """Asks for help and waits for the answer: helped, or unanswered once the request expires."""
        at = now()
        async with self._sessionmaker.begin() as session:
            request = await store.add(session, run_id, reason, live_view_url, at, at + WAIT)
        await self._pauses.pause(run_id)
        try:
            await self._timeline.log(run_id, EventKind.HELP, reason)
            url = f"{settings.base_url}/runs/{run_id}"
            try:
                await self._notifier.help_requested(request.id, await self._users.all(), reason, url)
            except exceptions.NotifyFailed as error:
                await self._timeline.log(run_id, EventKind.ERROR, str(error))
            answer = await self._answer(request.id)
        finally:
            await self._pauses.resume(run_id)
        if isinstance(answer, models.NoAnswer):
            await self._timeline.log(run_id, EventKind.NOTE, "Nobody answered in time, so the agent stopped waiting")
        return answer

    async def _answer(self, help_id: uuid.UUID) -> models.Answer:
        while True:
            request = await self._queue.request(help_id)
            if request.status == HelpStatus.DONE:
                return models.HumanHelp(note=request.note or "")
            if request.status == HelpStatus.EXPIRED:
                return models.NoAnswer()
            if request.expires_at <= now():
                async with self._sessionmaker.begin() as session:
                    await store.expire(session, help_id, now())
                continue
            await asyncio.sleep(POLL.total_seconds())
