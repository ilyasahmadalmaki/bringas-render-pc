<div align="center">

<img src="assets/header.svg" width="100%" alt="BRINGAS RENDER PC" />

**Gabung video + audio jadi video siap upload YouTube — audio kelas terbaik.**

![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D6?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![ffmpeg](https://img.shields.io/badge/ffmpeg-auto--download-007808?logo=ffmpeg&logoColor=white)
![GPU](https://img.shields.io/badge/GPU-NVENC%20%7C%20QSV%20%7C%20AMF-76B900?logo=nvidia&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

</div>

## ⚡ Apa ini?

**Bringas Render PC** adalah aplikasi desktop Windows (Python + Tkinter, stdlib only) untuk menggabungkan **video + audio** menjadi video siap upload YouTube, dengan kualitas audio terbaik.

Contoh: video 1 menit (seamless loop) + audio 1 jam → video output 1 jam. Atau: folder berisi klip-klip pendek 2–10 detik + folder lagu → video panjang otomatis.

## 🚀 Setup 1 Klik (untuk yang awam)

Lo **tidak perlu** install Python manual, **tidak perlu** download ffmpeg manual, **tidak perlu** buka command prompt. Cukup:

1. Download repo ini (tombol hijau **Code → Download ZIP**), extract ke satu folder.
2. Double-click **`setup-1-klik.bat`**, tunggu sampai ada tulisan **SELESAI**. Jangan ditutup di tengah jalan.
3. File ini otomatis, berurutan: install Python 3.12 (kalau belum ada, lewat winget — kalau winget gagal, download installer dari python.org lalu install silent), download ffmpeg dari gyan.dev lalu taruh `ffmpeg.exe` + `ffprobe.exe` di folder ini, install PyInstaller, build `BringasRenderPC.exe`, lalu aplikasinya langsung dibuka.
4. Berikutnya tinggal double-click `BringasRenderPC.exe` saja.

Yang dibutuhkan cuma **Windows 10/11 + internet**. Tidak butuh admin. Kalau internet putus di tengah jalan, double-click lagi file yang sama — langkah yang sudah selesai tidak diulang dari nol.

## 🎬 Cara pakai

### 1 — Video

| Mode | Cara kerja |
|---|---|
| **1 File (loop)** | 1 video, diasumsikan seamless loop. Dipakai `-c:v copy` (tidak pernah di-encode ulang), durasi output = durasi audio. |
| **Folder** | **Semua** klip di folder dipakai (diacak, tiap putaran di-shuffle ulang). Kalau total durasi **kurang** dari audio → **diduplikat** sampai nutupin audio. Kalau **lebih** → **dipotong** pas durasi audio. Folder sumber utuh — tidak ada file yang dipindah atau dihapus. |

Tombol **Shuffle Preview** nunjukin urutan klip yang bakal dipakai (termasuk berapa duplikatnya).

### 2 — Audio

| Mode | Cara kerja |
|---|---|
| **1 File** | WAV/FLAC → loudnorm dual-pass **-14 LUFS / -1 dB** → AAC 320k, 48 kHz, stereo. MP3 → `-c:a copy` (hindari double lossy encode). |
| **Folder** | Gabung **semua** file sampai habis. **Pin** maju ke depan sesuai urutan nge-pin (tidak terbatas, pin tidak pernah diacak). Yang tidak di-pin: alphabetical kalau Random OFF, shuffle murni kalau Random ON. Antar file: potong langsung, tanpa jeda. Tiap file di-normalize sendiri ke -14 LUFS lalu encode satu-satu ke AAC 320k. |

### 3 — Preview & Output

- Cek urutan audio, total durasi audio/video, dan estimasi output sebelum render.
- **Nama file output bisa custom** — begitu lo mengetik nama sendiri, auto-nama berhenti menimpa. Tombol **Auto Nama** regenerasi nama otomatis (`nama-video_nama-audio_TANGGAL`).
- **Tidak pernah menimpa file lama** — nama yang sama otomatis jadi `nama (1).mp4`, `nama (2).mp4`, dst (gaya Windows Explorer).
- **Temp auto-hapus** — folder temp (`bringas_render_*`) dibersihkan otomatis setelah render, berhasil maupun gagal.

## ✨ Fitur

- 🎮 **Render GPU otomatis** — deteksi NVENC (NVIDIA), Quick Sync (Intel), atau AMF (AMD). Tiap kandidat diverifikasi dulu dengan encode 1 frame, jadi deteksi tidak ngaco. Kalau tidak ada GPU → CPU libx264 (CRF 18) seperti biasa. Bisa dimatikan lewat checkbox.
- 👻 **Tanpa jendela cmd** — ffmpeg/ffprobe jalan tanpa console hitam, saat render maupun probing.
- 🌃 **UI dark neon cyberpunk** — hitam-biru pekat, aksen cyan + magenta, log terminal hijau, tombol ▶ RENDER besar.
- 📜 **Log lengkap** — semua langkah tercatat (probing → normalize → concat → mux), enak buat debug.
- 🧩 **Stdlib only** — tidak ada `pip install` apa-apa. `requirements.txt` memang kosong.

<details>
<summary><b>🔧 Detail teknis (klik untuk buka)</b></summary>

<br>

**Video concat — spek sama vs beda**

Tool cek spek tiap klip (lebar/tinggi/fps/codec) via ffprobe. Kalau **semua sama**: concat demuxer + `-c:v copy` (cepat, tanpa turun kualitas). Kalau **beda**: re-encode via concat filter ke 1920×1080, 30 fps — libx264 CRF 18 preset veryfast, atau encoder GPU kalau terdeteksi (NVENC p4 + CQ 19, Quick Sync medium + ICQ 19, AMF balanced + CQP 19).

**Audio — kenapa begitu**

YouTube selalu re-encode audio yang di-upload. Jadi yang penting adalah memberi source paling bersih: AAC 320k 48 kHz stereo dari source lossless, loudness -14 LUFS (standar normalisasi YouTube), peak -1 dBTP. MP3 yang sudah lossy tidak di-encode ulang kalau bisa dihindari (mode 1 file), tapi di mode Folder semua file harus disatukan ke satu format dulu supaya bisa digabung — jadi di situ semuanya di-encode ke AAC 320k.

**Probe durasi**

Durasi tiap file di-probe sekali per sesi (di-cache). Fallback: durasi container → durasi stream → frames/fps. Klip yang durasinya tetap tidak terbaca dihitung **1 detik** dan tetap dipakai semua — tidak ada klip yang dilewati diam-diam.

</details>

## 📁 File di repo ini

| File | Isi |
|---|---|
| `bringas_render_pc.py` | Aplikasi GUI Tkinter (UI dark neon) |
| `ffmpeg_utils.py` | Semua logika ffmpeg — probe, normalize, pick, build, deteksi GPU (tanpa GUI) |
| `test_logic.py` | Self-test logika + integrasi ffmpeg (`python3 test_logic.py`) |
| `assets/header.svg` | Header animasi gradasi untuk README ini |
| `setup-1-klik.bat` | **Setup 1 klik untuk awam:** auto-install Python + auto-download ffmpeg + install PyInstaller + build .exe, lalu buka aplikasinya |
| `build_exe.bat` | Build `BringasRenderPC.exe` lokal di Windows (PyInstaller) — untuk yang Python-nya sudah ada |
| `run.bat` | Launcher Windows (cek python + ffmpeg, lalu jalan) |
| `.github/workflows/build-windows.yml` | Build .exe otomatis di GitHub Actions (saat ini Actions **disabled** untuk repo ini, jadi jalur yang jalan adalah `setup-1-klik.bat` / `build_exe.bat`) |
| `requirements.txt` | Kosong — stdlib only |
| `.gitignore` | Abaikan file output, temp, dan `__pycache__` |
| `LICENSE` | MIT |

## 🧪 Test

```bash
python3 -m py_compile bringas_render_pc.py ffmpeg_utils.py test_logic.py
python3 test_logic.py
```

Di Linux (VM dev) ffmpeg/ffprobe tersedia, jadi test integrasi (membuat klip + nada kecil, build audio folder, build video loop & folder, verify, GPU detect) ikut jalan. Di Windows, pastikan `ffmpeg.exe` / `ffprobe.exe` ketemu dulu sebelum Render.

## 📝 Catatan v1

- Loudness target -14 LUFS / -1 dB **terkunci** (tidak bisa diubah di v1).
- Tombol **Batalkan** di v1 hanya mencegah render berikutnya; proses ffmpeg yang sedang jalan tidak dimatikan paksa.
- Video 1-file diasumsikan seamless loop — tool tidak mengubahnya.
- ffmpeg **tidak** dibundel ke dalam .exe (ukurannya ~80 MB sendiri) — `setup-1-klik.bat` download otomatis, atau taruh `ffmpeg.exe` + `ffprobe.exe` di folder yang sama dengan `.exe`, atau di PATH.

<div align="center">

**◆ HYDRA TEAM BRINGAS — PERMUDAH HIDUPMU ◆**

</div>
