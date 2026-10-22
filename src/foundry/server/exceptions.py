from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from foundry.app import exceptions

_STATUS = (
    (exceptions.NotSignedIn, status.HTTP_401_UNAUTHORIZED),
    (exceptions.WrongPassword, status.HTTP_401_UNAUTHORIZED),
    (exceptions.NotAllowed, status.HTTP_403_FORBIDDEN),
    (exceptions.SourceNotFound, status.HTTP_404_NOT_FOUND),
    (exceptions.RunNotFound, status.HTTP_404_NOT_FOUND),
    (exceptions.HelpNotFound, status.HTTP_404_NOT_FOUND),
    (exceptions.FileNotFound, status.HTTP_404_NOT_FOUND),
    (exceptions.HelpTaken, status.HTTP_409_CONFLICT),
    (exceptions.SourceBusy, status.HTTP_409_CONFLICT),
    (exceptions.NoScraper, status.HTTP_409_CONFLICT),
    (exceptions.Error, status.HTTP_400_BAD_REQUEST),
)


async def _translate(request: Request, error: Exception) -> Response:
    if isinstance(error, exceptions.NotSignedIn) and not request.url.path.startswith("/api/"):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=status.HTTP_303_SEE_OTHER)
    code = next(code for kind, code in _STATUS if isinstance(error, kind))
    return JSONResponse({"detail": str(error)}, status_code=code)


def install(app: FastAPI) -> None:
    for kind, _ in _STATUS:
        app.add_exception_handler(kind, _translate)
