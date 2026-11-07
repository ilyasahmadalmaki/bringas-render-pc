@echo off
REM ============================================================
REM  Bringas Render PC - Setup 1 Klik (untuk yang awam)
REM  Double-click file ini, tunggu sampai selesai, beres.
REM
REM  Yang dilakukan otomatis, berurutan:
REM   [1/4] Cek Python. Kalau belum ada, install otomatis
REM         (coba winget dulu, kalau gagal download installer
REM         Python 3.12 dari python.org lalu install silent).
REM   [2/4] Cek ffmpeg.exe + ffprobe.exe. Kalau belum ada,
REM         download otomatis dari gyan.dev lalu taruh di
REM         folder ini (sebelah .exe nanti).
REM   [3/4] Install PyInstaller otomatis (pip).
REM   [4/4] Build BringasRenderPC.exe otomatis, lalu dibuka.
REM
REM  Download pakai curl dulu (bawaan Windows 10/11, tahan
REM  file besar + retry otomatis 3x). Kalau curl gagal, baru
REM  fallback ke PowerShell dengan timeout dimatikan
REM  (-TimeoutSec 0) supaya file ~100 MB tidak "timed out"
REM  di tengah jalan seperti Invoke-WebRequest bawaan.
REM
REM  Yang lo butuhkan cuma: Windows 10/11 + internet.
REM  Tidak perlu install Python manual, tidak perlu centang
REM  "Add Python to PATH" manual - script ini yang urus.
REM  Tidak butuh admin (install Python user-level).
REM ============================================================
setlocal
title Bringas Render PC - Setup 1 Klik
cd /d "%~dp0"

echo ============================================================
echo  Bringas Render PC - Setup 1 Klik
echo  Double-click, tunggu sampai selesai, beres.
echo  Jangan tutup jendela ini sebelum ada tulisan SELESAI.
echo ============================================================
echo.

REM ---------- [1/4] Python ----------
echo [1/4] Cek Python...
python --version >nul 2>&1
if not errorlevel 1 goto :python_ok

echo Python belum ketemu, lagi install otomatis...
echo (Ini bisa makan waktu 1-3 menit, jangan ditutup.)

where winget >nul 2>&1
if not errorlevel 1 (
  echo  - Coba install lewat winget...
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
)

REM Coba lagi: winget kadang butuh PATH di-refresh manual.
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312\;%LOCALAPPDATA%\Programs\Python\Python312\Scripts\;%PATH%"
python --version >nul 2>&1
if not errorlevel 1 goto :python_ok

echo  - winget gagal / tidak ada, download installer Python langsung...
call :download "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe" "%TEMP%\python-setup.exe"
if errorlevel 1 (
  echo.
  echo GAGAL download installer Python. Cek internet lo, lalu
  echo double-click file ini lagi.
  pause
  exit /b 1
)
echo  - Install Python (silent, tanpa perlu klik apa-apa)...
"%TEMP%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
del "%TEMP%\python-setup.exe" >nul 2>&1
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312\;%LOCALAPPDATA%\Programs\Python\Python312\Scripts\;%PATH%"

python --version >nul 2>&1
if errorlevel 1 (
  echo.
  echo GAGAL install Python. Coba restart PC lo sekali, lalu
  echo double-click file ini lagi. Kalau tetap gagal, install
  echo manual dari python.org (centang "Add Python to PATH").
  pause
  exit /b 1
)

:python_ok
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Python ketemu: %%v
echo.

REM ---------- [2/4] ffmpeg ----------
echo [2/4] Cek ffmpeg...
if exist ffmpeg.exe if exist ffprobe.exe goto :ffmpeg_ok

echo ffmpeg belum ada, lagi download otomatis dari gyan.dev...
echo (File sekitar 100 MB, jangan ditutup. Kalau internet lambat
echo  ini bisa 3-5 menit - itu normal, bukan stuck.)
call :download "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" "%TEMP%\ffmpeg.zip"
if errorlevel 1 (
  echo.
  echo GAGAL download ffmpeg setelah 3x coba. Cek internet lo, lalu
  echo double-click file ini lagi.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d='%TEMP%\ffmpeg-unzip'; if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive -Path '%TEMP%\ffmpeg.zip' -DestinationPath $d; $f=Get-ChildItem -Path $d -Recurse -Filter ffmpeg.exe | Select-Object -First 1; $p=Get-ChildItem -Path $d -Recurse -Filter ffprobe.exe | Select-Object -First 1; Copy-Item $f.FullName -Destination '%~dp0ffmpeg.exe' -Force; Copy-Item $p.FullName -Destination '%~dp0ffprobe.exe' -Force"
del "%TEMP%\ffmpeg.zip" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Remove-Item '%TEMP%\ffmpeg-unzip' -Recurse -Force -ErrorAction SilentlyContinue"

if not exist ffmpeg.exe (
  echo.
  echo GAGAL pasang ffmpeg. Cek internet lo, lalu
  echo double-click file ini lagi.
  pause
  exit /b 1
)
if not exist ffprobe.exe (
  echo.
  echo GAGAL pasang ffprobe. Cek internet lo, lalu
  echo double-click file ini lagi.
  pause
  exit /b 1
)

:ffmpeg_ok
echo ffmpeg ketemu: ffmpeg.exe + ffprobe.exe
echo.

REM ---------- [3/4] PyInstaller ----------
echo [3/4] Install PyInstaller (sekali saja)...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install pyinstaller
if errorlevel 1 (
  echo.
  echo GAGAL install PyInstaller. Cek internet lo, lalu
  echo double-click file ini lagi.
  pause
  exit /b 1
)
echo.

REM ---------- [4/4] Build EXE ----------
echo [4/4] Build BringasRenderPC.exe (ini paling lama, 1-3 menit)...
python -m PyInstaller --noconfirm --onefile --windowed --name BringasRenderPC bringas_render_pc.py
if errorlevel 1 (
  echo.
  echo GAGAL build .exe. Coba double-click file ini lagi.
  pause
  exit /b 1
)
if not exist dist\BringasRenderPC.exe (
  echo.
  echo GAGAL: file dist\BringasRenderPC.exe tidak ketemu.
  echo Coba double-click file ini lagi.
  pause
  exit /b 1
)

copy /y dist\BringasRenderPC.exe "%~dp0BringasRenderPC.exe" >nul

echo.
echo ============================================================
echo  SELESAI!
echo.
echo  Aplikasi lo ada di: BringasRenderPC.exe
echo  (di folder ini, sebelah file setup ini)
echo  ffmpeg.exe + ffprobe.exe juga sudah ada di sebelahnya.
echo.
echo  Tinggal double-click BringasRenderPC.exe buat pakai.
echo ============================================================
echo.
echo Membuka aplikasi...
start "" "%~dp0BringasRenderPC.exe"
pause
exit /b 0

REM ============================================================
REM  :download  - download 1 file dengan retry.
REM  Pakai:  call :download "URL" "path\output"
REM  Urutan: curl dulu (retry 3x, progress kelihatan),
REM  fallback PowerShell dengan -TimeoutSec 0.
REM  Return 0 kalau file ada dan ukurannya lebih dari 0.
REM ============================================================
:download
set "DL_URL=%~1"
set "DL_OUT=%~2"
del "%DL_OUT%" >nul 2>&1
where curl >nul 2>&1
if not errorlevel 1 (
  echo  - Download pakai curl (retry otomatis 3x)...
  curl -L --retry 3 --retry-delay 5 --connect-timeout 30 -o "%DL_OUT%" "%DL_URL%"
  if exist "%DL_OUT%" (
    for %%A in ("%DL_OUT%") do if %%~zA gtr 0 exit /b 0
  )
  echo  - curl gagal, coba PowerShell...
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '%DL_URL%' -OutFile '%DL_OUT%' -TimeoutSec 0 -UseBasicParsing"
if exist "%DL_OUT%" (
  for %%A in ("%DL_OUT%") do if %%~zA gtr 0 exit /b 0
)
exit /b 1
