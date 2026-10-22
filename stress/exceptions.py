class Error(Exception):
    """The stress harness's own failures, raised where a scenario makes the world break."""


class WorkerDied(Error):
    def __init__(self, pid: int) -> None:
        super().__init__(f"The worker died with its crawl (process {pid} killed outright).")


class BucketDown(Error):
    def __init__(self) -> None:
        super().__init__("The files bucket can't be reached.")
