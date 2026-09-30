# Hallway Music Maker

Hallway Music Maker is a command-line tool that builds randomized, fixed-length MP3 mixes ("combos") from a Spotify playlist. You give it a playlist and a length (12 minutes by default). It picks random songs from the playlist, finds and downloads matching audio, and joins the songs into one MP3 of that length.

It remembers which songs each playlist has already used, so later combos don't repeat songs until you reset that playlist's list.

> Spotify supplies only the playlist metadata (titles, artists and lengths). The audio comes from a YouTube search through [yt-dlp](https://github.com/yt-dlp/yt-dlp). Only download music you have permission to use, and follow the relevant service terms.

---

## How it works

```
Spotify playlist ──► pick random unused tracks ──► search YouTube for each ──► convert to MP3
      (spotipy)          (until ≈ target − 2 min)        (yt-dlp, "ytsearch1")      (FFmpeg)
                                                                                        │
   data/used_tracks.json ◄── record chosen track IDs ◄── join + trim to target ◄───────┘
                                                            (FFmpeg concat)
```

1. **Read the playlist.** The app signs in to the Spotify Web API with client credentials and reads every track in the playlist, following pagination.
2. **Filter out used tracks.** It removes tracks already recorded for this playlist in `data/used_tracks.json`, tracks used by earlier combos in the same run, and tracks with no known duration. It then shuffles what remains.
3. **Download.** It downloads tracks one at a time. For each track, yt-dlp takes the first YouTube result for `"<artists> - <title> audio"` and FFmpeg converts it to a 192 kbps MP3. If a download fails, the app skips that track and tries the next one.
4. **Stop early to avoid cut-off songs.** It stops adding songs once the combined length reaches the **target minus 2 minutes** (10 minutes for a 12-minute combo). This keeps a new song from starting only to be cut off seconds later.
5. **Merge.** FFmpeg joins the MP3s and caps the result at the target length. The finished combo is therefore never longer than the target, and it can be up to about 2 minutes shorter.
6. **Record.** The app adds the chosen track IDs to the playlist's used list only after the final MP3 exists, then deletes the temporary downloads.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) (see [FFmpeg lookup](#ffmpeg-lookup))
- Node.js 18+ on `PATH` (recommended). yt-dlp uses it to solve YouTube's JavaScript challenges.
- A [Spotify developer application](https://developer.spotify.com/dashboard) with a client ID and client secret
- A playlist the Spotify application can read (public playlists work)
- A browser you've used to visit YouTube. The app reads that browser's cookies, and uses Firefox unless you choose another (see [Options](#options)).

## Setup

### Windows (PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"   # [dev] adds pytest
Copy-Item .env.example .env
winget install Gyan.FFmpeg      # if FFmpeg isn't installed yet
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e ".[dev]"   # [dev] adds pytest
cp .env.example .env
brew install ffmpeg node        # macOS with Homebrew
```

Then add your Spotify credentials to `.env`:

```text
SPOTIPY_CLIENT_ID=your_spotify_client_id
SPOTIPY_CLIENT_SECRET=your_spotify_client_secret
```

Open a new terminal and check that `ffmpeg -version` and `node --version` both work.

## Usage

### Interactive mode

Activate the virtual environment and run the command with no arguments:

```powershell
hallway-music-maker
```

It asks for:

1. **How many combos** to make (default 1).
2. **Custom songs** (optional): Spotify track URLs or IDs, separated by commas.
3. **The combo length** in minutes. Press Enter for 12.
4. **The Spotify playlist link.** It skips this question only if each combo's custom song is long enough to fill that combo by itself (at least the target minus 2 minutes).

Output files are named automatically as `output/MM,DD-N.mp3`, where `N` is the next free number for that day (for example `09,23-1.mp3` and `09,23-2.mp3`).

On Windows, you can also double-click `run-hallway-music-maker.bat`. It activates `.venv`, runs the tool in interactive mode, and keeps the window open when the tool finishes.

On macOS, double-click `run-hallway-music-maker.command` in Finder. It does the same thing in Terminal. (The first time, macOS may block it; right-click it and choose **Open**.)

When the run finishes, the output folder opens in Finder or File Explorer. Pass `--no-open` to skip this.

#### Custom songs

- The app shuffles the custom songs once, gives each combo one of them, and plays that combo's song first.
- If you enter more custom songs than combos, it warns you that the extra songs will be skipped and asks whether to continue.
- A custom song is used even if it's already on the used list.

### Command-line mode

Preview a selection without downloading anything or changing the used list:

```powershell
hallway-music-maker --playlist https://open.spotify.com/playlist/PLAYLIST_ID --dry-run --seed 42
```

Create one combo:

```powershell
hallway-music-maker --playlist PLAYLIST_ID --target-minutes 12
```

Reset one playlist's used list without creating a combo. Other playlists keep their lists:

```powershell
hallway-music-maker --playlist PLAYLIST_ID --reset-used
```

Reset the used lists for every playlist:

```powershell
hallway-music-maker --playlist all --reset-used
```

### Options

| Option | Default | Description |
| --- | --- | --- |
| `--playlist` | *(prompted)* | Spotify playlist URL or ID. With `--reset-used`, `all` means every playlist. |
| `--target-minutes` | *(prompted, 12)* | Combo length in minutes. |
| `--output` | `output/MM,DD-N.mp3` | Output MP3 path. When you make several combos, `-1`, `-2`, … is added to the file name. |
| `--state` | `data/used_tracks.json` | The file that stores used tracks. |
| `--work-dir` | `.downloads` | Where temporary downloads go. Each combo gets its own subfolder, which is deleted afterwards. |
| `--ffmpeg` | `ffmpeg` | FFmpeg command or full path. |
| `--cookies-from-browser` | `firefox` | The browser whose YouTube cookies to use: `chrome`, `edge`, `firefox` or `brave`. |
| `--dry-run` | off | Print the selection only. Nothing is downloaded or saved. |
| `--seed` | random | Makes the selection reproducible. Combo *n* uses `seed + n − 1`. |
| `--reset-used` | off | Clear the used list for `--playlist`, then exit. |
| `--no-open` | off | Don't open the output folder when the run finishes. |

When you pass `--playlist` (or `--dry-run`), the app makes a single combo and doesn't ask about custom songs.

## Files the app creates

| Path | Contents |
| --- | --- |
| `output/MM,DD-N.mp3` | The finished combos. |
| `output/combo_log_YYYYMMDD_HHMMSS.txt` | One log per run, listing each combo's file name and its tracks in order. |
| `data/used_tracks.json` | Used Spotify track IDs, grouped by playlist ID. Combos made only from custom songs are stored under `__custom__`. |
| `.downloads/` | Temporary audio files, deleted after each combo. |

Example `data/used_tracks.json`:

```json
{
  "37i9dQZF1DXcBWIGoYBM5M": ["0VjIjW4GlUZAMYd2vXMi3b", "7qiZfU4dY1lWllzX7mPBI3"]
}
```

To start completely fresh, use `--playlist all --reset-used` or delete the file.

### When the playlist runs low

- When fewer than 10 unused songs remain, the app prints a warning.
- If the unused songs can't fill the combo, it asks you to choose:
  1. **Finish anyway.** Join the songs already downloaded into a shorter combo.
  2. **Reset and retry.** Clear this playlist's used list and pick again. Songs already used earlier in the same run are still left out.

## FFmpeg lookup

If `--ffmpeg` is left at its default, the app looks for FFmpeg in this order:

1. `ffmpeg` on `PATH`
2. WinGet installs of `Gyan.FFmpeg` under `%LOCALAPPDATA%\Microsoft\WinGet\Packages`
3. `/opt/homebrew/bin/ffmpeg`, then `/usr/local/bin/ffmpeg` (Homebrew on macOS)

If it doesn't find FFmpeg, it exits and tells you to install FFmpeg or pass `--ffmpeg <path>`. `--dry-run` doesn't need FFmpeg.

## Project structure

```
├── src/hallway_music_maker/
│   ├── cli.py         # argparse CLI, interactive prompts, output naming, run log
│   ├── pipeline.py    # Spotify reading, track selection, FFmpeg lookup, yt-dlp download, FFmpeg merge
│   └── state.py       # Reads and writes data/used_tracks.json (atomic writes)
├── tests/
│   └── test_pipeline.py
├── run-hallway-music-maker.bat   # Windows launcher
├── .env.example                  # Template for Spotify credentials
└── pyproject.toml                # Package metadata; installs the `hallway-music-maker` command
```

Key pieces:

- **`cli.main()`** is the entry point. It collects input, checks credentials and FFmpeg, and reads the playlist and custom songs from Spotify once. It then calls `create_combo` for each combo, sharing a list of songs already used in this run.
- **`pipeline.create_combo()`** runs one combo: it orders the tracks, calls `download_until()` (which skips failed downloads), shows the low-playlist prompt if needed, then merges and updates the used list.
- **`pipeline.choose_tracks()` and `prioritize_tracks()`** are the pure selection functions used by dry runs and tests.
- **`state.save_used_ids()` and `reset_all_used_ids()`** write to a temporary file and then swap it in, so an interrupted run can't corrupt the used list.

## Running tests

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

The tests cover track selection, custom-song priority, filename sanitizing, FFmpeg concat-file escaping, output naming, the used-list file, the custom-songs-only check and the reset-and-retry flow (with downloads and merging stubbed out). Two tests need FFmpeg: the FFmpeg lookup test fails without it, and the merge test does nothing without it.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Missing environment variable(s): SPOTIPY_CLIENT_ID…` | Create `.env` from `.env.example` and fill in your credentials. |
| `Spotify could not find this playlist` | Check that the ID comes from the playlist URL, that the playlist still exists and that it's public. |
| YouTube returns HTTP 403, or every download is skipped | Pass `--cookies-from-browser` with a browser that's installed and has visited YouTube. Update yt-dlp with `pip install -U yt-dlp`, and make sure Node.js is on `PATH`. |
| `FFmpeg was not found` | Install FFmpeg or pass `--ffmpeg "C:\path\to\ffmpeg.exe"`. |
| A song doesn't sound right | yt-dlp uses the first YouTube search result, which is sometimes a cover, a live version or the wrong song. |
