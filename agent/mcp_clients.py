"""MCP client wiring for both servers, with legible connection failures.

Failure scenario B depends on this module: when Playwright MCP cannot start —
wrong command, missing Node, broken config — the agent must say so in one clear
sentence, not emit a traceback from inside a task group.
"""

import json
import os
import re
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "mcp_config.json"

_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


class MCPConnectionError(RuntimeError):
    """A configured MCP server could not be loaded, started, or initialized."""


def _expand(value: str) -> str:
    defaults = {"REPO_ROOT": str(REPO_ROOT)}
    return _PLACEHOLDER.sub(
        lambda m: os.environ.get(m.group(1), defaults.get(m.group(1), "")), value
    )


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict:
    """Read the MCP configuration and expand ${ENV_VAR} placeholders."""
    path = Path(path)
    if not path.exists():
        raise MCPConnectionError(
            f"MCP configuration not found at {path}. It should sit at the repository "
            "root and list both the playwright and kyiv-strikes servers."
        )
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConnectionError(
            f"MCP configuration at {path} is not valid JSON: {exc}"
        ) from exc

    servers = config.get("mcpServers")
    if not servers:
        raise MCPConnectionError(f"MCP configuration at {path} declares no servers.")

    for name, server in servers.items():
        if "command" not in server:
            raise MCPConnectionError(f"Server {name!r} declares no command to run.")
        server["env"] = {k: _expand(v) for k, v in server.get("env", {}).items()}
    return config


def _connections(config: dict) -> dict:
    """Shape the config the way langchain-mcp-adapters expects."""
    connections = {}
    for name, server in config["mcpServers"].items():
        # Drop placeholders that expanded to nothing: an empty value would
        # override the server's own default and crash it at startup.
        declared = {k: v for k, v in server.get("env", {}).items() if v}
        env = dict(os.environ) | declared
        command = server["command"]
        if command == "python":
            command = sys.executable
        connections[name] = {
            "command": command,
            "args": server.get("args", []),
            "env": env,
            "cwd": str(REPO_ROOT),
            "transport": server.get("transport", "stdio"),
        }
    return connections


async def build_client(config_path: str | Path = DEFAULT_CONFIG) -> MultiServerMCPClient:
    """Create a client for both servers and verify each one actually starts."""
    config = load_config(config_path)
    client = MultiServerMCPClient(_connections(config))

    for name in config["mcpServers"]:
        try:
            await client.get_tools(server_name=name)
        except Exception as exc:
            raise MCPConnectionError(
                f"MCP server {name!r} failed to start.\n"
                f"  cause: {type(exc).__name__}: {exc}\n"
                f"  check: is its command installed, and is mcp_config.json correct?"
            ) from exc
    return client


@asynccontextmanager
async def open_tools(config_path: str | Path = DEFAULT_CONFIG):
    """Open one long-lived session per server and yield {tool_name: tool}.

    This must be a held-open session rather than the convenience path that
    loads tools directly from the client. That path opens a fresh connection
    for every single tool call, which is fine for our own stateless tools but
    silently breaks Playwright: browser_navigate would open a browser, go to
    the page, and close it, and the following browser_snapshot would get a new
    blank browser reporting about:blank. Browser state only survives inside one
    session.
    """
    config = load_config(config_path)
    client = MultiServerMCPClient(_connections(config))
    names = list(config["mcpServers"])

    async with AsyncExitStack() as stack:
        tools: dict = {}
        for name in names:
            try:
                session = await stack.enter_async_context(client.session(name))
                loaded = await load_mcp_tools(session)
            except Exception as exc:
                raise MCPConnectionError(
                    f"MCP server {name!r} failed to start.\n"
                    f"  cause: {type(exc).__name__}: {exc}\n"
                    f"  check: is its command installed, and is mcp_config.json correct?"
                ) from exc
            for tool in loaded:
                tools[tool.name] = tool
        yield tools
