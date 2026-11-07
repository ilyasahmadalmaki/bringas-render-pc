@echo off
REM Build BringasRenderPC.exe di PC Windows lo sendiri (alternatif kalau
REM lo gak mau nunggu GitHub Actions).
REM Syarat: Python 3.10+ sudah terinstall dan ada di PATH.
REM Hasil: dist\BringasRenderPC.exe
REM Catatan: ffmpeg.exe + ffprobe.exe TIDAK ikut dibundel ke dalam .exe.
REM Taruh ffmpeg.exe + ffprobe.exe di folder yang sama dengan .exe-nya,
REM atau di PATH, seperti biasa.

python --version >nul 2>&1
if errorlevel 1 (
  echo Python tidak ketemu di PATH. Install Python 3.10+ dulu dari python.org
  echo dan centang "Add Python to PATH" pas install.
  pause
  exit /b 1
)

pip install pyinstaller
if errorlevel 1 (
  echo Gagal install PyInstaller.
  pause
  exit /b 1
)

pyinstaller --noconfirm --onefile --windowed --name BringasRenderPC bringas_render_pc.py
if errorlevel 1 (
  echo Build gagal.
  pause
  exit /b 1
)

echo.
echo Selesai! EXE ada di: dist\BringasRenderPC.exe
echo Jangan lupa taruh ffmpeg.exe + ffprobe.exe di sebelah .exe-nya.
pause
