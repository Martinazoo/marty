"""Minimal async CalDAV client (RFC 4791) for iCloud, on top of httpx.

Only what MARTY needs: discover the calendar home, manage calendars, query
events by time range, fetch one by URL, and write events with ETag preconditions so a
change made meanwhile on another device is never overwritten silently.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urljoin
from uuid import uuid4
from xml.sax.saxutils import escape

import httpx

logger = logging.getLogger(__name__)

NS = {
    "d": "DAV:",
    "c": "urn:ietf:params:xml:ns:caldav",
    "a": "http://apple.com/ns/ical/",
    "cs": "http://calendarserver.org/ns/",
}
_XMLNS = (
    'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
    'xmlns:a="http://apple.com/ns/ical/" xmlns:cs="http://calendarserver.org/ns/"'
)


class CalendarError(Exception):
    """Base class; messages never contain credentials."""


class CalendarUnavailableError(CalendarError):
    """Network failure, timeout, server error or rejected credentials."""


class CalendarConflictError(CalendarError):
    """The object changed on the server since it was read (HTTP 412)."""


class CalendarRequestError(CalendarError):
    """The server refused the request (for example deleting a protected calendar)."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class RemoteCalendar:
    url: str
    name: str
    color: str | None
    components: tuple[str, ...]


@dataclass(frozen=True)
class RemoteObject:
    url: str
    etag: str | None
    ics: str


def _ical_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


class CalDAVClient:
    def __init__(
        self,
        *,
        url: str,
        username: str,
        password: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._url = url
        self._auth = httpx.BasicAuth(username, password)
        self._client = client
        self._timeout = timeout_seconds
        self._home: str | None = None

    # --- Transport ---------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...],
    ) -> httpx.Response:
        all_headers = {"Content-Type": "application/xml; charset=utf-8", **(headers or {})}
        try:
            response = await self._client.request(
                method,
                url,
                content=body.encode() if body is not None else None,
                headers=all_headers,
                auth=self._auth,
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise CalendarUnavailableError(f"CalDAV {method}: {type(exc).__name__}") from exc
        status = response.status_code
        if status in expected:
            return response
        logger.info("CalDAV request failed", extra={"method": method, "status": status})
        if status == 401:
            raise CalendarUnavailableError("CalDAV: credentials rejected")
        if status == 412:
            raise CalendarConflictError("CalDAV: the object changed on the server")
        if status >= 500:
            raise CalendarUnavailableError(f"CalDAV {method}: HTTP {status}")
        raise CalendarRequestError(f"CalDAV {method}: HTTP {status}", status)

    async def _propfind(self, url: str, props: str, depth: str) -> tuple[ET.Element, str]:
        body = f'<?xml version="1.0"?><d:propfind {_XMLNS}><d:prop>{props}</d:prop></d:propfind>'
        response = await self._request(
            "PROPFIND", url, body=body, headers={"Depth": depth}, expected=(207,)
        )
        return self._xml(response), str(response.url)

    async def _report(self, url: str, body: str) -> list[RemoteObject]:
        response = await self._request(
            "REPORT", url, body=body, headers={"Depth": "1"}, expected=(207,)
        )
        base = str(response.url)
        objects = []
        for item in self._xml(response).findall("d:response", NS):
            data = item.findtext(".//c:calendar-data", namespaces=NS)
            href = item.findtext("d:href", namespaces=NS)
            if data and href:
                etag = item.findtext(".//d:getetag", namespaces=NS)
                objects.append(RemoteObject(urljoin(base, href), etag, data))
        return objects

    @staticmethod
    def _xml(response: httpx.Response) -> ET.Element:
        try:
            return ET.fromstring(response.content)  # noqa: S314 - trusted CalDAV server over TLS
        except ET.ParseError as exc:
            raise CalendarUnavailableError("CalDAV: malformed XML response") from exc

    # --- Discovery and calendars ---------------------------------------------------

    async def home(self) -> str:
        if self._home is None:
            root, base = await self._propfind(self._url, "<d:current-user-principal/>", "0")
            principal = root.findtext(".//d:current-user-principal/d:href", namespaces=NS)
            if not principal:
                raise CalendarUnavailableError("CalDAV: no current-user-principal")
            root, base = await self._propfind(
                urljoin(base, principal), "<c:calendar-home-set/>", "0"
            )
            home = root.findtext(".//c:calendar-home-set/d:href", namespaces=NS)
            if not home:
                raise CalendarUnavailableError("CalDAV: no calendar-home-set")
            self._home = urljoin(base, home)
        return self._home

    async def calendars(self) -> list[RemoteCalendar]:
        root, base = await self._propfind(
            await self.home(),
            "<d:displayname/><d:resourcetype/><a:calendar-color/>"
            "<c:supported-calendar-component-set/>",
            "1",
        )
        calendars = []
        for item in root.findall("d:response", NS):
            if item.find(".//d:resourcetype/c:calendar", NS) is None:
                continue
            components = tuple(
                comp.get("name", "")
                for comp in item.findall(".//c:supported-calendar-component-set/c:comp", NS)
            )
            calendars.append(
                RemoteCalendar(
                    url=urljoin(base, item.findtext("d:href", default="", namespaces=NS)),
                    name=item.findtext(".//d:displayname", default="", namespaces=NS),
                    color=item.findtext(".//a:calendar-color", namespaces=NS),
                    components=components,
                )
            )
        return calendars

    async def create_calendar(self, name: str, color: str) -> str:
        url = urljoin(await self.home(), f"{uuid4()}/")
        body = (
            f'<?xml version="1.0" encoding="utf-8"?><c:mkcalendar {_XMLNS}><d:set><d:prop>'
            f"<d:displayname>{escape(name)}</d:displayname>"
            f"<a:calendar-color>{escape(color)}</a:calendar-color>"
            '<c:supported-calendar-component-set><c:comp name="VEVENT"/>'
            "</c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>"
        )
        await self._request("MKCALENDAR", url, body=body, expected=(201,))
        return url

    async def update_calendar(
        self, url: str, *, name: str | None = None, color: str | None = None
    ) -> None:
        props = ""
        if name is not None:
            props += f"<d:displayname>{escape(name)}</d:displayname>"
        if color is not None:
            props += f"<a:calendar-color>{escape(color)}</a:calendar-color>"
        if not props:
            return
        body = (
            f'<?xml version="1.0" encoding="utf-8"?><d:propertyupdate {_XMLNS}>'
            f"<d:set><d:prop>{props}</d:prop></d:set></d:propertyupdate>"
        )
        response = await self._request("PROPPATCH", url, body=body, expected=(207,))
        statuses = [s.text or "" for s in self._xml(response).iter("{DAV:}status")]
        if any(" 200 " not in s for s in statuses):
            raise CalendarRequestError("CalDAV PROPPATCH: property rejected", 403)

    async def delete_calendar(self, url: str) -> None:
        await self._request("DELETE", url, expected=(200, 204))

    # --- Events ----------------------------------------------------------------

    async def events_between(
        self, calendar_url: str, start: datetime, end: datetime
    ) -> list[RemoteObject]:
        """Objects with at least one occurrence in [start, end) (the server expands
        recurrences for the filter; the caller expands them for display)."""
        body = (
            f'<?xml version="1.0"?><c:calendar-query {_XMLNS}>'
            "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{_ical_utc(start)}" end="{_ical_utc(end)}"/>'
            "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
        )
        return await self._report(calendar_url, body)

    async def get_object(self, url: str) -> RemoteObject | None:
        """Fetch one event by URL (iCloud rejects UID filters, so events are
        addressed by URL)."""
        try:
            response = await self._request("GET", url, expected=(200,))
        except CalendarRequestError as exc:
            if exc.status in (404, 410):
                return None
            raise
        return RemoteObject(url, response.headers.get("ETag"), response.text)

    async def count(self, calendar_url: str) -> int:
        root, _ = await self._propfind(calendar_url, "<d:getetag/>", "1")
        return max(len(root.findall("d:response", NS)) - 1, 0)

    async def create_object(self, calendar_url: str, uid: str, ics: str) -> RemoteObject:
        url = urljoin(calendar_url, quote(uid.split("@")[0]) + ".ics")
        response = await self._request(
            "PUT",
            url,
            body=ics,
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
            expected=(201, 204),
        )
        return RemoteObject(url, response.headers.get("ETag"), ics)

    async def update_object(self, current: RemoteObject, ics: str) -> RemoteObject:
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if current.etag:
            headers["If-Match"] = current.etag
        response = await self._request(
            "PUT", current.url, body=ics, headers=headers, expected=(200, 201, 204)
        )
        return RemoteObject(current.url, response.headers.get("ETag"), ics)

    async def delete_object(self, current: RemoteObject) -> None:
        headers = {"If-Match": current.etag} if current.etag else {}
        await self._request("DELETE", current.url, headers=headers, expected=(200, 204))
