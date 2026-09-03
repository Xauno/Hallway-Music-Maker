# Hallway Music Maker

Creates a randomized MP3 combo from a Spotify playlist. Each successful combo is trimmed to 12 minutes by default, and its selected Spotify track IDs are stored under that playlist's ID in `data/used_tracks.json` so they are not selected again for that playlist. If a combo exceeds length set minus two minutes (10 minutes for the 12 minute default) then it will not add another song. This is to prevent a song getting cut off when it just starts.

## Requirements

- Python 3.10+
- FFmpeg available on `PATH`
- Node.js available on `PATH` for yt-dlp's YouTube JavaScript support
- A Spotify application with a client ID and client secret
- A playlist that the Spotify application can read

The app uses Spotify for playlist metadata and yt-dlp to find and convert matching audio. Only download music you have permission to use, and follow the relevant service terms.

## Setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
Copy-Item .env.example .env
```

Put your Spotify application credentials in `.env`:

```text
SPOTIPY_CLIENT_ID=...
SPOTIPY_CLIENT_SECRET=...
```

Install FFmpeg separately and make sure `ffmpeg -version` works in a new terminal. Node.js 18+ is also recommended; verify it with `node --version`.

## Use

For the short interactive mode, activate the environment and run the command with no arguments:

```powershell
hallway-music-maker
```

It asks how many combos to make, whether you want to add custom Spotify tracks, and the combo length. Custom tracks are shuffled once and assigned one per combo, with the assigned track placed first. If more custom tracks are entered than combos, it warns that the extras will be skipped and asks whether to proceed. If the custom tracks alone cover the requested duration for every combo, it does not ask for a playlist. Otherwise, it asks for the playlist link. Press Enter at the length prompt to use 12 minutes. It uses Firefox cookies by default, and the output is automatically named `output\\MM,DD-N.mp3`, where `N` is the next combo made that day.

Preview the next selection without downloading or changing the used list:

```powershell
hallway-music-maker --playlist https://open.spotify.com/playlist/PLAYLIST_ID --dry-run --seed 42
```

Create the combo:

```powershell
hallway-music-maker --playlist PLAYLIST_ID
```

Reset the used-track list for one playlist without creating a combo:

```powershell
hallway-music-maker --playlist PLAYLIST_ID --reset-used
```

This clears only that playlist's entries in `data/used_tracks.json` and preserves used tracks for other playlists.

Reset the used-track lists for every playlist:

```powershell
hallway-music-maker --playlist all --reset-used
```

Useful options:

- `--target-minutes 12` changes the output length.
- `--output output/morning.mp3` changes the output path.
- `--state data/used_tracks.json` changes the used-track database.
- `--work-dir .downloads` changes temporary download storage.
- `--cookies-from-browser chrome` uses a logged-in browser session when YouTube returns HTTP 403. Use `edge`, `firefox`, or `brave` as appropriate.
- `--seed 42` makes selection reproducible.

Each combo uses its own temporary subdirectory under the work directory, so simultaneous or interrupted runs do not reuse another combo's download filenames.

The used list is updated only after the final MP3 is created. Tracks are tracked separately for each playlist, so using a different playlist does not exclude tracks used by another playlist. To start over, delete `data/used_tracks.json`.

When fewer than 10 unused songs remain, the app prints a warning. If the playlist runs out before reaching the requested combo length, you can either combine the songs already downloaded or reset that playlist's used list and select again. Songs already used during the current execution are still excluded after a reset.
