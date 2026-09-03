import json
import random
import shutil
import subprocess
from pathlib import Path
from datetime import date

from hallway_music_maker.cli import next_combo_path, resolve_ffmpeg
from hallway_music_maker.pipeline import (
    Track,
    choose_tracks,
    concat_file_path,
    merge_mp3s,
    prioritize_tracks,
    safe_filename,
)
from hallway_music_maker.state import load_used_ids, reset_all_used_ids, save_used_ids


def track(track_id: str, duration_ms: int = 240_000) -> Track:
    return Track(track_id, f"Song {track_id}", "Artist", f"spotify:track:{track_id}", duration_ms)


def test_choose_tracks_excludes_used_and_reaches_target():
    tracks = [track("a"), track("b"), track("c")]

    chosen = choose_tracks(tracks, {"a"}, 480, random.Random(1))

    assert {item.id for item in chosen} == {"b", "c"}


def test_choose_tracks_ignores_tracks_without_duration():
    tracks = [track("unknown", 0), track("usable")]

    assert choose_tracks(tracks, set(), 60, random.Random(1)) == [tracks[1]]


def test_resolve_ffmpeg_returns_absolute_path():
    resolved = resolve_ffmpeg("ffmpeg")

    assert Path(resolved).is_absolute()
    assert Path(resolved).is_file()


def test_concat_file_path_escapes_apostrophes():
    path = Path(".downloads/01 - The Killers - All These Things That I've Done.mp3")

    assert concat_file_path(path).endswith("That I'\\''ve Done.mp3'")


def test_concat_file_path_preserves_song_punctuation():
    path = Path(".downloads/R&B #1 (Live) [2024] - It's 100%!.mp3")

    manifest_line = concat_file_path(path)

    assert manifest_line.endswith("R&B #1 (Live) [2024] - It'\\''s 100%!.mp3'")


def test_merge_mp3s_accepts_punctuation_in_filename(tmp_path: Path):
    source = tmp_path / "It's R&B #1 (Live) [2024]!.mp3"
    output = tmp_path / "merged.mp3"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1", str(source)],
        check=True,
    )

    merge_mp3s([source], output, 1, ffmpeg)

    assert output.is_file()
    assert output.stat().st_size > 0


def test_safe_filename_removes_windows_invalid_characters():
    assert safe_filename('"Heroes": Live?') == "_Heroes__ Live_"


def test_prioritize_tracks_can_put_custom_songs_first_or_shuffle_them_in():
    tracks = [track("playlist"), track("custom-1"), track("custom-2")]

    custom_first = prioritize_tracks(tracks, set(), {"custom-1", "custom-2"}, random.Random(1))
    shuffled = prioritize_tracks(tracks, set(), set(), random.Random(1))

    assert {item.id for item in custom_first[:2]} == {"custom-1", "custom-2"}
    assert {item.id for item in shuffled} == {"playlist", "custom-1", "custom-2"}


def test_prioritize_tracks_puts_only_the_assigned_custom_song_first():
    tracks = [track("playlist"), track("custom-1"), track("custom-2")]

    prioritized = prioritize_tracks(tracks, set(), {"custom-2"}, random.Random(1))

    assert prioritized[0].id == "custom-2"


def test_state_round_trip_is_atomic_format(tmp_path: Path):
    state_path = tmp_path / "data" / "used_tracks.json"

    save_used_ids(state_path, "playlist-a", {"b", "a"})

    assert load_used_ids(state_path, "playlist-a") == {"a", "b"}
    assert load_used_ids(state_path, "playlist-b") == set()
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"playlist-a": ["a", "b"]}


def test_resetting_one_playlist_preserves_other_used_lists(tmp_path: Path):
    state_path = tmp_path / "data" / "used_tracks.json"

    save_used_ids(state_path, "playlist-a", {"a"})
    save_used_ids(state_path, "playlist-b", {"b"})
    save_used_ids(state_path, "playlist-a", set())

    assert load_used_ids(state_path, "playlist-a") == set()
    assert load_used_ids(state_path, "playlist-b") == {"b"}


def test_reset_all_used_ids_clears_every_playlist(tmp_path: Path):
    state_path = tmp_path / "data" / "used_tracks.json"

    save_used_ids(state_path, "playlist-a", {"a"})
    save_used_ids(state_path, "playlist-b", {"b"})
    reset_all_used_ids(state_path)

    assert load_used_ids(state_path, "playlist-a") == set()
    assert load_used_ids(state_path, "playlist-b") == set()
    assert state_path.read_text(encoding="utf-8") == "{}\n"


def test_next_combo_path_counts_only_the_requested_date(tmp_path: Path):
    (tmp_path / "08,18-1.mp3").touch()
    (tmp_path / "08,18-3.mp3").touch()
    (tmp_path / "08,17-9.mp3").touch()

    assert next_combo_path(tmp_path, date(2026, 8, 18)) == tmp_path / "08,18-4.mp3"
