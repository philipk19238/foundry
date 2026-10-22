import asyncio
import re
import urllib.parse
from pathlib import Path

import boto3
from mypy_boto3_s3 import S3Client

from foundry.app import exceptions
from foundry.app.settings import Settings

LINK_SECONDS = 600
PREFIX = "files/"
DIGEST = re.compile(r"[0-9a-f]{64}")


class R2Bucket:
    """A Cloudflare R2 bucket, through its S3 API. Links are presigned GETs that expire in ten minutes, so the bytes
    go straight from R2 to the browser."""

    def __init__(self, settings: Settings) -> None:
        if settings.files_bucket is None or settings.r2_access_key_id is None or settings.r2_secret_access_key is None:
            raise exceptions.MissingSetting("FILES_BUCKET, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY for R2")
        self._bucket = settings.files_bucket
        self._client: S3Client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
            region_name="auto",
        )

    async def put(self, digest: str, content: bytes, content_type: str | None) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=PREFIX + digest,
            Body=content,
            ContentType=content_type or "application/octet-stream",
        )

    async def link(self, digest: str, name: str) -> str:
        disposition = f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": PREFIX + digest, "ResponseContentDisposition": disposition},
            ExpiresIn=LINK_SECONDS,
        )


class FolderBucket:
    """A folder on this machine, for local development; the server streams files from it."""

    def __init__(self, settings: Settings) -> None:
        self._folder = settings.files_dir

    def path(self, digest: str) -> Path:
        """Where a file lives; ``digest`` comes from a URL, so anything but a SHA-256 hex digest is refused."""
        if DIGEST.fullmatch(digest) is None:
            raise exceptions.FileNotFound("(any)", digest)
        return self._folder / digest[:2] / digest

    async def put(self, digest: str, content: bytes, content_type: str | None) -> None:
        path = self.path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)

    async def link(self, digest: str, name: str) -> str:
        return f"/files/raw/{digest}/{urllib.parse.quote(name)}"
