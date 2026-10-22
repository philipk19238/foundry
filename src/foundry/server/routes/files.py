"""Attachments, for anyone signed in: a stored file redirects to a short-lived link from its bucket; one that never
downloaded redirects to the portal's own URL."""

from fastapi import APIRouter, status
from fastapi.responses import FileResponse, RedirectResponse, Response

from foundry.app import exceptions
from foundry.app.catalog.catalog import Opportunities
from foundry.app.files.client import FolderBucket
from foundry.app.files.models import Bucket
from foundry.server.routes.auth import SignedIn

router = APIRouter(prefix="/files")


@router.get("/{notice_id}/{file_id}")
async def download(notice_id: str, file_id: str, services: SignedIn) -> Response:
    opportunities, bucket = await services.aget(Opportunities, Bucket)
    file = await opportunities.file(notice_id, file_id)
    url = file.url if file.sha256 is None else await bucket.link(file.sha256, file.name)
    return RedirectResponse(url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/raw/{digest}/{name}")
async def raw(digest: str, name: str, services: SignedIn) -> Response:
    """The local folder bucket's files; with R2 the links go to R2 instead."""
    bucket = await services.aget(Bucket)
    if not isinstance(bucket, FolderBucket) or not bucket.path(digest).is_file():
        raise exceptions.FileNotFound("(any)", digest)
    return FileResponse(bucket.path(digest), filename=name)
