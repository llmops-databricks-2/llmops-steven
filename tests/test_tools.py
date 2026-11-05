"""The model's tools, on a fake connection and embedding."""

import json
from unittest.mock import MagicMock

import pytest

from vehicle_rules import tools
from vehicle_rules.tools import MAX_NUM_RESULTS, genie_tools, make_tools

CHUNK = {"annex": None, "article": "5.2.47", "paragraph": None, "text": "Seat belts.", "vector_score": 0.81234}


@pytest.fixture
def by_name(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Return the tools by name, with the model and the database replaced."""
    monkeypatch.setattr(tools, "embed_question", MagicMock(return_value=[0.1]))
    monkeypatch.setattr(tools, "semantic", MagicMock(return_value=[CHUNK]))
    monkeypatch.setattr(tools, "article_chunks", MagicMock(return_value=[CHUNK]))
    return {tool.name: tool for tool in make_tools(MagicMock(), MagicMock(), MagicMock())}


def test_the_model_sees_a_valid_definition_of_each_tool(by_name: dict) -> None:
    """The name matches, and every required parameter is defined."""
    for name, tool in by_name.items():
        function = tool.spec["function"]
        assert function["name"] == name
        assert function["description"]
        assert set(function["parameters"]["required"]) <= set(function["parameters"]["properties"])


def test_search_returns_references_scores_and_text(by_name: dict) -> None:
    """The search runs at article level, with the vehicle type code set."""
    results = json.loads(by_name["search_regulations"].run("seat belts"))
    assert results == [{"reference": "Article 5.2.47", "score": 0.8123, "text": "Seat belts."}]
    args = tools.semantic.call_args
    assert args.args[3] == "article"
    assert args.kwargs["vehicle_type"] == "Passenger cars"


def test_the_model_cannot_set_the_vehicle_type(by_name: dict) -> None:
    """It is not a parameter of the tool."""
    spec = by_name["search_regulations"].spec["function"]["parameters"]
    assert "vehicle_type" not in spec["properties"]
    assert spec["additionalProperties"] is False


@pytest.mark.parametrize(("query", "num_results"), [("  ", 5), ("seat belts", 0), ("seat belts", MAX_NUM_RESULTS + 1)])
def test_invalid_search_arguments_return_an_error_before_any_model_call(
    by_name: dict, query: str, num_results: int
) -> None:
    """The model gets a reason it can correct, and nothing is embedded."""
    assert "error" in json.loads(by_name["search_regulations"].run(query, num_results))
    tools.embed_question.assert_not_called()


def test_get_article_returns_the_article(by_name: dict) -> None:
    """Its reference and its text."""
    assert json.loads(by_name["get_article"].run("5.2.47")) == {"reference": "Article 5.2.47", "text": "Seat belts."}


def test_an_article_of_many_chunks_points_to_the_search(by_name: dict) -> None:
    """Article 1.1 would be some 40,000 characters in one tool result."""
    tools.article_chunks.return_value = [CHUNK, CHUNK]
    assert "search_regulations" in json.loads(by_name["get_article"].run("1.1"))["error"]


def test_a_definition_is_named_by_its_term(by_name: dict) -> None:
    """Two definitions of article 1.1 must not look like the same result."""
    tools.semantic.return_value = [CHUNK | {"article": "1.1", "paragraph": "aanhangwagen"}]
    result = json.loads(by_name["search_regulations"].run("trailer"))
    assert result[0]["reference"] == "Article 1.1, definition: aanhangwagen"


def test_an_unknown_article_returns_an_error(by_name: dict) -> None:
    """Not found is an answer the model can use, in or outside an annex."""
    tools.article_chunks.return_value = []
    assert "in annex VIII" in json.loads(by_name["get_article"].run("999", "VIII"))["error"]
    assert "give the annex" in json.loads(by_name["get_article"].run("40"))["error"]


@pytest.fixture
def genie(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Return a fake MCP client with one tool, in place of the real one."""
    definition = MagicMock(description="Ask Genie.", inputSchema={"type": "object"})
    definition.name = "query_space"
    client = MagicMock()
    client.list_tools.return_value = [definition]
    monkeypatch.setattr(tools, "DatabricksMCPClient", MagicMock(return_value=client))
    return client


def test_a_genie_tool_keeps_the_definition_of_its_server(genie: MagicMock) -> None:
    """The model sees the name, description and schema that the MCP server gives."""
    tool = genie_tools(MagicMock(), MagicMock(config=MagicMock(host="https://host/")))[0]
    assert tool.spec["function"] == {
        "name": "query_space",
        "description": "Ask Genie.",
        "parameters": {"type": "object"},
    }


GENIE_RESPONSE = {
    "status": "COMPLETED",
    "conversationId": "c",
    "messageId": "m",
    "content": {
        "textAttachments": ["There are 1,234."],
        "queryAttachments": [
            {
                "query": "SELECT 1",
                "statement_response": {
                    "manifest": {"long": "metadata"},
                    "result": {"data_array": [{"values": [{"string_value": "1234"}]}]},
                },
            }
        ],
    },
}


def test_a_genie_answer_keeps_only_what_the_model_needs(genie: MagicMock) -> None:
    """The SQL, rows, words and identifiers, without the statement's metadata."""
    genie.call_tool.return_value = MagicMock(content=[MagicMock(text=json.dumps(GENIE_RESPONSE))], isError=False)
    tool = genie_tools(MagicMock(), MagicMock(config=MagicMock(host="https://host/")))[0]
    assert json.loads(tool.run(query="How many?")) == {
        "status": "COMPLETED",
        "conversationId": "c",
        "messageId": "m",
        "queries": [{"sql": "SELECT 1", "rows": [["1234"]]}],
        "text": ["There are 1,234."],
    }


def test_a_pending_genie_answer_keeps_the_identifiers_to_poll_with(genie: MagicMock) -> None:
    """It has no content yet."""
    pending = {"status": "EXECUTING_QUERY", "conversationId": "c", "messageId": "m"}
    genie.call_tool.return_value = MagicMock(content=[MagicMock(text=json.dumps(pending))], isError=False)
    tool = genie_tools(MagicMock(), MagicMock(config=MagicMock(host="https://host/")))[0]
    assert json.loads(tool.run(query="How many?"))["messageId"] == "m"


def test_a_genie_error_reaches_the_model_as_one(genie: MagicMock) -> None:
    """It is passed on, not parsed as an answer."""
    genie.call_tool.return_value = MagicMock(content=[MagicMock(text="no access")], isError=True)
    tool = genie_tools(MagicMock(), MagicMock(config=MagicMock(host="https://host/")))[0]
    assert json.loads(tool.run(query="How many?")) == {"error": "no access"}
