"""Persistent state for tracks that have already been used."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def _read_state(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read state file {path}: {exc}") from exc


def _write_state(path: Path, data: dict[str, list[str]]) -> None:
    """Write through a temporary file so an interrupted run cannot leave a half-written state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp.write(json.dumps(data, indent=2) + "\n")
        temporary_path = Path(temp.name)
    os.replace(temporary_path, path)


def _is_id_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def load_used_ids(path: Path, playlist_id: str) -> set[str]:
    data = _read_state(path)
    if isinstance(data, list):
        return set()
    if not isinstance(data, dict):
        raise RuntimeError(f"State file {path} must contain a JSON object keyed by playlist ID")
    playlist_tracks = data.get(playlist_id, [])
    if not _is_id_list(playlist_tracks):
        raise RuntimeError(f"State file {path} must contain lists of track IDs")
    return set(playlist_tracks)


def save_used_ids(path: Path, playlist_id: str, used_ids: set[str]) -> None:
    existing = _read_state(path)
    data = (
        {key: value for key, value in existing.items() if isinstance(key, str) and _is_id_list(value)}
        if isinstance(existing, dict)
        else {}
    )
    data[playlist_id] = sorted(used_ids)
    _write_state(path, data)


def reset_all_used_ids(path: Path) -> None:
    _write_state(path, {})
