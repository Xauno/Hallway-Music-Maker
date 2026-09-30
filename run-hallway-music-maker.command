#!/bin/zsh
# macOS launcher: double-click in Finder to run Hallway Music Maker.
cd "$(dirname "$0")"
source .venv/bin/activate
hallway-music-maker
echo
read -s -k "?Press any key to close..."
