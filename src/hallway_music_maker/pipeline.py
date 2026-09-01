"""Spotify selection, Savify downloads, and FFmpeg assembly."""

from __future__ import annotations

import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import spotipy
from yt_dlp import YoutubeDL

from .state import load_used_ids, save_used_ids

SELECTION_BUFFER_SECONDS = 120


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
        for item in results.get("items", []):
            track = item.get("track") or {}
            track_id = track.get("id")
            if not track_id or not track.get("name"):
                continue
            url = (track.get("external_urls") or {}).get("spotify") or track.get("uri")
            if not url:
                continue
            tracks.append(
                Track(
                    id=track_id,
                    name=track["name"],
                    artists=", ".join(artist["name"] for artist in track.get("artists", [])),
                    url=url,
                    duration_ms=int(track.get("duration_ms") or 0),
                )
            )
        results = spotify.next(results) if results.get("next") else None
    return tracks


def choose_tracks(
    tracks: list[Track],
    used_ids: set[str],
    target_seconds: int,
    rng: random.Random,
    shuffle_tracks: bool = True,
) -> list[Track]:
    available = [track for track in tracks if track.id not in used_ids]
    if shuffle_tracks:
        rng.shuffle(available)
    chosen: list[Track] = []
    total_seconds = 0
    for track in available:
        chosen.append(track)
        total_seconds += max(track.duration_ms / 1000, 1)
        if total_seconds >= target_seconds:
            return chosen
    raise RuntimeError(
        f"Only {len(available)} unused track(s) are available, but they do not cover "
        f"the requested {target_seconds} seconds. Add more tracks or reset the used list."
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
    concat_file = output.parent / f".{output.stem}.concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{file.resolve().as_posix().replace(chr(39), chr(39) + chr(39))}'" for file in files)
        + "\n",
        encoding="utf-8",
    )
    try:
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-t",
            str(target_seconds),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(output),
        ]
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg was not found. Install it and add it to PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"FFmpeg could not create {output}") from exc
    finally:
        concat_file.unlink(missing_ok=True)


def create_combo(
    spotify: Any,
    savify: Any | None,
    playlist_id: str,
    work_dir: Path,
    output: Path,
    state_path: Path,
    target_seconds: int,
    ffmpeg: str = "ffmpeg",
    cookies_from_browser: str | None = None,
    rng: random.Random | None = None,
    dry_run: bool = False,
    extra_track_ids: list[str] | None = None,
    execution_used_ids: set[str] | None = None,
) -> list[Track]:
    rng = rng or random.Random()
    if execution_used_ids is None:
        execution_used_ids = set()
    all_tracks = [] if playlist_id == "__custom__" else playlist_tracks(spotify, playlist_id)
    extra_tracks: list[Track] = []
    for track_id in extra_track_ids or []:
        try:
            item = spotify.track(track_id)
            extra_tracks.append(
                Track(
                    id=item["id"],
                    name=item["name"],
                    artists=", ".join(artist["name"] for artist in item.get("artists", [])),
                    url=(item.get("external_urls") or {}).get("spotify") or item.get("uri", ""),
                    duration_ms=int(item.get("duration_ms") or 0),
                )
            )
        except Exception as exc:
            print(f"Skipping additional track {track_id}: {exc}")
    all_tracks.extend(extra_tracks)
    extra_ids = {track.id for track in extra_tracks}
    if dry_run:
        used_ids = load_used_ids(state_path, playlist_id) | execution_used_ids
        ordered_tracks = prioritize_tracks(all_tracks, used_ids, extra_ids, rng)
        chosen = choose_tracks(
            ordered_tracks,
            set(),
            max(target_seconds - SELECTION_BUFFER_SECONDS, 1),
            rng,
            shuffle_tracks=False,
        )
        print("Selected tracks:")
        for track in chosen:
            print(f"  - {track.label}")
        return chosen
    work_dir.mkdir(parents=True, exist_ok=True)
    downloads: list[Path] = []
    chosen: list[Track] = []
    total_seconds = 0.0
    selection_target_seconds = max(target_seconds - SELECTION_BUFFER_SECONDS, 1)
    used_ids = load_used_ids(state_path, playlist_id)
    available = prioritize_tracks(all_tracks, used_ids | execution_used_ids, extra_ids, rng)
    warned_low = False
    attempted_until = 0
    try:
        for index, track in enumerate(available, start=1):
            attempted_until = index
            remaining = len(available) - index
            if remaining < 10 and not warned_low:
                print(f"Warning: fewer than 10 unused songs remain ({remaining}).")
                warned_low = True
            print(f"Downloading {index}/{len(available)}: {track.label}")
            try:
                destination = download_path(work_dir, index, track)
                download_track(track, destination, ffmpeg, cookies_from_browser)
            except (OSError, RuntimeError) as exc:
                print(f"Skipping {track.label}: {exc}")
                continue
            if not destination.is_file() or destination.stat().st_size == 0:
                print(f"Skipping {track.label}: download file was not created")
                continue
            downloads.append(destination)
            chosen.append(track)
            total_seconds += max(track.duration_ms / 1000, 1)
            if total_seconds >= selection_target_seconds:
                break
        valid_pairs = [
            (track, download)
            for track, download in zip(chosen, downloads)
            if download.is_file() and download.stat().st_size > 0
        ]
        if len(valid_pairs) != len(downloads):
            print("A downloaded file disappeared before merging; trying replacement songs.")
            chosen = [track for track, _ in valid_pairs]
            downloads = [download for _, download in valid_pairs]
            total_seconds = sum(max(track.duration_ms / 1000, 1) for track in chosen)
            for index, track in enumerate(available[attempted_until:], start=attempted_until + 1):
                print(f"Downloading replacement {index}/{len(available)}: {track.label}")
                try:
                    destination = download_path(work_dir, index, track)
                    download_track(track, destination, ffmpeg, cookies_from_browser)
                except (OSError, RuntimeError) as exc:
                    print(f"Skipping {track.label}: {exc}")
                    continue
                if not destination.is_file() or destination.stat().st_size == 0:
                    print(f"Skipping {track.label}: download file was not created")
                    continue
                downloads.append(destination)
                chosen.append(track)
                total_seconds += max(track.duration_ms / 1000, 1)
                if total_seconds >= selection_target_seconds:
                    break
        if total_seconds < selection_target_seconds:
            print("No unused songs remain in this playlist.")
            choice = input(
                "Enter 1 to combine what was downloaded and finish, or "
                "2 to reset this playlist and select randomly again: "
            ).strip()
            if choice == "2":
                execution_used_ids.update(track.id for track in chosen)
                save_used_ids(state_path, playlist_id, set())
                used_ids = set()
                for download in downloads:
                    download.unlink(missing_ok=True)
                downloads.clear()
                chosen.clear()
                total_seconds = 0.0
                available = prioritize_tracks(all_tracks, execution_used_ids, extra_ids, rng)
                warned_low = False
                for index, track in enumerate(available, start=1):
                    remaining = len(available) - index
                    if remaining < 10 and not warned_low:
                        print(f"Warning: fewer than 10 unused songs remain ({remaining}).")
                        warned_low = True
                    print(f"Downloading {index}/{len(available)}: {track.label}")
                    try:
                        destination = download_path(work_dir, index, track)
                        download_track(track, destination, ffmpeg, cookies_from_browser)
                    except (OSError, RuntimeError) as exc:
                        print(f"Skipping {track.label}: {exc}")
                        continue
                    if not destination.is_file() or destination.stat().st_size == 0:
                        print(f"Skipping {track.label}: download file was not created")
                        continue
                    downloads.append(destination)
                    chosen.append(track)
                    total_seconds += max(track.duration_ms / 1000, 1)
                    if total_seconds >= selection_target_seconds:
                        break
                if total_seconds < target_seconds:
                    raise RuntimeError(
                        "The playlist has no additional songs available during this execution."
                    )
            elif not downloads:
                raise RuntimeError("No downloaded songs remain to combine.")
        merge_mp3s(downloads, output, target_seconds, ffmpeg)
        save_used_ids(state_path, playlist_id, used_ids | {track.id for track in chosen})
        print(f"Created {output}")
        return chosen
    finally:
        for download in downloads:
            download.unlink(missing_ok=True)


def download_track(
    track: Track,
    destination: Path,
    ffmpeg: str,
    cookies_from_browser: str | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    if not shutil.which(ffmpeg) and not Path(ffmpeg).is_file() and ffmpeg == "ffmpeg":
        package_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
        matches = sorted(package_root.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
        if matches:
            ffmpeg = str(matches[-1])
    if not shutil.which(ffmpeg) and not Path(ffmpeg).is_file():
        raise RuntimeError(f"FFmpeg was not found at '{ffmpeg}'. Exiting.")
    template = str(destination.with_suffix("")) + ".%(ext)s"
    options = {
        "format": "140/251/bestaudio/best",
        "outtmpl": template,
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
