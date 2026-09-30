"""Spotify selection, yt-dlp downloads, and FFmpeg assembly."""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import spotipy
from yt_dlp import YoutubeDL

from .state import load_used_ids, save_used_ids

SELECTION_BUFFER_SECONDS = 120
LOW_TRACK_WARNING = 10


def resolve_ffmpeg(value: str) -> str:
    resolved = shutil.which(value)
    if resolved:
        return str(Path(resolved).resolve())
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    if value == "ffmpeg":
        package_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        matches = sorted(package_root.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
        if matches:
            return str(matches[-1].resolve())
        for candidate in (Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")):
            if candidate.is_file():
                return str(candidate.resolve())
    return value


def concat_file_path(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("\\", "\\\\").replace("'", "'\\''")
    return f"file '{escaped}'"


def selection_target(target_seconds: float) -> float:
    """Stop adding songs this far short of the target so a new song is not cut off right after it starts."""
    return max(target_seconds - SELECTION_BUFFER_SECONDS, 1)


@dataclass(frozen=True)
class Track:
    id: str
    name: str
    artists: str
    url: str
    duration_ms: int

    @property
    def label(self) -> str:
        return f"{self.artists} - {self.name}"

    @property
    def seconds(self) -> float:
        return max(self.duration_ms / 1000, 1)


def total_seconds(tracks: list[Track]) -> float:
    return sum(track.seconds for track in tracks)


def track_from_spotify(item: dict[str, Any] | None) -> Track | None:
    item = item or {}
    url = (item.get("external_urls") or {}).get("spotify") or item.get("uri")
    if not item.get("id") or not item.get("name") or not url:
        return None
    return Track(
        id=item["id"],
        name=item["name"],
        artists=", ".join(artist["name"] for artist in item.get("artists", [])),
        url=url,
        duration_ms=int(item.get("duration_ms") or 0),
    )


def safe_filename(value: str, max_length: int = 180) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not cleaned:
        cleaned = "track"
    if cleaned.upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
        cleaned = f"_{cleaned}"
    return cleaned[:max_length].rstrip(" .") or "track"


def download_path(work_dir: Path, index: int, track: Track) -> Path:
    return work_dir / f"{index:02d} - {safe_filename(track.artists)} - {safe_filename(track.name)}.mp3"


def playlist_tracks(spotify: Any, playlist_id: str) -> list[Track]:
    tracks: list[Track] = []
    seen_pages: set[str] = set()
    try:
        results = spotify.playlist_items(
            playlist_id,
            fields="items(track(id,name,artists(name),external_urls,uri,duration_ms)),next",
            additional_types=("track",),
        )
    except spotipy.SpotifyException as exc:
        if exc.http_status == 404:
            raise RuntimeError(
                "Spotify could not find this playlist. Check that the ID came from the playlist URL, "
                "that the playlist still exists, and that it is public."
            ) from exc
        raise
    while results:
        page_url = results.get("href") or results.get("next")
        if page_url and page_url in seen_pages:
            raise RuntimeError("Spotify returned the same playlist page more than once.")
        if page_url:
            seen_pages.add(page_url)
        for item in results.get("items", []):
            track = track_from_spotify(item.get("track"))
            if track:
                tracks.append(track)
        results = spotify.next(results) if results.get("next") else None
    return tracks


def fetch_tracks(spotify: Any, track_ids: list[str]) -> list[Track]:
    """Look up individual Spotify tracks, skipping any that cannot be read."""
    tracks: list[Track] = []
    for track_id in track_ids:
        try:
            track = track_from_spotify(spotify.track(track_id))
        except Exception as exc:
            print(f"Skipping additional track {track_id}: {exc}")
            continue
        if track:
            tracks.append(track)
        else:
            print(f"Skipping additional track {track_id}: Spotify returned incomplete track data")
    return tracks


def choose_tracks(
    tracks: list[Track],
    used_ids: set[str],
    target_seconds: float,
    rng: random.Random,
    shuffle_tracks: bool = True,
) -> list[Track]:
    available = [track for track in tracks if track.id not in used_ids and track.duration_ms > 0]
    if shuffle_tracks:
        rng.shuffle(available)
    chosen: list[Track] = []
    seconds = 0.0
    for track in available:
        chosen.append(track)
        seconds += track.seconds
        if seconds >= target_seconds:
            return chosen
    raise RuntimeError(
        f"Only {len(available)} unused track(s) are available, but they do not cover "
        f"the requested {target_seconds:g} seconds. Add more tracks or reset the used list."
    )


def prioritize_tracks(
    tracks: list[Track],
    used_ids: set[str],
    priority_ids: set[str],
    rng: random.Random,
) -> list[Track]:
    available = [track for track in tracks if track.id not in used_ids]
    priority = [track for track in available if track.id in priority_ids]
    regular = [track for track in available if track.id not in priority_ids]
    rng.shuffle(priority)
    rng.shuffle(regular)
    return priority + regular


def merge_mp3s(files: list[Path], output: Path, target_seconds: int, ffmpeg: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    missing = [file for file in files if not file.is_file() or file.stat().st_size == 0]
    if missing:
        names = ", ".join(str(file) for file in missing)
        raise RuntimeError(f"Downloaded file disappeared before merging: {names}")
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.stem}.",
        suffix=".concat.txt",
        delete=False,
    ) as concat:
        concat.write("".join(f"{concat_file_path(file)}\n" for file in files))
        concat_file = Path(concat.name)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-t", str(target_seconds), "-vn", "-codec:a", "libmp3lame", "-q:a", "2",
        str(output),
    ]
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg was not found. Install it and add it to PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"FFmpeg could not create {output}") from exc
    finally:
        concat_file.unlink(missing_ok=True)


def download_until(
    tracks: list[Track],
    work_dir: Path,
    target_seconds: float,
    ffmpeg: str,
    cookies_from_browser: str | None,
) -> tuple[list[Track], list[Path]]:
    """Download tracks in order, skipping failures, until they add up to target_seconds."""
    chosen: list[Track] = []
    downloads: list[Path] = []
    seconds = 0.0
    warned_low = False
    for index, track in enumerate(tracks, start=1):
        remaining = len(tracks) - index
        if remaining < LOW_TRACK_WARNING and not warned_low:
            print(f"Warning: fewer than {LOW_TRACK_WARNING} unused songs remain ({remaining}).")
            warned_low = True
        print(f"Downloading {index}/{len(tracks)}: {track.label}")
        destination = download_path(work_dir, index, track)
        try:
            download_track(track, destination, ffmpeg, cookies_from_browser)
        except (OSError, RuntimeError) as exc:
            print(f"Skipping {track.label}: {exc}")
            continue
        chosen.append(track)
        downloads.append(destination)
        seconds += track.seconds
        if seconds >= target_seconds:
            break
    return chosen, downloads


def create_combo(
    tracks: list[Track],
    playlist_id: str,
    work_dir: Path,
    output: Path,
    state_path: Path,
    target_seconds: int,
    ffmpeg: str = "ffmpeg",
    cookies_from_browser: str | None = None,
    rng: random.Random | None = None,
    dry_run: bool = False,
    priority_track: Track | None = None,
    execution_used_ids: set[str] | None = None,
) -> list[Track]:
    """Build one combo from the playlist's tracks, with an optional custom track placed first.

    execution_used_ids holds tracks already used during this run; it is updated if the playlist is reset.
    """
    rng = rng or random.Random()
    if execution_used_ids is None:
        execution_used_ids = set()
    candidates = tracks
    priority_ids: set[str] = set()
    if priority_track:
        candidates = [priority_track, *(track for track in tracks if track.id != priority_track.id)]
        priority_ids = {priority_track.id}
    target = selection_target(target_seconds)
    used_ids = load_used_ids(state_path, playlist_id)

    def ordered(excluded_ids: set[str]) -> list[Track]:
        # The assigned custom track is always allowed, even if it was used before.
        return prioritize_tracks(candidates, excluded_ids - priority_ids, priority_ids, rng)

    if dry_run:
        chosen = choose_tracks(ordered(used_ids | execution_used_ids), set(), target, rng, shuffle_tracks=False)
        print("Selected tracks:")
        for track in chosen:
            print(f"  - {track.label}")
        return chosen

    work_dir.mkdir(parents=True, exist_ok=True)
    combo_work_dir = Path(tempfile.mkdtemp(prefix=f".{output.stem}.", dir=work_dir))
    try:
        chosen, downloads = download_until(
            ordered(used_ids | execution_used_ids), combo_work_dir, target, ffmpeg, cookies_from_browser
        )
        if total_seconds(chosen) < target:
            print("No unused songs remain in this playlist.")
            choice = input(
                "Enter 1 to combine what was downloaded and finish, or "
                "2 to reset this playlist and select randomly again: "
            ).strip()
            if choice == "2":
                execution_used_ids.update(track.id for track in chosen)
                save_used_ids(state_path, playlist_id, set())
                used_ids = set()
                chosen, downloads = download_until(
                    ordered(execution_used_ids), combo_work_dir, target, ffmpeg, cookies_from_browser
                )
                if total_seconds(chosen) < target:
                    raise RuntimeError("The playlist has no additional songs available during this execution.")
            elif not downloads:
                raise RuntimeError("No downloaded songs remain to combine.")
        merge_mp3s(downloads, output, target_seconds, ffmpeg)
        save_used_ids(state_path, playlist_id, used_ids | {track.id for track in chosen})
        print(f"Created {output}")
        return chosen
    finally:
        shutil.rmtree(combo_work_dir, ignore_errors=True)


def download_track(
    track: Track,
    destination: Path,
    ffmpeg: str,
    cookies_from_browser: str | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    options = {
        "format": "140/251/bestaudio/best",
        "outtmpl": str(destination.with_suffix("")) + ".%(ext)s",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "socket_timeout": 30,
        "ffmpeg_location": ffmpeg,
        "js_runtimes": {"node": {}} if shutil.which("node") else {},
        "remote_components": ["ejs:github"],
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    if cookies_from_browser:
        options["cookiesfrombrowser"] = (cookies_from_browser,)
    try:
        with YoutubeDL(options) as downloader:
            result = downloader.download([f"ytsearch1:{track.label} audio"])
    except Exception as exc:
        raise RuntimeError(f"Could not download {track.label}: {exc}") from exc
    if result != 0 or not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError(f"The downloader did not create an MP3 for {track.label}")
