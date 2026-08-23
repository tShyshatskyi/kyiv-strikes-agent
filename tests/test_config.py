from agent.config import load_env


def test_values_are_read(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('OPENROUTER_MODEL=gemini-2.0-flash\nKSM_OFFLINE=1\n', encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("KSM_OFFLINE", raising=False)
    assert set(load_env(env)) == {"OPENROUTER_MODEL", "KSM_OFFLINE"}
    import os
    assert os.environ["OPENROUTER_MODEL"] == "gemini-2.0-flash"


def test_comments_and_blank_lines_are_skipped(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# a comment\n\nOPENROUTER_MODEL=x\n', encoding="utf-8")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    assert load_env(env) == ["OPENROUTER_MODEL"]


def test_quotes_are_stripped(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('NOMINATIM_CONTACT="me@example.com"\n', encoding="utf-8")
    monkeypatch.delenv("NOMINATIM_CONTACT", raising=False)
    load_env(env)
    import os
    assert os.environ["NOMINATIM_CONTACT"] == "me@example.com"


def test_an_explicit_environment_variable_wins_over_the_file(tmp_path, monkeypatch):
    """KSM_OFFLINE=1 on the command line must beat KSM_OFFLINE=0 in .env."""
    env = tmp_path / ".env"
    env.write_text("KSM_OFFLINE=0\n", encoding="utf-8")
    monkeypatch.setenv("KSM_OFFLINE", "1")
    assert load_env(env) == []
    import os
    assert os.environ["KSM_OFFLINE"] == "1"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_env(tmp_path / "nope.env") == []
