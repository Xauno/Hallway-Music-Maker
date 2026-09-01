@echo off
setlocal
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
hallway-music-maker.exe --ffmpeg "C:\Users\xoehl\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build-shared\bin\ffmpeg.exe"
pause
