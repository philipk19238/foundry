"""A turn the model's provider cut off goes on in the same thread after a wait, and any other failed turn fails the
run (`foundry.app.agent.agent.passing`). Resuming a refused or broken request would loop on it; failing an overload
would end a run that one more wait would have carried on."""

import pytest
from openai_codex.generated.v2_all import TurnError

from foundry.app.agent.agent import passing


def error(info: object) -> TurnError:
    return TurnError.model_validate({"message": "failed", "codexErrorInfo": info})


@pytest.mark.parametrize(
    "info",
    [
        "serverOverloaded",
        "rateLimitExceeded",
        "internalServerError",
        {"responseStreamDisconnected": {"httpStatusCode": 502}},
        {"httpConnectionFailed": {"httpStatusCode": None}},
    ],
    ids=["overloaded", "rate-limited", "server-error", "stream-dropped", "connection-failed"],
)
def test_a_provider_outage_resumes_the_turn(info: object) -> None:
    assert passing(error(info))


@pytest.mark.parametrize(
    "info",
    ["usageLimitExceeded", "contextWindowExceeded", "unauthorized", "badRequest", "sandboxError", "other", None],
    ids=["out-of-credit", "context-full", "unauthorized", "bad-request", "sandbox", "other", "unknown"],
)
def test_any_other_failure_fails_the_run(info: object) -> None:
    assert not passing(error(info))
