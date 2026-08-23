"""Load .env into the process environment.

The README tells you to put configuration in .env, so something has to read it.
Deliberately tiny and dependency-free: it handles KEY=value, ignores blanks and
# comments, strips surrounding quotes, and never overwrites a variable that is
already set — an explicit `KSM_OFFLINE=1 python -m agent ...` on the command
line must win over the file.
"""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_FILE) -> list[str]:
    """Set variables from the file. Returns the names that were applied."""
    if not path.exists():
        return []

    applied = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        applied.append(key)
    return applied


def describe() -> str:
    """A one-line summary of the model configuration, with no secret in it."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    return (
        f"model {os.environ.get('OPENROUTER_MODEL', '(unset)')} "
        f"via {os.environ.get('OPENROUTER_BASE_URL', '(unset)')} "
        f"[key {'set, ' + str(len(key)) + ' chars' if key else 'MISSING'}]"
    )
