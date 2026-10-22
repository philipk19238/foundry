import uuid


class Error(Exception):
    """Everything foundry refuses. The message is written for whoever acts on it: the person in the UI or the agent."""


class SourceNotFound(Error):
    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__(f"There is no source {source_id}.")


class RunNotFound(Error):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(f"There is no run {run_id}.")


class HelpNotFound(Error):
    def __init__(self, help_id: uuid.UUID) -> None:
        super().__init__(f"There is no help request {help_id}.")


class LeaseLost(Error):
    def __init__(self) -> None:
        super().__init__("This run no longer holds its source: it expired and another run took it over.")


class SourceBusy(Error):
    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__(f"Another run is working on source {source_id}; this one was skipped.")


class NotSignedIn(Error):
    def __init__(self) -> None:
        super().__init__("Sign in first.")


class WrongPassword(Error):
    def __init__(self) -> None:
        super().__init__("That email and password don't match.")


class NotAllowed(Error):
    def __init__(self, action: str) -> None:
        super().__init__(f"Your role may not {action}.")


class HelpTaken(Error):
    def __init__(self) -> None:
        super().__init__("Someone else claimed this request, or it is already closed.")


class ScraperRejected(Error):
    def __init__(self, reason: str) -> None:
        super().__init__(f"The scraper failed its check: {reason}")


class AgentFailed(Error):
    def __init__(self, reason: str) -> None:
        super().__init__(f"The agent stopped with an error: {reason}")


class NotifyFailed(Error):
    def __init__(self, reason: str) -> None:
        super().__init__(f"The help email did not go out ({reason}); the request is still on the help queue page.")


class BrowserGone(Error):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"The Kernel browser {session_id} has closed.")


class KernelRefused(Error):
    def __init__(self, action: str) -> None:
        super().__init__(f"Kernel would not {action}; check the Kernel key and plan.")


class NoScraper(Error):
    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__(f"Source {source_id} has no scraper yet; onboard it first.")


class AgentWorking(Error):
    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__("The agent is still working on this scraper. Approve it once the agent hands in its version.")


class NotImportable(Error):
    def __init__(self, pull: int, why: str) -> None:
        super().__init__(f"Pull request #{pull} can't be imported: {why}.")


class NothingToMerge(Error):
    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__("There is no version waiting for review on this scraper.")


class GitFailed(Error):
    def __init__(self, command: str, output: str) -> None:
        super().__init__(f"git {command} failed: {output}")


class NoGitHubToken(Error):
    def __init__(self) -> None:
        super().__init__("Set GITHUB_TOKEN to a token that can push branches and open pull requests on the repo.")


class GitHubRefused(Error):
    def __init__(self, action: str, status: int, message: str) -> None:
        super().__init__(f"GitHub would not {action} ({status}): {message}")


class SiteMissing(Error):
    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"The site {key} is not registered in the tree: {reason}")


class NothingToDeploy(Error):
    def __init__(self, commit: str) -> None:
        super().__init__(
            f"main is still at {commit[:12]}, which is what runs now. Your change must be merged before a restart "
            "can pick it up: open a pull request and ask a person to merge it."
        )


class TooManyRestarts(Error):
    def __init__(self, limit: int) -> None:
        super().__init__(f"This run already restarted {limit} times; finish on the code you have, or ask for help.")


class DeployFailed(Error):
    def __init__(self, commit: str, output: str) -> None:
        super().__init__(f"Deploying main at {commit[:12]} failed; the run stopped here. modal said: {output}")


class FileNotFound(Error):
    def __init__(self, notice_id: str, file_id: str) -> None:
        super().__init__(f"Notice {notice_id} has no file {file_id}.")


class MissingSetting(Error):
    def __init__(self, what: str) -> None:
        super().__init__(f"Set {what}.")


class NoAgent(Error):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(f"Run {run_id} is a crawl with no agent in it, so nobody would read this.")


class CrawlStopped(Error):
    def __init__(self) -> None:
        super().__init__("A person restarted this crawl; the next attempt carries on where it got to.")


class CrawlStalled(Error):
    def __init__(self, why: str) -> None:
        super().__init__(f"The crawl {why}.")


class CannotRestart(Error):
    def __init__(self, run_id: uuid.UUID) -> None:
        super().__init__(f"Run {run_id} isn't a running or failed scrape, so it can't be restarted.")


class NoBronze(Error):
    def __init__(self, what: str) -> None:
        super().__init__(f"Foundry kept nothing the portal sent for {what}.")


class RecordingNotFound(Error):
    def __init__(self, run_id: uuid.UUID, sha256: str) -> None:
        super().__init__(f"Run {run_id} kept no recording {sha256}.")
