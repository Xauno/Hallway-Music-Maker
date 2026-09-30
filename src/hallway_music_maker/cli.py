"""Command-line interface for Hallway Music Maker."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import spotipy
from dotenv import load_dotenv

from .pipeline import Track, create_combo, fetch_tracks, playlist_tracks, resolve_ffmpeg, selection_target
from .state import reset_all_used_ids, save_used_ids

CUSTOM_ONLY_KEY = "__custom__"
OUTPUT_DIR = Path("output")


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


def custom_songs_cover_combos(custom_seconds: list[float], combo_count: int, target_seconds: float) -> bool:
    """Each combo gets one custom song, so every combo needs its own song long enough to fill it."""
    required = selection_target(target_seconds)
    assigned = custom_seconds[:combo_count]
    return len(assigned) == combo_count and all(seconds >= required for seconds in assigned)


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


def combo_output_path(requested: Path | None, combo_number: int, combo_count: int) -> Path:
    if requested is None:
        return next_combo_path(OUTPUT_DIR)
    if combo_count > 1:
        return requested.with_name(f"{requested.stem}-{combo_number}{requested.suffix}")
    return requested


def write_execution_log(path: Path, combos: list[tuple[Path, list[Track]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    for output, tracks in combos:
        sections.append("\n".join([f"[{output.name}]"] + [f"[{track.label}]" for track in tracks]))
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


def open_folder(path: Path) -> None:
    """Show the folder in Finder / File Explorer; failure here should never fail the run."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as exc:
        print(f"Could not open {path}: {exc}", file=sys.stderr)


def ask_yes(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes"}


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def prompt_combo_setup() -> tuple[int, list[str]] | None:
    """Ask for the combo count and optional custom songs; returns None if the user input is invalid or cancelled."""
    try:
        combo_count = int(input("How many combos would you like to make? [1]: ").strip() or "1")
    except ValueError:
        fail("The number of combos must be a whole number.")
        return None
    if combo_count <= 0:
        fail("The number of combos must be greater than zero.")
        return None
    extra_track_ids: list[str] = []
    if ask_yes("Do you want to add additional songs? [y/N]: "):
        extra_track_ids = track_ids(input("Spotify track URLs or IDs, separated by commas: ").strip())
        if len(extra_track_ids) > combo_count:
            skipped_count = len(extra_track_ids) - combo_count
            if not ask_yes(
                f"Warning: {len(extra_track_ids)} custom songs were entered for {combo_count} "
                f"combos. {skipped_count} custom song(s) will be skipped. Proceed? [y/N]: "
            ):
                fail("Cancelled because some custom songs would be skipped.")
                return None
        random.shuffle(extra_track_ids)
    return combo_count, extra_track_ids


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
    parser.add_argument(
        "--reset-used",
        action="store_true",
        help="Clear the used-track list for --playlist and exit",
    )
    parser.add_argument("--no-open", action="store_true", help="Don't open the output folder when finished")
    return parser


def reset_used(args: argparse.Namespace) -> int:
    if not args.playlist:
        return fail("--reset-used requires --playlist.")
    selected_playlist_id = playlist_id(args.playlist)
    if selected_playlist_id.lower() == "all":
        reset_all_used_ids(args.state)
        print("Reset used tracks for all playlists.")
    else:
        save_used_ids(args.state, selected_playlist_id, set())
        print(f"Reset used tracks for playlist {selected_playlist_id}.")
    return 0


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    if args.reset_used:
        return reset_used(args)
    combo_count, extra_track_ids = 1, []
    if not args.playlist and not args.dry_run:
        setup = prompt_combo_setup()
        if setup is None:
            return 2
        combo_count, extra_track_ids = setup
    if args.target_minutes is None:
        length = input("Finished MP3 length in minutes [12]: ").strip()
        try:
            args.target_minutes = float(length) if length else 12.0
        except ValueError:
            return fail("Length must be a number of minutes.")
    missing = [name for name in ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET") if not os.getenv(name)]
    if missing:
        return fail(f"Missing environment variable(s): {', '.join(missing)}")
    if args.target_minutes <= 0:
        return fail("--target-minutes must be greater than zero")
    args.ffmpeg = resolve_ffmpeg(args.ffmpeg)
    if not args.dry_run and not Path(args.ffmpeg).is_file():
        return fail(f"FFmpeg was not found at '{args.ffmpeg}'. Exiting. Install FFmpeg or pass its path with --ffmpeg.")
    target_seconds = round(args.target_minutes * 60)

    spotify = spotipy.Spotify(
        auth_manager=spotipy.SpotifyClientCredentials(
            client_id=os.environ["SPOTIPY_CLIENT_ID"],
            client_secret=os.environ["SPOTIPY_CLIENT_SECRET"],
        )
    )
    custom_tracks = fetch_tracks(spotify, extra_track_ids)[:combo_count]
    if args.playlist:
        playlist_key = playlist_id(args.playlist)
    elif custom_tracks and custom_songs_cover_combos(
        [track.seconds for track in custom_tracks], combo_count, target_seconds
    ):
        print("The custom songs cover all requested combos; no playlist is needed.")
        playlist_key = CUSTOM_ONLY_KEY
    else:
        link = input("Spotify playlist link: ").strip()
        if not link:
            return fail("A playlist link is required.")
        playlist_key = playlist_id(link)
    try:
        tracks = [] if playlist_key == CUSTOM_ONLY_KEY else playlist_tracks(spotify, playlist_key)
    except Exception as exc:
        print(f"Could not read the playlist: {exc}", file=sys.stderr)
        return 1

    execution_combos: list[tuple[Path, list[Track]]] = []
    execution_used_ids: set[str] = set()
    log_path = (args.output.parent if args.output else OUTPUT_DIR) / f"combo_log_{datetime.now():%Y%m%d_%H%M%S}.txt"
    for combo_number in range(1, combo_count + 1):
        output = combo_output_path(args.output, combo_number, combo_count)
        try:
            chosen = create_combo(
                tracks=tracks,
                playlist_id=playlist_key,
                work_dir=args.work_dir,
                output=output,
                state_path=args.state,
                target_seconds=target_seconds,
                ffmpeg=args.ffmpeg,
                cookies_from_browser=args.cookies_from_browser,
                rng=random.Random(None if args.seed is None else args.seed + combo_number - 1),
                dry_run=args.dry_run,
                priority_track=custom_tracks[combo_number - 1] if combo_number <= len(custom_tracks) else None,
                execution_used_ids=execution_used_ids,
            )
            if not args.dry_run:
                execution_used_ids.update(track.id for track in chosen)
                execution_combos.append((output, chosen))
                write_execution_log(log_path, execution_combos)
        except Exception as exc:
            print(f"Combo {combo_number} failed: {exc}", file=sys.stderr)
            return 1
    if execution_combos and not args.no_open:
        open_folder(execution_combos[-1][0].parent.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
