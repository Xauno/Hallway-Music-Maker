"""Command-line interface for Hallway Music Maker."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import os
import random
import re
import shutil
import sys
from pathlib import Path

import spotipy
from dotenv import load_dotenv

from .pipeline import create_combo


def playlist_id(value: str) -> str:
    match = re.search(r"playlist/([A-Za-z0-9]+)", value)
    return match.group(1) if match else value.strip()


def track_ids(value: str) -> list[str]:
    ids = []
    for item in value.split(","):
        text = item.strip()
        match = re.search(r"track/([A-Za-z0-9]+)", text)
        track_id = match.group(1) if match else text
        if track_id:
            ids.append(track_id)
    return ids


def resolve_ffmpeg(value: str) -> str:
    if shutil.which(value) or Path(value).is_file():
        return value
    if value == "ffmpeg":
        package_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        matches = sorted(package_root.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
        if matches:
            return str(matches[-1])
    return value


def ffmpeg_available(value: str) -> bool:
    return shutil.which(value) is not None or Path(value).is_file()


def next_combo_path(output_dir: Path, today: date | None = None) -> Path:
    today = today or date.today()
    prefix = today.strftime("%m,%d")
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)\.mp3$")
    numbers = [
        int(match.group(1))
        for file in output_dir.glob(f"{prefix}-*.mp3")
        if (match := pattern.match(file.name))
    ]
    return output_dir / f"{prefix}-{max(numbers, default=0) + 1}.mp3"


def write_execution_log(path: Path, combos: list[tuple[Path, list]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    for output, tracks in combos:
        sections.append("\n".join([f"[{output.name}]"] + [f"[{track.label}]" for track in tracks]))
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Make a random 12-minute MP3 from a Spotify playlist.")
    parser.add_argument("--playlist", help="Spotify playlist URL or playlist ID")
    parser.add_argument("--output", type=Path, help="Output MP3 path; defaults to output\\MM,DD-N.mp3")
    parser.add_argument("--target-minutes", type=float, help="Combo length in minutes; defaults to 12")
    parser.add_argument("--state", type=Path, default=Path("data/used_tracks.json"))
    parser.add_argument("--work-dir", type=Path, default=Path(".downloads"))
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable or full path")
    parser.add_argument(
        "--cookies-from-browser",
        choices=("chrome", "edge", "firefox", "brave"),
        default="firefox",
        help="Browser cookies for YouTube; defaults to firefox",
    )
    parser.add_argument("--dry-run", action="store_true", help="Select tracks without downloading or changing state")
    parser.add_argument("--seed", type=int, help="Seed selection for reproducible dry runs")
    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    if not args.playlist and not args.dry_run:
        try:
            combo_count = int(input("How many combos would you like to make? [1]: ").strip() or "1")
        except ValueError:
            print("The number of combos must be a whole number.", file=sys.stderr)
            return 2
        if combo_count <= 0:
            print("The number of combos must be greater than zero.", file=sys.stderr)
            return 2
        extra_track_ids = []
        wants_extras = input("Do you want to add additional songs? [y/N]: ").strip().lower()
        if wants_extras in {"y", "yes"}:
            extra_value = input("Spotify track URLs or IDs, separated by commas: ").strip()
            extra_track_ids = track_ids(extra_value) if extra_value else []
    else:
        combo_count = 1
        extra_track_ids = []
    if args.target_minutes is None:
        length = input("Finished MP3 length in minutes [12]: ").strip()
        try:
            args.target_minutes = float(length) if length else 12.0
        except ValueError:
            print("Length must be a number of minutes.", file=sys.stderr)
            return 2
    requested_output = args.output
    required = ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        print(f"Missing environment variable(s): {', '.join(missing)}", file=sys.stderr)
        return 2
    if args.target_minutes <= 0:
        print("--target-minutes must be greater than zero", file=sys.stderr)
        return 2
    args.ffmpeg = resolve_ffmpeg(args.ffmpeg)
    if not args.dry_run and not ffmpeg_available(args.ffmpeg):
        print(
            f"FFmpeg was not found at '{args.ffmpeg}'. Exiting. Install FFmpeg or pass its path with --ffmpeg.",
            file=sys.stderr,
        )
        return 2

    spotify = spotipy.Spotify(
        auth_manager=spotipy.SpotifyClientCredentials(
            client_id=os.environ["SPOTIPY_CLIENT_ID"],
            client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        )
    )
    playlist_key = playlist_id(args.playlist) if args.playlist else "__custom__"
    if not args.playlist and extra_track_ids:
        custom_seconds = 0
        for track_id in extra_track_ids:
            try:
                custom_seconds += int(spotify.track(track_id).get("duration_ms") or 0) / 1000
            except Exception as exc:
                print(f"Could not read additional track {track_id}: {exc}", file=sys.stderr)
        required_seconds = combo_count * args.target_minutes * 60
        if custom_seconds >= required_seconds:
            print("The custom songs cover all requested combos; no playlist is needed.")
        else:
            args.playlist = input("Spotify playlist link: ").strip()
            if not args.playlist:
                print("A playlist link is required.", file=sys.stderr)
                return 2
            playlist_key = playlist_id(args.playlist)
    elif not args.playlist:
        args.playlist = input("Spotify playlist link: ").strip()
        if not args.playlist:
            print("A playlist link is required.", file=sys.stderr)
            return 2
        playlist_key = playlist_id(args.playlist)
    execution_combos = []
    execution_used_ids = set()
    log_directory = requested_output.parent if requested_output else Path("output")
    log_path = log_directory / f"combo_log_{datetime.now():%Y%m%d_%H%M%S}.txt"
    for combo_number in range(1, combo_count + 1):
        if requested_output is None:
            output = next_combo_path(Path("output"))
        elif combo_count > 1:
            output = requested_output.with_name(
                f"{requested_output.stem}-{combo_number}{requested_output.suffix}"
            )
        else:
            output = requested_output
        try:
            tracks = create_combo(
                spotify=spotify,
                savify=None,
                playlist_id=playlist_key,
                work_dir=args.work_dir,
                output=output or next_combo_path(Path("output")),
                state_path=args.state,
                target_seconds=round(args.target_minutes * 60),
                ffmpeg=args.ffmpeg,
                cookies_from_browser=args.cookies_from_browser,
                rng=random.Random(args.seed + combo_number - 1) if args.seed is not None else random.Random(),
                dry_run=args.dry_run,
                extra_track_ids=extra_track_ids,
                execution_used_ids=execution_used_ids,
            )
            if not args.dry_run:
                execution_used_ids.update(track.id for track in tracks)
                execution_combos.append((output, tracks))
                write_execution_log(log_path, execution_combos)
        except Exception as exc:
            print(f"Combo {combo_number} failed: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
