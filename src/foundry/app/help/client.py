import uuid
from collections.abc import Sequence

from knockapi import APIError, AsyncKnock

from foundry.app import exceptions
from foundry.app.auth.models import UserView
from foundry.app.settings import Settings


class KnockNotifier:
    """Emails people through two Knock workflows in Knock's development environment: one per help request, and one
    for the agent's messages, which batches a run's burst of messages into one email. Recipients are identified inline
    under a `foundry-` id, so they never touch another app's Knock users."""

    def __init__(self, knock: AsyncKnock, settings: Settings) -> None:
        self._knock = knock
        self._help = settings.knock_workflow
        self._messages = settings.knock_message_workflow

    async def help_requested(self, help_id: uuid.UUID, recipients: Sequence[UserView], reason: str, url: str) -> None:
        await self._trigger(self._help, recipients, {"reason": reason, "url": url}, str(help_id))

    async def agent_said(self, run_id: uuid.UUID, recipients: Sequence[UserView], text: str, url: str) -> None:
        await self._trigger(self._messages, recipients, {"run": str(run_id), "text": text, "url": url}, None)

    async def _trigger(
        self, workflow: str, recipients: Sequence[UserView], data: dict[str, object], key: str | None
    ) -> None:
        try:
            await self._knock.workflows.trigger(
                workflow,
                recipients=[{"id": f"foundry-{user.id}", "email": user.email} for user in recipients],
                data=data,
                idempotency_key=key,
            )
        except APIError as error:
            raise exceptions.NotifyFailed(type(error).__name__) from error
