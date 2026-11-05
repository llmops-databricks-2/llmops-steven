"""One turn of the agent, on a fake model and fake tools."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from vehicle_rules.agent import MAX_ITERATIONS, UNFINISHED, VehicleRulesAgent
from vehicle_rules.tools import Tool


def call(name: str, arguments: str = "{}", call_id: str = "1") -> SimpleNamespace:
    """Return a tool call as the model makes it."""
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def reply(content: str | None = None, *calls: SimpleNamespace) -> MagicMock:
    """Return the response of the model."""
    return MagicMock(choices=[MagicMock(message=SimpleNamespace(content=content, tool_calls=list(calls)))])


def agent(*replies: MagicMock, run: MagicMock | None = None, car: dict | None = None) -> VehicleRulesAgent:
    """Return an agent whose model gives the replies, with one tool, "search"."""
    client = MagicMock()
    client.chat.completions.create.side_effect = replies
    tool = Tool("search", {"function": {"name": "search"}}, run or MagicMock(return_value="found"))
    return VehicleRulesAgent(client, MagicMock(llm_endpoint="m", system_prompt="rules"), [tool], lambda _: car)


def tool_contents(turn: object) -> list[str]:
    """Return the results of the tools in a turn."""
    return [m["content"] for m in turn.messages if m["role"] == "tool"]


def test_a_direct_answer_needs_no_tool() -> None:
    """The model may answer at once."""
    turn = agent(reply("Hello.")).chat("Hi")
    assert turn.answer == "Hello."
    assert [m["role"] for m in turn.messages] == ["user", "assistant"]


def test_a_tool_result_follows_its_call_and_goes_back_to_the_model() -> None:
    """Each result carries the ID of its call."""
    bot = agent(reply(None, call("search", '{"query": "belts"}', "a")), reply("Done."))
    turn = bot.chat("Seat belts?")
    assert turn.answer == "Done."
    assert [m["role"] for m in turn.messages] == ["user", "assistant", "tool", "assistant"]
    assert turn.messages[1]["tool_calls"][0]["id"] == turn.messages[2]["tool_call_id"] == "a"
    sent = bot.client.chat.completions.create.call_args.kwargs["messages"]
    assert sent[0] == {"role": "system", "content": "rules"}
    assert sent[-1]["content"] == "found"


def test_each_of_several_calls_gets_a_result() -> None:
    """One reply can ask for more than one tool."""
    turn = agent(reply(None, call("search", call_id="a"), call("search", call_id="b")), reply("Done.")).chat("Q")
    assert [m["tool_call_id"] for m in turn.messages if m["role"] == "tool"] == ["a", "b"]


def test_the_limit_stops_a_model_that_keeps_calling_tools() -> None:
    """It says that it could not finish, rather than presenting a partial answer."""
    bot = agent(*[reply(None, call("search")) for _ in range(MAX_ITERATIONS)])
    turn = bot.chat("Q")
    assert turn.answer == UNFINISHED
    assert bot.client.chat.completions.create.call_count == MAX_ITERATIONS


def test_nothing_the_model_asks_for_can_crash_a_turn() -> None:
    """An unknown tool, bad JSON, wrong arguments and a failing tool all return an error to the model."""
    failing = MagicMock(side_effect=RuntimeError("database down"))
    calls = [call("nope", call_id="a"), call("search", "{not json", "b"), call("search", '{"q": 1}', "c")]
    turn = agent(reply(None, *calls), reply("Sorry."), run=failing).chat("Q")
    assert turn.answer == "Sorry."
    errors = [json.loads(content)["error"] for content in tool_contents(turn)]
    assert "no tool called nope" in errors[0]
    assert "not valid JSON" in errors[1]
    assert "database down" in errors[2]


def test_a_plate_puts_the_car_before_the_question() -> None:
    """Code looks it up, without the model choosing to."""
    turn = agent(reply("Yes."), car={"make": "VOLVO"}).chat("Is AB-123-C allowed?")
    assert "VOLVO" in turn.messages[0]["content"]
    assert turn.messages[0]["role"] == "system"
    assert turn.messages[1]["role"] == "user"


def test_an_unknown_plate_is_stated() -> None:
    """The model is told, so that it does not guess."""
    turn = agent(reply("Unknown car.")).chat("Is AB-123-C allowed?")
    assert "not known" in turn.messages[0]["content"]


def test_no_plate_means_no_lookup() -> None:
    """Only the question goes to the model."""
    lookup = MagicMock()
    bot = agent(reply("In general…"))
    bot.lookup = lookup
    bot.chat("Do cars need seat belts?")
    lookup.assert_not_called()


def test_history_comes_before_the_new_message() -> None:
    """A later turn sees the earlier ones, and only the new messages come back to be stored."""
    bot = agent(reply("Yes."))
    earlier = [{"role": "user", "content": "First"}, {"role": "assistant", "content": "Answer"}]
    turn = bot.chat("Second", earlier)
    sent = bot.client.chat.completions.create.call_args.kwargs["messages"]
    assert [m["content"] for m in sent] == ["rules", "First", "Answer", "Second"]
    assert all(m not in turn.messages for m in earlier)
