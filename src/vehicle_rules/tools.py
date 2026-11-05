"""The tools the model can call: what it sees of each, and the function behind it.

The model sees a tool's name, description and parameters, in the Chat Completions format. It never runs anything:
it asks for a call, and the agent runs the tool and returns the result as text. A tool reports a problem it can
foresee in that result, so that the model can correct its call or say what went wrong.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient
from databricks_mcp import DatabricksMCPClient

from vehicle_rules.config import ProjectConfig
from vehicle_rules.search import article_chunks, embed_question, semantic

# Search whole articles. This is a design choice, not a result of week 2: see docs/week-3.md.
SEARCH_GRANULARITY = "article"
DEFAULT_VEHICLE_TYPE = "Passenger cars"
DEFAULT_NUM_RESULTS = 5
MAX_NUM_RESULTS = 10


@dataclass(frozen=True)
class Tool:
    """A tool of the agent: its name, what the model sees, and the function that runs it."""

    name: str
    spec: dict[str, Any]
    run: Callable[..., str]


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Return a tool's definition in the Chat Completions format."""
    parameters = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


SEARCH_SPEC = _spec(
    "search_regulations",
    "Search the Dutch vehicle regulation by meaning, for the articles about a topic. Use it for any question about "
    "a rule, in the regulation's terms where you know them. It returns the best articles with their text. Do not "
    "use it for an article you know the number of: use get_article.",
    {
        "query": {"type": "string", "description": "What to search for, in English."},
        "num_results": {
            "type": "integer",
            "description": f"How many chunks to return, 1 to {MAX_NUM_RESULTS}; {DEFAULT_NUM_RESULTS} if left out.",
        },
    },
    ["query"],
)

ARTICLE_SPEC = _spec(
    "get_article",
    "Read one article of the regulation in full, by its number. Use it for an article the user names, or one that "
    'another article refers to, such as "Annex VIII, Article 40". Not for the definitions of article 1.1: find those '
    "with search_regulations.",
    {
        "article": {"type": "string", "description": 'The number alone, such as "5.2.47" or "40".'},
        "annex": {"type": "string", "description": 'The annex, such as "VIII". Leave it out outside the annexes.'},
    },
    ["article"],
)


def _reference(chunk: dict[str, Any]) -> str:
    """Name a chunk: "Article 5.2.47", "Annex VIII, Article 40", or a definition of article 1.1 by its Dutch term."""
    annex = f"Annex {chunk['annex']}, " if chunk["annex"] else ""
    definition = f", definition: {chunk['paragraph']}" if chunk["paragraph"] else ""
    return f"{annex}Article {chunk['article']}{definition}"


def _error(message: str) -> str:
    """Return a problem as a tool result."""
    return json.dumps({"error": message})


def genie_tools(config: ProjectConfig, workspace: WorkspaceClient) -> list[Tool]:
    """Return the tools of the Genie space, as its MCP server defines them: one to ask, one to wait for the answer."""
    url = f"{workspace.config.host.rstrip('/')}/api/2.0/mcp/genie/{config.genie_space_id}"
    client = DatabricksMCPClient(server_url=url, workspace_client=workspace)

    def tool(definition: Any) -> Tool:  # noqa: ANN401
        """Adapt an MCP tool definition to a Tool."""

        def run(**arguments: Any) -> str:  # noqa: ANN401
            response = client.call_tool(definition.name, arguments)
            text = "".join(block.text for block in response.content)
            return _error(text) if response.isError else text

        function = {
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.inputSchema,
        }
        return Tool(definition.name, {"type": "function", "function": function}, run)

    return [tool(definition) for definition in client.list_tools()]


def make_tools(
    conn: psycopg.Connection,
    config: ProjectConfig,
    workspace: WorkspaceClient,
    vehicle_type: str = DEFAULT_VEHICLE_TYPE,
) -> list[Tool]:
    """Return the tools for one conversation. Code sets the vehicle type, so the model cannot change it."""

    def search_regulations(query: str, num_results: int = DEFAULT_NUM_RESULTS) -> str:
        """Return the chunks closest to the query, keeping those of this vehicle type and of any vehicle."""
        if not query.strip():
            return _error("The query is empty.")
        if not 1 <= num_results <= MAX_NUM_RESULTS:
            return _error(f"num_results must be between 1 and {MAX_NUM_RESULTS}.")
        embedding = embed_question(config, workspace, query)
        chunks = semantic(conn, config, embedding, SEARCH_GRANULARITY, vehicle_type=vehicle_type, limit=num_results)
        results = [
            {"reference": _reference(c), "score": round(c["vector_score"], 4), "text": c["text"]} for c in chunks
        ]
        return json.dumps(results, ensure_ascii=False)

    def get_article(article: str, annex: str | None = None) -> str:
        """Return an article. Only article 1.1 is several chunks, one per definition: too long to return whole."""
        chunks = article_chunks(conn, config, article, annex)
        if not chunks:
            hint = f" in annex {annex}" if annex else ". If it is in an annex, give the annex too"
            return _error(f"There is no translated article {article}{hint}.")
        if len(chunks) > 1:
            return _error(f"Article {article} is too long to read whole. Use search_regulations to find a definition.")
        return json.dumps({"reference": _reference(chunks[0]), "text": chunks[0]["text"]}, ensure_ascii=False)

    return [
        Tool("search_regulations", SEARCH_SPEC, search_regulations),
        Tool("get_article", ARTICLE_SPEC, get_article),
    ]
