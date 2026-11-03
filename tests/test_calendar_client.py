from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx
import pytest

from app.integrations.apple_calendar import (
    CalDAVClient,
    CalendarConflictError,
    CalendarRequestError,
    CalendarUnavailableError,
    RemoteObject,
)

BASE = "https://caldav.test"
HOME = f"{BASE}/123/calendars/"
PASSWORD = "app-specific-secret"


def multistatus(*responses: str) -> str:
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
        'xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:a="http://apple.com/ns/ical/">'
        + "".join(responses)
        + "</d:multistatus>"
    )


def prop_response(href: str, props: str, status: str = "HTTP/1.1 200 OK") -> str:
    return (
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>"
        f"<d:status>{status}</d:status></d:propstat></d:response>"
    )


DISCOVERY = {
    ("PROPFIND", "/"): multistatus(
        prop_response(
            "/",
            "<d:current-user-principal><d:href>/123/principal/</d:href></d:current-user-principal>",
        )
    ),
    ("PROPFIND", "/123/principal/"): multistatus(
        prop_response(
            "/123/principal/",
            "<c:calendar-home-set><d:href>/123/calendars/</d:href></c:calendar-home-set>",
        )
    ),
}


class Server:
    """Answers by (method, path); records every request."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response | str]) -> None:
        self.routes = {**DISCOVERY, **routes}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get((request.method, request.url.path))
        if answer is None:
            return httpx.Response(404)
        if isinstance(answer, str):
            return httpx.Response(207, text=answer)
        return answer

    def sent(self, method: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method]


async def test_discovers_home_once_and_lists_only_calendars():
    calendar_props = (
        "<d:displayname>{name}</d:displayname><d:resourcetype><d:collection/><c:calendar/>"
        "</d:resourcetype><a:calendar-color>#FF9500FF</a:calendar-color>"
        '<c:supported-calendar-component-set><c:comp name="{comp}"/>'
        "</c:supported-calendar-component-set>"
    )
    server = Server(
        {
            ("PROPFIND", "/123/calendars/"): multistatus(
                prop_response(
                    "/123/calendars/", "<d:resourcetype><d:collection/></d:resourcetype>"
                ),
                prop_response(
                    "/123/calendars/feina/", calendar_props.format(name="Feina", comp="VEVENT")
                ),
                prop_response(
                    "/123/calendars/tasks/", calendar_props.format(name="Tareas", comp="VTODO")
                ),
                prop_response(
                    "/123/calendars/inbox/", "<d:resourcetype><c:schedule-inbox/></d:resourcetype>"
                ),
            )
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        first = await client.calendars()
        await client.calendars()

    assert [(c.name, c.url, c.color, c.components) for c in first] == [
        ("Feina", f"{HOME}feina/", "#FF9500FF", ("VEVENT",)),
        ("Tareas", f"{HOME}tasks/", "#FF9500FF", ("VTODO",)),
    ]
    assert len(server.sent("PROPFIND")) == 4  # two discovery steps, then two listings
    assert server.requests[0].headers["Authorization"].startswith("Basic ")


async def test_events_between_sends_a_utc_time_range_and_resolves_hrefs():
    server = Server(
        {
            ("REPORT", "/123/calendars/feina/"): multistatus(
                prop_response(
                    "/123/calendars/feina/a.ics",
                    '<d:getetag>"e1"</d:getetag><c:calendar-data>BEGIN:VCALENDAR</c:calendar-data>',
                ),
                prop_response("/123/calendars/feina/empty.ics", '<d:getetag>"e2"</d:getetag>'),
            )
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        objects = await client.events_between(
            f"{HOME}feina/",
            datetime(2026, 11, 9, 0, 0, tzinfo=UTC),
            datetime(2026, 11, 16, 0, 0, tzinfo=UTC),
        )

    assert objects == [RemoteObject(f"{HOME}feina/a.ics", '"e1"', "BEGIN:VCALENDAR")]
    (report,) = server.sent("REPORT")
    assert report.headers["Depth"] == "1"
    body = ET.fromstring(report.content)  # noqa: S314 - XML built by our own client
    time_range = body.find(".//{urn:ietf:params:xml:ns:caldav}time-range")
    assert time_range.attrib == {"start": "20261109T000000Z", "end": "20261116T000000Z"}


async def test_writes_use_etag_preconditions():
    url = "/123/calendars/feina/abc.ics"
    server = Server(
        {
            ("PUT", url): httpx.Response(201, headers={"ETag": '"v1"'}),
            ("DELETE", url): httpx.Response(204),
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        created = await client.create_object(f"{HOME}feina/", "abc@marty", "ICS")
        await client.update_object(created, "ICS2")
        await client.delete_object(created)

    assert created == RemoteObject(f"{BASE}{url}", '"v1"', "ICS")
    create, update = server.sent("PUT")
    assert create.headers["If-None-Match"] == "*"
    assert create.headers["Content-Type"].startswith("text/calendar")
    assert update.headers["If-Match"] == '"v1"'
    assert server.sent("DELETE")[0].headers["If-Match"] == '"v1"'


async def test_changed_object_raises_conflict():
    server = Server({("PUT", "/123/calendars/feina/abc.ics"): httpx.Response(412)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        with pytest.raises(CalendarConflictError):
            await client.update_object(RemoteObject(f"{HOME}feina/abc.ics", '"old"', "ICS"), "X")


async def test_get_object_returns_none_when_missing():
    url = "/123/calendars/feina/abc.ics"
    server = Server({("GET", url): httpx.Response(200, text="ICS", headers={"ETag": '"v3"'})})
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        found = await client.get_object(f"{BASE}{url}")
        missing = await client.get_object(f"{HOME}feina/gone.ics")

    assert found == RemoteObject(f"{BASE}{url}", '"v3"', "ICS")
    assert missing is None


async def test_failures_are_classified_without_leaking_credentials():
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        with pytest.raises(CalendarUnavailableError) as offline:
            await client.calendars()

    server = Server({("DELETE", "/123/calendars/last/"): httpx.Response(403)})
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        with pytest.raises(CalendarRequestError) as refused:
            await client.delete_calendar(f"{HOME}last/")

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unauthorized)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        with pytest.raises(CalendarUnavailableError) as denied:
            await client.calendars()

    assert refused.value.status == 403
    for error in (offline.value, refused.value, denied.value):
        assert PASSWORD not in str(error)


async def test_rejected_property_update_is_an_error():
    server = Server(
        {
            ("PROPPATCH", "/123/calendars/feina/"): multistatus(
                prop_response("/123/calendars/feina/", "<d:displayname/>", "HTTP/1.1 403 Forbidden")
            )
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as http:
        client = CalDAVClient(url=f"{BASE}/", username="me", password=PASSWORD, client=http)
        with pytest.raises(CalendarRequestError):
            await client.update_calendar(f"{HOME}feina/", name="Treball")
