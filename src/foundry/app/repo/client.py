import httpx
from pydantic import TypeAdapter

from foundry.app import exceptions
from foundry.app.repo import models
from foundry.app.settings import Settings

API = "https://api.github.com"
BASE = "main"
MESSAGE = 300
PULLS = TypeAdapter(list[models.Pull])


class GitHubPulls:
    """Pull requests on the foundry repo, through GitHub's REST API. Opening one for a branch that already has an
    open pull request updates that one instead."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._repo = settings.repo
        self._token = settings.github_token

    async def open(self, branch: str, title: str, body: str) -> models.Pull:
        owner = self._repo.partition("/")[0]
        found = PULLS.validate_json(
            await self._call("list pull requests", "GET", "pulls", {"head": f"{owner}:{branch}"}, None)
        )
        if found:
            edit = {"title": title, "body": body}
            updated = await self._call("update the pull request", "PATCH", f"pulls/{found[0].number}", None, edit)
            return models.Pull.model_validate_json(updated)
        new = {"title": title, "head": branch, "base": BASE, "body": body}
        return models.Pull.model_validate_json(await self._call("open a pull request", "POST", "pulls", None, new))

    async def get(self, number: int) -> models.Pull:
        return models.Pull.model_validate_json(
            await self._call("read the pull request", "GET", f"pulls/{number}", None, None)
        )

    async def _call(
        self, action: str, method: str, path: str, params: dict[str, str] | None, body: dict[str, str] | None
    ) -> bytes:
        if self._token is None:
            raise exceptions.NoGitHubToken()
        headers = {
            "Authorization": f"Bearer {self._token.get_secret_value()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = f"{API}/repos/{self._repo}/{path}"
        response = await self._http.request(method, url, headers=headers, params=params, json=body)
        if response.is_error:
            raise exceptions.GitHubRefused(action, response.status_code, response.text[:MESSAGE])
        return response.content
