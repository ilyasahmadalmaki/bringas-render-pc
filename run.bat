@echo off
REM Bringas Render PC - Windows launcher
REM Cek Python + ffmpeg, lalu jalankan GUI.
setlocal
title Bringas Render PC

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python tidak ketemu di PATH.
  echo Install Python 3.10+ dari https://www.python.org/ dan centang "Add Python to PATH".
  pause
  exit /b 1
)

if exist "%~dp0ffmpeg.exe" (
  echo [OK] ffmpeg.exe ketemu di folder aplikasi.
) else (
  where ffmpeg >nul 2>nul
  if errorlevel 1 (
    echo [PERINGATAN] ffmpeg.exe tidak ketemu di folder aplikasi maupun PATH.
    echo Download dari https://www.gyan.dev/ffmpeg/builds/ lalu copy ffmpeg.exe + ffprobe.exe ke folder ini.
    echo Tool tetap dibuka, tapi Generate akan gagal sampai ffmpeg tersedia.
  ) else (
    echo [OK] ffmpeg ketemu di PATH.
  )
)

cd /d "%~dp0"
python bringas_render_pc.py
if errorlevel 1 (
  echo.
  echo [ERROR] Aplikasi berhenti dengan error. Lihat pesan di atas.
  pause
)
