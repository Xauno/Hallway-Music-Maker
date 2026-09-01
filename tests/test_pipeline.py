import json
import random
from pathlib import Path
from datetime import date

from hallway_music_maker.cli import next_combo_path
from hallway_music_maker.pipeline import Track, choose_tracks, safe_filename
from hallway_music_maker.state import load_used_ids, save_used_ids


def track(track_id: str, duration_ms: int = 240_000) -> Track:
    return Track(track_id, f"Song {track_id}", "Artist", f"spotify:track:{track_id}", duration_ms)


def test_choose_tracks_excludes_used_and_reaches_target():
    tracks = [track("a"), track("b"), track("c")]

    chosen = choose_tracks(tracks, {"a"}, 480, random.Random(1))

    assert {item.id for item in chosen} == {"b", "c"}


def test_safe_filename_removes_windows_invalid_characters():
    assert safe_filename('"Heroes": Live?') == "_Heroes__ Live_"


def test_state_round_trip_is_atomic_format(tmp_path: Path):
    state_path = tmp_path / "data" / "used_tracks.json"

    save_used_ids(state_path, "playlist-a", {"b", "a"})

    assert load_used_ids(state_path, "playlist-a") == {"a", "b"}
    assert load_used_ids(state_path, "playlist-b") == set()
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"playlist-a": ["a", "b"]}


def test_next_combo_path_counts_only_the_requested_date(tmp_path: Path):
    (tmp_path / "08,18-1.mp3").touch()
    (tmp_path / "08,18-3.mp3").touch()
    (tmp_path / "08,17-9.mp3").touch()

    assert next_combo_path(tmp_path, date(2026, 8, 18)) == tmp_path / "08,18-4.mp3"
