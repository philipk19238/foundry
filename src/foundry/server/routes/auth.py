from typing import Annotated

import svcs
import svcs.fastapi
from fastapi import APIRouter, Depends, Form, Header, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from foundry.app import exceptions
from foundry.app.auth.auth import Logins
from foundry.app.auth.models import Token, UserView

COOKIE = "foundry_login"

router = APIRouter()


async def signed_in(
    request: Request,
    services: svcs.fastapi.DepContainer,
    authorization: Annotated[str | None, Header()] = None,
) -> svcs.Container:
    """Every route but sign-in goes through this: a login cookie (the UI) or a bearer API token (scripts)."""
    logins = await services.aget(Logins)
    if authorization is not None and authorization.lower().startswith("bearer "):
        user = await logins.by_token(Token(value=authorization.split(" ", 1)[1].strip()))
    elif (cookie := request.cookies.get(COOKIE)) is not None:
        user = await logins.by_login(Token(value=cookie))
    else:
        raise exceptions.NotSignedIn()
    services.register_local_value(UserView, user)
    return services


async def admin(services: Annotated[svcs.Container, Depends(signed_in)]) -> svcs.Container:
    user = await services.aget(UserView)
    if not user.is_admin:
        raise exceptions.NotAllowed("add sources or start runs")
    return services


SignedIn = Annotated[svcs.Container, Depends(signed_in)]
Admin = Annotated[svcs.Container, Depends(admin)]


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, services: svcs.fastapi.DepContainer, next: str = "/") -> Response:
    templates = await services.aget(Jinja2Templates)
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@router.post("/login")
async def login(
    request: Request,
    services: svcs.fastapi.DepContainer,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
) -> Response:
    logins, templates = await services.aget(Logins, Jinja2Templates)
    try:
        token = await logins.sign_in(email, password)
    except exceptions.WrongPassword as error:
        context = {"next": next, "error": str(error)}
        return templates.TemplateResponse(request, "login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)
    local = next.startswith("/") and not next.startswith("//")
    response = RedirectResponse(next if local else "/", status_code=status.HTTP_303_SEE_OTHER)
    secure = request.url.scheme == "https"
    response.set_cookie(COOKIE, token.value, httponly=True, secure=secure, samesite="lax", max_age=14 * 86400)
    return response


@router.post("/logout")
async def logout(request: Request, services: SignedIn) -> Response:
    logins = await services.aget(Logins)
    if (cookie := request.cookies.get(COOKIE)) is not None:
        await logins.sign_out(Token(value=cookie))
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE)
    return response
