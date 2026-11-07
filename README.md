# Bringas Render PC

Windows desktop tool (Python + Tkinter, stdlib only) untuk menggabung
**video + audio** menjadi video siap upload YouTube, dengan kualitas
audio terbaik.

Repo: `ilyasahmadalmaki/bringas-render-pc` (branch `main`)

## Yang dilakukan tool ini

- Video 1 menit (seamless loop) + audio 1 jam → video output 1 jam.
- Video pendek-pendek dari folder digabung acak sampai sepanjang audio.
- Audio dari 1 file atau dari 1 folder (digabung semua).

## Download .exe (paling gampang)

Tidak perlu install Python. Setiap push ke `main` otomatis dibuild
di GitHub Actions (Windows) jadi `BringasRenderPC.exe`:

1. Buka tab **Actions** di repo ini → klik run terbaru yang hijau
   ("Build Windows EXE") → download artifact
   **BringasRenderPC-windows** (isinya `BringasRenderPC.exe`).
2. Kalau ada tag `v*` (misal `v1.0.0`), .exe-nya juga ada di tab
   **Releases**.
3. Taruh `ffmpeg.exe` + `ffprobe.exe` (download dari
   https://www.gyan.dev/ffmpeg/builds/, `ffmpeg-release-full.zip`)
   di folder yang **sama** dengan `BringasRenderPC.exe`, atau di PATH.
   ffmpeg **tidak** dibundel ke dalam .exe (ukurannya ~80 MB sendiri),
   jadi ini tetap wajib.
4. Double-click `BringasRenderPC.exe`. Selesai.

Alternatif: build .exe sendiri di PC lo dengan double-click
`build_exe.bat` (butuh Python + `pip install pyinstaller`, script-nya
yang urus sisanya, hasil di `dist\BringasRenderPC.exe`).

## Install dari source di Windows

1. Install **Python 3.10+** dari python.org. Centang **"Add Python to PATH"**.
2. Download **ffmpeg** dari https://www.gyan.dev/ffmpeg/builds/
   (pilih `ffmpeg-release-full.zip` — sudah termasuk `libsoxr` untuk
   resample kualitas terbaik; `essentials` juga bisa, tool otomatis
   fallback ke resampler bawaan kalau soxr tidak ada).
   Extract, copy `ffmpeg.exe` dan `ffprobe.exe` ke folder aplikasi ini
   (sebelah `bringas_render_pc.py`), ATAU tambahkan folder `bin`-nya
   ke PATH Windows.
3. Double-click **`run.bat`**, atau jalankan:
   ```
   python bringas_render_pc.py
   ```
4. Tidak ada `pip install` apa-apa — semua library sudah bawaan Python
   (Tkinter + stdlib). `requirements.txt` memang kosong.

## Cara pakai (singkat)

1. **Video** — pilih mode:
   - **1 File (loop):** 1 video, diasumsikan seamless loop. Dipakai
     `-stream_loop -1`, `-c:v copy` (tidak pernah di-encode ulang),
     durasi output = durasi audio, `-movflags +faststart`.
   - **Folder:** folder berisi klip pendek (2–10 detik, boleh 1000 file).
     Tool probe durasi tiap klip, shuffle seluruh list (acak murni,
     tanpa pandang nama/tanggal, tanpa ulang di 1 output), ambil
     berurutan sampai total ≥ durasi audio, trim klip terakhir supaya
     total pas = durasi audio. Kalau total stok < durasi audio, tool
     kasih warning dulu dan tidak diam-diam mengulang video.
2. **Audio** — pilih mode:
   - **1 File:** WAV/FLAC → loudnorm dual-pass -14 LUFS, AAC 320k,
     48 kHz, stereo. MP3 → `-c:a copy` (default, hindari double
     lossy encode).
   - **Folder:** gabung SEMUA file sampai habis. Urutan default
     alphabetical dari nama file. **Pin:** file yang di-pin maju ke
     depan, urutan sesuai urutan nge-pin (#1, #2, #3…, pin tidak
     terbatas), file pin tidak pernah diacak. Yang tidak di-pin:
     alphabetical kalau Random OFF, `random.shuffle` kalau Random ON.
     **Hapus dari Daftar:** hanya hapus dari list sesi ini — file di
     disk aman, muncul lagi saat rescan. Hapus file yang di-pin juga
     menghapus pin-nya, pin lain naik nomor.
     Antar file: potong langsung, tanpa jeda, tanpa crossfade.
     Loudness: tiap file di-normalize sendiri ke -14 LUFS, peak -1 dB,
     dual-pass loudnorm, `linear=true`, resample 48 kHz (soxr),
     encode satu-satu ke AAC 320k (kualitas terbaik, lebih lambat
     diterima). Hasil AAC lalu digabung pakai `-c copy` (tanpa encode
     ulang kedua).
3. **Preview** — cek urutan audio, total durasi audio/video, estimasi
   output, dan warning stok sebelum Generate.
4. **Output** — pilih folder output, nama file otomatis
   (`nama-video_nama-audio_TANGGAL`), ada warning overwrite.
5. Tekan **Generate**. Progress + log menampilkan langkah:
   probing → normalizing file i/n → concat → mux → verify → move used.
   Tombol Generate nonaktif selama proses berjalan.

## Mode Video Folder — soal klip yang sudah dipakai

- Setelah output **berhasil dan terverifikasi** (file ada, ukuran > 0,
  durasi ffprobe cocok dalam toleransi 1 detik), semua video yang
  dipakai **dipindah** ke satu folder sibling
  `<folder-sumber>_used` (dibuat otomatis, misal `D:\stok-video_used\`).
- **Tidak pernah hapus permanen.** File masih bisa dikembalikan manual.
- Folder `_used` tidak ikut di-scan lagi, jadi video itu tidak akan
  terpilih di output berikutnya.
- Ada log `used_log.txt` di folder `_used`: tanggal, nama output,
  dan daftar file yang dipindah.
- Kalau output **gagal verifikasi, tidak ada file yang dipindah**.

## Video concat — spek sama vs beda

- Tool cek spek tiap klip (lebar/tinggi/fps/codec) via ffprobe.
- Kalau **semua sama**: concat demuxer + `-c:v copy` (cepat, tanpa
  turun kualitas).
- Kalau **beda**: re-encode via concat filter ke spek umum
  1920×1080, 30 fps, libx264, CRF 18, preset veryfast, yuv420p.
  Ini perlu karena concat copy hanya aman untuk spek yang identik.

## Audio — kenapa begitu

YouTube selalu re-encode audio yang di-upload. Jadi yang penting
adalah memberi source paling bersih: AAC 320k 48 kHz stereo dari
source lossless, loudness -14 LUFS (standar normalisasi YouTube),
peak -1 dBTP. MP3 yang sudah lossy tidak di-encode ulang kalau bisa
dihindari (single-file mode), tapi di mode Folder semua file harus
disatukan ke satu format dulu supaya bisa digabung — jadi di situ
semuanya di-encode ke AAC 320k.

## File di repo ini

| File | Isi |
|---|---|
| `bringas_render_pc.py` | Aplikasi GUI Tkinter |
| `ffmpeg_utils.py` | Semua logika ffmpeg + ordering/pick/used (tanpa GUI) |
| `test_logic.py` | Self-test logika + integrasi ffmpeg (`python3 test_logic.py`) |
| `run.bat` | Launcher Windows (cek python + ffmpeg, lalu jalan) |
| `build_exe.bat` | Build `BringasRenderPC.exe` lokal di Windows (PyInstaller) |
| `.github/workflows/build-windows.yml` | Build .exe otomatis di GitHub Actions |
| `requirements.txt` | Kosong — stdlib only |
| `.gitignore` | Abaikan output, `*_used/`, temp, `__pycache__` |
| `LICENSE` | MIT |

## Test

```bash
python3 -m py_compile bringas_render_pc.py ffmpeg_utils.py test_logic.py
python3 test_logic.py
```

Di Linux (VM dev) ffmpeg/ffprobe tersedia, jadi test integrasi
(membuat klip + nada kecil, build audio folder, build video loop &
folder, verify, move ke _used) ikut jalan. Di Windows, pastikan
`ffmpeg.exe` / `ffprobe.exe` ketemu dulu sebelum Generate.

## Catatan v1

- Loudness target -14 LUFS / -1 dB **terkunci** (tidak bisa diubah di v1).
- Tombol Batalkan di v1 hanya mencegah generate berikutnya; proses
  ffmpeg yang sedang jalan tidak dimatikan paksa (supaya tidak ada
  file setengah jadi / file terpakai yang kehapus setengah jalan).
- Video 1-file diasumsikan seamless loop — tool tidak mengubahnya.
