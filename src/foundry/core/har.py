"""Bronze in HAR 1.2, which any HAR viewer (a browser's network panel among them) opens."""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel


class HarModel(BaseModel):
    """A part of a HAR 1.2 document. HAR's names are camelCase; fields here are snake_case and read and written by
    HAR's names."""

    model_config = ConfigDict(frozen=True, alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


class HarPair(HarModel):
    """A name and its value: a header, a query parameter, a cookie."""

    name: str
    value: str


class HarPostData(HarModel):
    mime_type: str
    text: str


class HarRequest(HarModel):
    method: str
    url: str
    http_version: str
    headers: tuple[HarPair, ...]
    query_string: tuple[HarPair, ...]
    cookies: tuple[HarPair, ...] = ()
    headers_size: int = -1
    body_size: int
    post_data: HarPostData | None = None


class HarContent(HarModel):
    """A response body: its size and type, `_sha256` (the name of the file it is kept in, beside the HAR), and `text`
    inline only for a text body that isn't too big to read in a viewer."""

    size: int
    mime_type: str
    sha256: str = Field(alias="_sha256")
    text: str | None = None


class HarResponse(HarModel):
    status: int
    status_text: str
    http_version: str
    headers: tuple[HarPair, ...]
    cookies: tuple[HarPair, ...] = ()
    content: HarContent
    redirect_url: str = Field(default="", alias="redirectURL")
    headers_size: int = -1
    body_size: int


class HarTimings(HarModel):
    """Milliseconds. A crawl knows only how long a response took as a whole, so all of it is `wait`."""

    send: float = 0
    wait: float
    receive: float = 0


class HarEntry(HarModel):
    """One request a crawl sent and the response it got, as HAR 1.2 has it."""

    started_date_time: dt.datetime
    time: float
    request: HarRequest
    response: HarResponse
    cache: dict[str, JsonValue] = {}
    timings: HarTimings


class HarCreator(HarModel):
    name: str = "foundry"
    version: str = "1"


class HarLog(HarModel):
    version: str = "1.2"
    creator: HarCreator = HarCreator()
    entries: tuple[HarEntry, ...]


class Har(HarModel):
    """A HAR 1.2 document, which any HAR viewer (a browser's network panel among them) opens."""

    log: HarLog
