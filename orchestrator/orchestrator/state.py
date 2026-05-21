"""JSON-backed session-id store, keyed by absolute repo path."""

import json
import os
from pathlib import Path

STATE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    / "release-orchestrator"
)
STATE_FILE = STATE_DIR / "sessions.json"


def _load() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())


def _save(data: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))


def _key(repo_path: str) -> str:
    return str(Path(repo_path).expanduser().resolve())


def get(repo_path: str) -> str | None:
    return _load().get(_key(repo_path))


def set_(repo_path: str, session_id: str) -> None:
    data = _load()
    data[_key(repo_path)] = session_id
    _save(data)


def clear(repo_path: str) -> None:
    data = _load()
    data.pop(_key(repo_path), None)
    _save(data)


def all_sessions() -> dict[str, str]:
    return _load()
