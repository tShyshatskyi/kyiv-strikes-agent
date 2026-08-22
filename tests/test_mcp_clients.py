import json

import pytest

from agent.mcp_clients import MCPConnectionError, build_client, load_config


def test_config_declares_both_servers():
    config = load_config()
    assert set(config["mcpServers"]) == {"playwright", "kyiv-strikes"}


def test_playwright_version_is_pinned_not_floating():
    """A demo that silently upgrades its browser server between rehearsal and
    defence is a demo that breaks during the defence."""
    args = " ".join(load_config()["mcpServers"]["playwright"]["args"])
    assert "@latest" not in args
    assert "@playwright/mcp@0." in args


def test_env_placeholders_are_expanded(monkeypatch):
    monkeypatch.setenv("KSM_OFFLINE", "1")
    monkeypatch.setenv("KSM_DB_PATH", "test.sqlite")
    monkeypatch.setenv("NOMINATIM_CONTACT", "a@b.c")
    env = load_config()["mcpServers"]["kyiv-strikes"]["env"]
    assert env["KSM_OFFLINE"] == "1"
    assert env["KSM_DB_PATH"] == "test.sqlite"
    assert env["NOMINATIM_CONTACT"] == "a@b.c"
    assert env["PYTHONPATH"].endswith("kyiv-strikes-agent")


def test_a_missing_config_says_so_plainly(tmp_path):
    with pytest.raises(MCPConnectionError) as exc:
        load_config(tmp_path / "nope.json")
    assert "not found" in str(exc.value)


def test_a_broken_config_says_what_is_wrong(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(MCPConnectionError) as exc:
        load_config(bad)
    assert "not valid JSON" in str(exc.value)


def test_a_server_without_a_command_is_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"mcpServers": {"playwright": {"args": []}}}),
                   encoding="utf-8")
    with pytest.raises(MCPConnectionError) as exc:
        load_config(bad)
    assert "no command" in str(exc.value)


async def test_a_server_that_cannot_start_fails_with_a_readable_message(tmp_path):
    """Failure scenario B: the agent must report this, not crash on it."""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"mcpServers": {"playwright": {
        "command": "definitely-not-a-real-binary", "args": [], "transport": "stdio",
    }}}), encoding="utf-8")

    with pytest.raises(MCPConnectionError) as exc:
        await build_client(bad)
    message = str(exc.value)
    assert "playwright" in message
    assert "failed to start" in message
    assert "check:" in message
