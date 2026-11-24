"""One function, `ask(role, prompt, schema)`, that returns parsed JSON.

Two backends, chosen by LLM_BACKEND:

- "claude" (default): the Claude Code CLI in headless mode (`claude -p`), with
  the answer constrained to a JSON schema. Runs on whatever account the CLI is
  logged in with; no key in this repository.
- "openai": any OpenAI-compatible endpoint (OpenRouter, Gemini, Ollama) via
  OPENAI_BASE_URL / OPENAI_API_KEY / per-role model names.

Every answer is cached on disk by a hash of (model, prompt, schema), so a rerun
of the pipeline costs nothing for work already done, and a crash halfway
through loses nothing.
"""

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache" / "llm"

# Which model does which job. The reader handles volume, so it is the cheap one;
# matching and auditing need judgement.
MODELS = {
    "reader": os.environ.get("KSR_READER_MODEL", "haiku"),
    "matcher": os.environ.get("KSR_MATCHER_MODEL", "sonnet"),
    "auditor": os.environ.get("KSR_AUDITOR_MODEL", "sonnet"),
}

# The CLI is run from an empty folder so it does not pick up this project's own
# instructions or tools.
_NEUTRAL_CWD = Path(tempfile.gettempdir()) / "ksr-llm"
_NEUTRAL_CWD.mkdir(exist_ok=True)


class LLMError(RuntimeError):
    pass


def _key(model: str, prompt: str, schema: dict) -> str:
    blob = json.dumps([model, prompt, schema], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _claude_binary() -> str:
    """The real executable. On Windows npm installs a .cmd wrapper, and passing
    a JSON schema full of quotes through cmd.exe is asking for trouble."""
    import shutil
    found = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or "claude"
    if found.lower().endswith(".cmd"):
        exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    return found


# How long each role may think before answering. Reading is transcription and
# gains nothing from it (3x slower, 5x dearer in a test); matching and auditing
# are judgement calls.
THINKING = {"reader": "0", "matcher": "0", "auditor": "2000"}

SYSTEM = ("You are one agent in a data pipeline that documents strikes on Kyiv from "
          "public reports. Follow the task exactly and answer only through the "
          "structured output.")


def _claude(role: str, model: str, prompt: str, schema: dict, timeout: int) -> dict:
    # A lean session: our own system prompt, no tools, no MCP servers, no
    # skills or plugins from whoever's machine this runs on. Without this the
    # CLI loads the user's whole environment into every call, which made calls
    # slow and, now and then, failed them outright as "prompt too long".
    cmd = [_claude_binary(), "-p", "--model", model, "--output-format", "json",
           "--json-schema", json.dumps(schema, ensure_ascii=True), "--tools", "",
           "--no-session-persistence", "--system-prompt", SYSTEM,
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--disable-slash-commands"]
    env = {**os.environ, "MAX_THINKING_TOKENS": THINKING.get(role, "0")}
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout, cwd=_NEUTRAL_CWD, env=env)
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise LLMError(f"CLI gave no JSON (exit {proc.returncode}): "
                       f"{(proc.stderr or proc.stdout)[:300]}")
    if envelope.get("is_error"):
        raise LLMError(str(envelope.get("result"))[:300])
    out = envelope.get("structured_output")
    if out is None:
        raise LLMError("no structured_output in the answer")
    return {"data": out, "cost_usd": envelope.get("total_cost_usd", 0),
            "ms": envelope.get("duration_ms", 0)}


def _openai(role: str, model: str, prompt: str, schema: dict, timeout: int) -> dict:
    import httpx
    base = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    r = httpx.post(f"{base}/chat/completions", timeout=timeout, headers={
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, json={
        "model": model, "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "answer", "schema": schema, "strict": False}},
    })
    if r.status_code != 200:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
    return {"data": json.loads(r.json()["choices"][0]["message"]["content"]),
            "cost_usd": 0, "ms": 0}


def ask(role: str, prompt: str, schema: dict, timeout: int = 600) -> dict:
    """Answer `prompt` as JSON matching `schema`. Cached; retried twice."""
    model = MODELS[role]
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{role}-{_key(model, prompt, schema)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["data"]

    backend = _openai if os.environ.get("LLM_BACKEND") == "openai" else _claude
    last = None
    for attempt in range(3):
        try:
            answer = backend(role, model, prompt, schema, timeout)
            break
        except (LLMError, subprocess.TimeoutExpired) as exc:
            last = exc
            time.sleep(5 * (attempt + 1))
    else:
        raise LLMError(f"{role} failed three times: {last}")

    path.write_text(json.dumps({"role": role, "model": model, **answer},
                               ensure_ascii=False), encoding="utf-8")
    return answer["data"]


def spent() -> dict:
    """What the cached answers cost, per role. Used for the run report."""
    totals: dict = {}
    for f in CACHE.glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        t = totals.setdefault(d["role"], {"calls": 0, "usd": 0.0, "seconds": 0.0})
        t["calls"] += 1
        t["usd"] += d.get("cost_usd") or 0
        t["seconds"] += (d.get("ms") or 0) / 1000
    return totals
