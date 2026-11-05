"""The agent: fixed steps for the plate around a loop in which the model chooses its tools.

Code finds the plate and looks up the car, so that the facts of a car never depend on what the model chooses. The
model then reads the regulation and applies it. docs/week-3.md has the design. A turn cannot crash on a tool: what
goes wrong in a call goes back to the model as the result of that call.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from databricks.sdk import WorkspaceClient
from openai import OpenAI

from vehicle_rules.config import ProjectConfig
from vehicle_rules.tools import Tool, tool_error
from vehicle_rules.vehicles import find_plate

MAX_ITERATIONS = 10
UNFINISHED = "I could not finish this within the limit of steps, so I have no complete answer. Please ask it again."

Message = dict[str, Any]


@dataclass(frozen=True)
class Turn:
    """What a turn produced: the answer, and the messages to keep for the next turn."""

    answer: str
    messages: list[Message]


def gateway_client(workspace: WorkspaceClient) -> OpenAI:
    """Return an OpenAI client on the AI Gateway, with the current Databricks credential."""
    token = workspace.config.authenticate()["Authorization"].removeprefix("Bearer ")
    return OpenAI(api_key=token, base_url=f"{workspace.config.host.rstrip('/')}/ai-gateway/mlflow/v1")


def car_message(plate: str, car: dict[str, Any] | None) -> Message:
    """Return what the register says about a plate, as a system message: only code writes it."""
    if car is None:
        content = f"Plate {plate} is not in the register, which holds passenger cars only. This car is not known."
    else:
        content = f"Facts of the owner's car, from the vehicle register: {json.dumps(car, ensure_ascii=False)}"
    return {"role": "system", "content": content}


def assistant_message(message: Any) -> Message:  # noqa: ANN401
    """Return a model's reply as a plain message, which can be sent back and stored."""
    calls = [
        {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
        for c in message.tool_calls or []
    ]
    return {"role": "assistant", "content": message.content} | ({"tool_calls": calls} if calls else {})


class VehicleRulesAgent:
    """Answer one turn of a conversation about the rules for a car."""

    def __init__(
        self,
        client: OpenAI,
        config: ProjectConfig,
        tools: list[Tool],
        lookup: Callable[[str], dict[str, Any] | None],
    ) -> None:
        self.client = client
        self.model = config.llm_endpoint
        self.system_prompt = config.system_prompt
        self.tools = {tool.name: tool for tool in tools}
        self.lookup = lookup

    def chat(self, message: str, history: Iterable[Message] = ()) -> Turn:
        """Answer a message after the earlier messages of its session; return the answer and the new messages."""
        new = []
        if plate := find_plate(message):
            new.append(car_message(plate, self.lookup(plate)))
        new.append({"role": "user", "content": message})
        earlier = [{"role": "system", "content": self.system_prompt}, *history]

        for _ in range(MAX_ITERATIONS):
            reply = self._ask([*earlier, *new])
            new.append(assistant_message(reply))
            if not reply.tool_calls:
                return Turn(reply.content or "", new)
            new += [self._result(call) for call in reply.tool_calls]

        new.append({"role": "assistant", "content": UNFINISHED})
        return Turn(UNFINISHED, new)

    def _ask(self, messages: list[Message]) -> Any:  # noqa: ANN401
        """Return the model's reply, which is an answer or a request for tool calls."""
        specs = [tool.spec for tool in self.tools.values()]
        return self.client.chat.completions.create(model=self.model, messages=messages, tools=specs).choices[0].message

    def _result(self, call: Any) -> Message:  # noqa: ANN401
        """Run a tool call and return its result, which is an error if anything went wrong."""
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "content": self._run(call.function.name, call.function.arguments),
        }

    def _run(self, name: str, arguments: str) -> str:
        """Run a tool by name, with arguments that come from the model and are not to be trusted."""
        if name not in self.tools:
            return tool_error(f"There is no tool called {name}.")
        try:
            return self.tools[name].run(**json.loads(arguments or "{}"))
        except json.JSONDecodeError:
            return tool_error(f"The arguments of {name} are not valid JSON.")
        except Exception as error:
            return tool_error(f"{name} failed: {error}")
