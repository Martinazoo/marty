from zoneinfo import ZoneInfo

import pytest

from app.agents import ALL_TOOLS
from app.agents.calendar import build_calendar_tools
from app.core.tools import ToolRegistry
from app.services.calendar import CalendarService
from tests.calendar_fakes import FakeBackend

REGISTRY = ToolRegistry(
    (*ALL_TOOLS, *build_calendar_tools(CalendarService(FakeBackend(), ZoneInfo("UTC"))))
)


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


@pytest.mark.parametrize("spec", REGISTRY.specs(), ids=lambda s: s.name)
def test_schemas_are_self_consistent_and_free_of_refs(spec):
    for node in walk(spec.parameters):
        assert "$ref" not in node and "$defs" not in node
        assert not isinstance(node.get("title"), str)  # keyword noise is stripped
        missing = set(node.get("required", ())) - set(node.get("properties", {}))
        assert not missing, f"required but not defined: {missing}"


def test_a_property_called_title_survives():
    (create_event,) = (s for s in REGISTRY.specs() if s.name == "create_event")

    assert create_event.parameters["properties"]["title"]["type"] == "string"
