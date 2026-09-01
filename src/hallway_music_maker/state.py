"""Persistent state for tracks that have already been used."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def load_used_ids(path: Path, playlist_id: str) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read state file {path}: {exc}") from exc
    if isinstance(data, list):
        return set()
    if not isinstance(data, dict):
        raise RuntimeError(f"State file {path} must contain a JSON object keyed by playlist ID")
    playlist_tracks = data.get(playlist_id, [])
    if not isinstance(playlist_tracks, list) or not all(isinstance(item, str) for item in playlist_tracks):
        raise RuntimeError(f"State file {path} must contain lists of track IDs")
    return set(playlist_tracks)


def save_used_ids(path: Path, playlist_id: str, used_ids: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, list[str]] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read state file {path}: {exc}") from exc
        if isinstance(existing, dict):
            data = {
                key: value
                for key, value in existing.items()
                if isinstance(key, str) and isinstance(value, list) and all(isinstance(item, str) for item in value)
            }
    data[playlist_id] = sorted(used_ids)
    payload = json.dumps(data, indent=2) + "\n"
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        temp.write(payload)
        temporary_path = Path(temp.name)
    os.replace(temporary_path, path)
