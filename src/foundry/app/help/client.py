import uuid
from collections.abc import Sequence

from knockapi import APIError, AsyncKnock

from foundry.app import exceptions
from foundry.app.auth.models import UserView


class KnockNotifier:
    """Emails the helpers through one Knock workflow, in Knock's development environment. Recipients are identified
    inline under a `foundry-` id, so they never touch another app's Knock users."""

    def __init__(self, knock: AsyncKnock, workflow: str) -> None:
        self._knock = knock
        self._workflow = workflow

    async def help_requested(self, help_id: uuid.UUID, recipients: Sequence[UserView], reason: str, url: str) -> None:
        try:
            await self._knock.workflows.trigger(
                self._workflow,
                recipients=[{"id": f"foundry-{user.id}", "email": user.email} for user in recipients],
                data={"reason": reason, "url": url},
                idempotency_key=str(help_id),
            )
        except APIError as error:
            raise exceptions.NotifyFailed(type(error).__name__) from error
