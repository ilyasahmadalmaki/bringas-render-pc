"""
Self-test Bringas Render PC — pure logic + (if ffmpeg exists) integration.
Run:  python3 test_logic.py
Exit code 0 = all passed.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ffmpeg_utils as fu

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}  {detail}")


def test_pin_ordering():
    files = ["A.mp3", "B.mp3", "C.mp3", "D.mp3", "E.mp3"]
    # Pin E then B => E,B first (pin order, NOT alphabetical)
    out = fu.order_audio_files(files, ["E.mp3", "B.mp3"], random_shuffle=False)
    check("pin E lalu B => E,B di depan", out[:2] == ["E.mp3", "B.mp3"], str(out))
    check("pin: sisanya alphabetical", out[2:] == ["A.mp3", "C.mp3", "D.mp3"], str(out))
    # Unlimited pins
    out = fu.order_audio_files(files, ["C.mp3", "A.mp3", "E.mp3"])
    check("pin 3 file", out[:3] == ["C.mp3", "A.mp3", "E.mp3"], str(out))


def test_shuffle_unpinned():
    files = ["A.mp3", "B.mp3", "C.mp3", "D.mp3", "E.mp3", "F.mp3", "G.mp3"]
    rng = random.Random(42)
    out = fu.order_audio_files(files, ["C.mp3"], random_shuffle=True, rng=rng)
    check("shuffle: pin C tetap #1", out[0] == "C.mp3", str(out))
    check("shuffle: semua file tetap ada", sorted(out) == sorted(files), str(out))
    # Different seed -> (almost certainly) different unpinned order
    out2 = fu.order_audio_files(files, ["C.mp3"], random_shuffle=True,
                                rng=random.Random(7))
    check("shuffle: seed beda, urutan non-pin beda",
          out[1:] != out2[1:], f"{out} vs {out2}")


def test_delete_session_only(tmp):
    victim = os.path.join(tmp, "hapus_saya.mp3")
    Path(victim).write_bytes(b"fake")
    files = [victim, os.path.join(tmp, "lain.mp3")]
    pins = [victim]
    new_files, new_pins = fu.remove_from_list(files, victim, pins)
    check("delete: hilang dari list", victim not in new_files)
    check("delete: pin ikut hilang, pin lain geser", new_pins == [])
    check("delete: file di DISK tetap ada (session-only)",
          os.path.isfile(victim))
    check("delete: file lain tetap", len(new_files) == 1)


def test_pick_until_duration():
    clips = [("v1.mp4", 2.0), ("v2.mp4", 10.0), ("v3.mp4", 5.0), ("v4.mp4", 3.0)]
    rng = random.Random(1)
    picked, total = fu.pick_videos_until_duration(clips, 12.0, rng=rng)
    check("pick: total == target (trim terakhir)", abs(total - 12.0) < 1e-6,
          f"total={total} picked={picked}")
    check("pick: 1 putaran, tidak ada duplikat",
          len({p for p, _ in picked}) == len(picked), str(picked))
    check("pick: semua dari stok", all(p in [c[0] for c in clips] for p, _ in picked))
    # Stock shorter than target -> duplicate (cycle) until covered
    picked2, total2 = fu.pick_videos_until_duration(clips, 999.0, rng=random.Random(3))
    check("pick: stok kurang -> diduplikat sampai target",
          abs(total2 - 999.0) < 1e-6 and len(picked2) > len(clips),
          f"{picked2} {total2}")
    check("pick: duplikat tetap dari stok",
          all(p in [c[0] for c in clips] for p, _ in picked2))


def test_used_folder_path():
    check("used path Windows",
          fu.used_folder_path("D:\\stok-video") == "D:\\stok-video_used",
          fu.used_folder_path("D:\\stok-video"))
    check("used path POSIX",
          fu.used_folder_path("/tmp/stok-video") == "/tmp/stok-video_used",
          fu.used_folder_path("/tmp/stok-video"))
    check("used path trailing slash",
          fu.used_folder_path("/tmp/stok-video/") == "/tmp/stok-video_used")


def test_auto_output_name():
    n = fu.auto_output_name("klip-hujan.mp4", "lagu-pagi.wav")
    check("auto nama mengandung video+audio",
          n.startswith("klip-hujan_lagu-pagi_"), n)
    check("sanitize: karakter ilegal dibuang",
          fu.sanitize_filename('a/b:c*d?e"f') == "a_b_c_d_e_f",
          fu.sanitize_filename('a/b:c*d?e"f'))


def test_integration_ffmpeg(tmp):
    ff = shutil_which("ffmpeg")
    fp = shutil_which("ffprobe")
    if not ff or not fp:
        print("SKIP  integration: ffmpeg/ffprobe tidak tersedia di VM ini")
        return
    print("integration: ffmpeg tersedia, buat klip + nada tes…")
    # 2 tiny video clips (different durations) + 2 tiny tones
    clips = []
    for name, dur, color in (("c1.mp4", 2, "red"), ("c2.mp4", 3, "blue")):
        p = os.path.join(tmp, name)
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=320x240:d={dur}",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", p],
            capture_output=True, text=True)
        check(f"integration: buat {name}", r.returncode == 0 and os.path.isfile(p))
        clips.append((p, float(dur)))
    tones = []
    for name, dur, freq in (("t1.wav", 2, 440), ("t2.wav", 3, 660)):
        p = os.path.join(tmp, name)
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
             "-ar", "48000", "-ac", "2", p], capture_output=True, text=True)
        check(f"integration: buat {name}", r.returncode == 0 and os.path.isfile(p))
        tones.append(p)

    work = os.path.join(tmp, "work")
    os.makedirs(work, exist_ok=True)
    log = lambda m: None
    # Audio folder build: normalize each -> AAC, concat copy
    audio_out, total, lufs = fu.build_audio_folder(tones, work, log=log)
    check("integration: build_audio_folder ok",
          audio_out is not None and os.path.isfile(audio_out), str(audio_out))
    check("integration: total durasi audio folder ~5s",
          audio_out is not None and abs(total - 5.0) < 0.6, f"total={total}")
    if audio_out:
        d = fu.probe_duration(audio_out)
        check("integration: audio gabungan durasi ~5s", abs(d - 5.0) < 0.6, f"d={d}")
        # Best mode (dual-pass loudnorm) juga wajib keluar 48 kHz,
        # bukan 96 kHz hasil upsample loudnorm (bug 2026-11-25).
        rate = fu.probe_specs(audio_out).sample_rate
        check("integration: audio gabungan sample rate 48000",
              rate == 48000, f"rate={rate}")

    # Video folder build with picked clips (need 5s audio)
    picked, ptotal = fu.pick_videos_until_duration(clips, 5.0, rng=random.Random(0))
    out_mp4 = os.path.join(tmp, "out_folder.mp4")
    ok = fu.build_video_folder(picked, audio_out, 5.0, out_mp4, work, log=log)
    check("integration: build_video_folder ok", ok and os.path.isfile(out_mp4))
    if ok:
        good, msg = fu.verify_output(out_mp4, 5.0)
        check(f"integration: verify_output ({msg})", good)

    # Video loop build (1 file, copy)
    loop_out = os.path.join(tmp, "out_loop.mp4")
    ok = fu.build_video_loop(clips[0][0], audio_out, 5.0, loop_out, log=log)
    check("integration: build_video_loop ok", ok and os.path.isfile(loop_out))
    if ok:
        good, msg = fu.verify_output(loop_out, 5.0)
        check(f"integration: verify loop ({msg})", good)

    # Move used videos -> _used (single folder), log written
    src_folder = os.path.join(tmp, "stok")
    os.makedirs(src_folder, exist_ok=True)
    used_srcs = []
    for name in ("u1.mp4", "u2.mp4"):
        p = os.path.join(src_folder, name)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=160x120:d=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", p],
                       capture_output=True)
        used_srcs.append(p)
    used_folder, moved = fu.move_used_videos(used_srcs, src_folder, "out_test.mp4", log=log)
    check("integration: move ke _used", len(moved) == 2 and
          all(os.path.isfile(m) for m in moved), str(moved))
    check("integration: file asli sudah tidak di sumber",
          all(not os.path.isfile(p) for p in used_srcs))
    check("integration: used_log.txt ada",
          os.path.isfile(os.path.join(used_folder, "used_log.txt")))


def shutil_which(name):
    import shutil
    return shutil.which(name)


def test_unique_output_path(tmp):
    p = os.path.join(tmp, "video.mp4")
    check("unique: belum ada -> sama", fu.unique_output_path(p) == p)
    Path(p).write_bytes(b"x")
    check("unique: sudah ada -> (1)",
          fu.unique_output_path(p) == os.path.join(tmp, "video (1).mp4"))
    Path(os.path.join(tmp, "video (1).mp4")).write_bytes(b"x")
    check("unique: (1) ada -> (2)",
          fu.unique_output_path(p) == os.path.join(tmp, "video (2).mp4"))
    # Explorer-style: nama yang sudah bernomor naik satu tingkat
    p1 = os.path.join(tmp, "video (1).mp4")
    check("unique: minta 'video (1)' lagi -> (2)",
          fu.unique_output_path(p1) == os.path.join(tmp, "video (2).mp4"))
    pe = os.path.join(tmp, "tanpa-ekstensi")
    Path(pe).write_bytes(b"x")
    check("unique: tanpa ekstensi -> (1)",
          fu.unique_output_path(pe) == os.path.join(tmp, "tanpa-ekstensi (1)"))


def test_gpu_detection():
    fake_all = (
        "Encoders:\n"
        " V..... = Video\n"
        " ------\n"
        " V..... libx264              libx264 H.264 / AVC (codec h264)\n"
        " V..... h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)\n"
        " V..... h264_qsv             Intel Quick Sync Video H.264 encoder (codec h264)\n"
        " V..... h264_amf             AMD AMF H.264 Video Encoder (codec h264)\n"
    )
    check("gpu: prioritas NVENC saat ketiganya ada",
          fu.parse_encoders_output(fake_all) == "h264_nvenc",
          str(fu.parse_encoders_output(fake_all)))
    check("gpu: daftar kandidat urut prioritas",
          fu.parse_encoders_list(fake_all) == ["h264_nvenc", "h264_qsv", "h264_amf"],
          str(fu.parse_encoders_list(fake_all)))
    check("gpu: QSV saat NVENC tidak ada",
          fu.parse_encoders_output(" V..... h264_qsv  Intel QSV (codec h264)\n"
                                   " V..... h264_amf  AMD AMF (codec h264)\n") == "h264_qsv")
    check("gpu: AMF saat hanya AMF",
          fu.parse_encoders_output(" V..... h264_amf  AMD AMF (codec h264)\n") == "h264_amf")
    check("gpu: None saat tidak ada hardware encoder",
          fu.parse_encoders_output(" V..... libx264  libx264 (codec h264)\n") is None)
    check("gpu: teks kosong -> None", fu.parse_encoders_output("") is None)


def test_video_encoder_args():
    check("gpu args: default = libx264 crf 18",
          fu.video_encoder_args() == ["-c:v", "libx264", "-crf", "18",
                                      "-preset", "veryfast", "-pix_fmt", "yuv420p"])
    nv = fu.video_encoder_args("h264_nvenc")
    check("gpu args: NVENC pakai p4 + cq 19",
          nv[:4] == ["-c:v", "h264_nvenc", "-preset", "p4"] and "-cq" in nv and "19" in nv,
          str(nv))
    check("gpu args: QSV pakai global_quality",
          "-global_quality" in fu.video_encoder_args("h264_qsv"))
    amf = fu.video_encoder_args("h264_amf")
    check("gpu args: AMF pakai cqp",
          "-rc" in amf and "cqp" in amf, str(amf))
    check("gpu args: encoder asing -> fallback libx264",
          fu.video_encoder_args("h264_foo") == fu.video_encoder_args())


def test_detect_gpu_encoder_live():
    # Runs against the real ffmpeg on this machine (if any). No GPU here
    # is expected; the point is it never raises and caches consistently.
    # Note: `ffmpeg -encoders` alone is NOT proof — this VM lists
    # h264_nvenc but the 1-frame probe must reject it (no NVIDIA driver).
    enc1 = fu.detect_gpu_encoder()
    enc2 = fu.detect_gpu_encoder()
    check("gpu detect: hasil valid (None atau encoder dikenal)",
          enc1 is None or enc1 in fu._GPU_ENCODERS, str(enc1))
    check("gpu detect: hasil konsisten (cache)", enc1 == enc2)
    check("gpu detect: probe menolak encoder yang tidak dikenal",
          fu._encoder_probe_ok(fu.find_ffmpeg(), "h264_tidak_ada") is False)
    check("gpu detect: encoder terdaftar tapi tak bisa jalan -> None",
          enc1 is None)


def _make_tone(path, dur, freq=440):
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={dur}",
         "-ar", "48000", "-ac", "2", path],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]


def test_fast_encode(tmp):
    if not shutil_which("ffmpeg"):
        print("SKIP  fast encode: ffmpeg tidak tersedia")
        return
    src = os.path.join(tmp, "fast.wav")
    _make_tone(src, 2)
    dst = os.path.join(tmp, "fast.m4a")
    ok, in_lufs, _ = fu.loudnorm_encode_file(src, dst, fast=True)
    check("fast: single-pass encode ok", ok and os.path.isfile(dst)
          and os.path.getsize(dst) > 0)
    if ok:
        d = fu.probe_duration(dst)
        check("fast: output durasi ~2s", abs(d - 2.0) < 0.6, f"d={d}")
        # Bug 2026-11-25: loudnorm upsampled the output to 96 kHz and
        # YouTube Live rejected it (sample rate 0). Output WAJIB 48k.
        rate = fu.probe_specs(dst).sample_rate
        check("fast: sample rate output 48000 (bukan 96000)",
              rate == 48000, f"rate={rate}")


def test_norm_cache(tmp):
    if not shutil_which("ffmpeg"):
        print("SKIP  norm cache: ffmpeg tidak tersedia")
        return
    src = os.path.join(tmp, "cache.wav")
    _make_tone(src, 2)
    cache = os.path.join(tmp, "cache_dir")
    dst1 = os.path.join(tmp, "c1.m4a")
    dst2 = os.path.join(tmp, "c2.m4a")
    ok1, _, _ = fu.loudnorm_encode_file(src, dst1, fast=True, cache_dir=cache)
    files_after_1 = os.listdir(cache) if os.path.isdir(cache) else []
    ok2, _, _ = fu.loudnorm_encode_file(src, dst2, fast=True, cache_dir=cache)
    check("cache: encode pertama ok + 1 file cache",
          ok1 and len(files_after_1) == 1, str(files_after_1))
    check("cache: encode kedua ok (cache hit)",
          ok2 and os.path.isfile(dst2) and os.path.getsize(dst2) > 0)
    check("cache: hasil kedua == hasil pertama (byte-identik)",
          ok1 and ok2 and Path(dst1).read_bytes() == Path(dst2).read_bytes())
    # Mode beda -> key beda -> file cache baru
    dst3 = os.path.join(tmp, "c3.m4a")
    fu.loudnorm_encode_file(src, dst3, fast=False, cache_dir=cache)
    check("cache: mode best vs fast punya key sendiri",
          len(os.listdir(cache)) == 2, str(os.listdir(cache)))


def test_pick_random_songs():
    files = [f"s{i}.mp3" for i in range(1, 21)]  # 20 lagu
    pins = ["s3.mp3", "s7.mp3"]
    # 12 dari 20: 2 pin + 10 acak
    out = fu.pick_random_songs(files, pins, 12, rng=random.Random(42))
    check("pick: jumlah tepat 12", len(out) == 12, str(out))
    check("pick: pin tidak pernah dibuang, di depan sesuai urutan pin",
          out[:2] == pins, str(out))
    check("pick: semua dari list asli, tanpa duplikat",
          len(set(out)) == 12 and all(f in files for f in out), str(out))
    check("pick: seed sama -> hasil sama",
          out == fu.pick_random_songs(files, pins, 12, rng=random.Random(42)))
    check("pick: seed beda -> (hampir pasti) hasil beda",
          out != fu.pick_random_songs(files, pins, 12, rng=random.Random(7)),
          str(out))
    # count >= total -> semua; count 0/None -> semua
    check("pick: count >= total -> semua file",
          fu.pick_random_songs(files, pins, 20) == files)
    check("pick: count 25 (> total) -> semua file",
          fu.pick_random_songs(files, pins, 25) == files)
    check("pick: count 0 -> semua file",
          fu.pick_random_songs(files, pins, 0) == files)
    check("pick: count None -> semua file",
          fu.pick_random_songs(files, pins, None) == files)
    # Pin lebih banyak dari count -> semua pin tetap dipertahankan
    many_pins = [f"s{i}.mp3" for i in range(1, 6)]
    check("pick: pin (5) > count (3) -> semua pin dipertahankan",
          fu.pick_random_songs(files, many_pins, 3) == many_pins)
    # Tanpa pin: murni sample acak
    out2 = fu.pick_random_songs(files, [], 5, rng=random.Random(1))
    check("pick: tanpa pin -> 5 acak dari list",
          len(out2) == 5 and all(f in files for f in out2), str(out2))


def test_merge_file_lists():
    base = ["a.mp3", "b.mp3"]
    out = fu.merge_file_lists(base, ["b.mp3", "c.mp3", "d.mp3"])
    check("merge: urutan lama dipertahankan, baru nempel di belakang",
          out == ["a.mp3", "b.mp3", "c.mp3", "d.mp3"], str(out))
    check("merge: duplikat tidak dobel", len(out) == len(set(out)))
    out2 = fu.merge_file_lists(base, ["x.mp3", "a.mp3"], deleted=["x.mp3"])
    check("merge: file yang dihapus sesi ini tidak balik lagi",
          out2 == ["a.mp3", "b.mp3"], str(out2))
    check("merge: list kosong + folder baru",
          fu.merge_file_lists([], ["m.mp3", "n.mp3"]) == ["m.mp3", "n.mp3"])


def test_format_timestamp():
    check("timestamp: 0 -> 0:00", fu.format_timestamp(0) == "0:00")
    check("timestamp: 5 -> 0:05", fu.format_timestamp(5) == "0:05")
    check("timestamp: 65 -> 1:05", fu.format_timestamp(65) == "1:05")
    check("timestamp: 273.2 -> 4:33", fu.format_timestamp(273.2) == "4:33")
    check("timestamp: 3600 -> 1:00:00", fu.format_timestamp(3600) == "1:00:00")
    check("timestamp: 3661 -> 1:01:01", fu.format_timestamp(3661) == "1:01:01")
    check("timestamp: negatif -> 0:00", fu.format_timestamp(-3) == "0:00")


def test_build_chapters_text():
    entries = [("A THOUSAND MILES AWAY.mp3", 273.2),
               ("ELECTRIC FEELING.mp3", 269.9),
               ("INTO YOUR LIGHT.mp3", 262.2)]
    text = fu.build_chapters_text(entries)
    check("chapters: baris pertama 0:00",
          text.splitlines()[0] == "0:00 A THOUSAND MILES AWAY",
          text)
    check("chapters: lagu ke-2 mulai di 4:33 (273.2s)",
          text.splitlines()[1] == "4:33 ELECTRIC FEELING", text)
    check("chapters: lagu ke-3 mulai di 9:03 (273.2+269.9=543.1s)",
          text.splitlines()[2] == "9:03 INTO YOUR LIGHT", text)
    check("chapters: judul '_' jadi spasi, tanpa ekstensi",
          fu.chapter_title("my_song-title_v2.wav") == "my song-title v2")
    check("chapters: list kosong -> teks kosong",
          fu.build_chapters_text([]) == "")


def test_write_timestamps_file(tmp):
    entries = [("lagu_a.mp3", 120.0), ("lagu_b.mp3", 180.0)]
    p = os.path.join(tmp, "out_timestamps.txt")
    text = fu.write_timestamps_file(p, entries)
    check("timestamps file: tertulis", os.path.isfile(p))
    check("timestamps file: isi benar",
          Path(p).read_text(encoding="utf-8") == "0:00 lagu a\n2:00 lagu b\n",
          repr(text))


def test_single_wav_sample_rate(tmp):
    if not shutil_which("ffmpeg"):
        print("SKIP  single WAV rate: ffmpeg tidak tersedia")
        return
    src = os.path.join(tmp, "single.wav")
    _make_tone(src, 2)
    work = os.path.join(tmp, "work_single")
    os.makedirs(work, exist_ok=True)
    # WAV 48kHz input -> loudnorm dual-pass (best) -> AAC harus 48kHz.
    out, dur = fu.build_audio_single(src, work, log=lambda m: None)
    check("single WAV: build ok", out is not None and os.path.isfile(out),
          str(out))
    if out:
        rate = fu.probe_specs(out).sample_rate
        check("single WAV: sample rate output 48000 (bukan 96000)",
              rate == 48000, f"rate={rate}")


def test_cancel_run():
    if not shutil_which("ffmpeg"):
        print("SKIP  cancel run: ffmpeg tidak tersedia")
        return
    ctrl = fu.RenderControl()
    # -re forces realtime: 120s of audio, cancel after 0.5s must land mid-run.
    cmd = ["ffmpeg", "-hide_banner", "-re", "-f", "lavfi",
           "-i", "sine=frequency=440:duration=120", "-f", "null", "-"]
    result: dict = {}

    def _go():
        t0 = time.monotonic()
        try:
            fu._run(cmd, control=ctrl)
            result["raised"] = False
        except fu.RenderCancelled:
            result["raised"] = True
        result["elapsed"] = time.monotonic() - t0

    th = threading.Thread(target=_go, daemon=True)
    th.start()
    time.sleep(0.5)
    ctrl.request_cancel()
    th.join(timeout=20)
    check("cancel: _run raise RenderCancelled", result.get("raised") is True,
          str(result))
    check("cancel: berhenti cepat (<20s, bukan 120s)",
          result.get("elapsed", 999) < 20, str(result))
    check("cancel: thread benar-benar selesai", not th.is_alive())


def test_parallel_folder_fast(tmp):
    if not shutil_which("ffmpeg"):
        print("SKIP  parallel folder: ffmpeg tidak tersedia")
        return
    tones = []
    for i, freq in enumerate((440, 550, 660, 770), 1):
        p = os.path.join(tmp, f"p{i}.wav")
        _make_tone(p, 2, freq)
        tones.append(p)
    work = os.path.join(tmp, "work_par")
    os.makedirs(work, exist_ok=True)
    out, total, lufs = fu.build_audio_folder(
        tones, work, log=lambda m: None, fast=True, workers=2)
    check("paralel: build_audio_folder ok",
          out is not None and os.path.isfile(out), str(out))
    check("paralel: total ~8s", abs(total - 8.0) < 0.8, f"total={total}")
    check("paralel: urutan output tetap urutan input",
          [f for f, _ in lufs] == tones, str(lufs))
    if out:
        d = fu.probe_duration(out)
        check("paralel: audio gabungan durasi ~8s", abs(d - 8.0) < 0.8, f"d={d}")


def test_cancel_folder(tmp):
    if not shutil_which("ffmpeg"):
        print("SKIP  cancel folder: ffmpeg tidak tersedia")
        return
    tones = []
    for i, freq in enumerate((440, 550, 660), 1):
        p = os.path.join(tmp, f"cf{i}.wav")
        _make_tone(p, 60, freq)
        tones.append(p)
    work = os.path.join(tmp, "work_cancel")
    os.makedirs(work, exist_ok=True)
    ctrl = fu.RenderControl()
    result: dict = {}

    def _go():
        t0 = time.monotonic()
        try:
            fu.build_audio_folder(tones, work, log=lambda m: None,
                                  fast=True, control=ctrl)
            result["raised"] = False
        except fu.RenderCancelled:
            result["raised"] = True
        result["elapsed"] = time.monotonic() - t0

    th = threading.Thread(target=_go, daemon=True)
    th.start()
    time.sleep(1.0)
    ctrl.request_cancel()
    th.join(timeout=25)
    check("cancel folder: raise RenderCancelled", result.get("raised") is True,
          str(result))
    check("cancel folder: berhenti cepat (<25s, bukan puluhan detik)",
          result.get("elapsed", 999) < 25, str(result))
    check("cancel folder: thread benar-benar selesai", not th.is_alive())


def main():
    print("== Bringas Render PC — self test ==")
    with tempfile.TemporaryDirectory(prefix="bringas_test_") as tmp:
        test_pin_ordering()
        test_shuffle_unpinned()
        test_delete_session_only(tmp)
        test_pick_until_duration()
        test_used_folder_path()
        test_auto_output_name()
        test_gpu_detection()
        test_video_encoder_args()
        test_detect_gpu_encoder_live()
        test_unique_output_path(tmp)
        test_integration_ffmpeg(tmp)
        test_fast_encode(tmp)
        test_norm_cache(tmp)
        test_pick_random_songs()
        test_merge_file_lists()
        test_format_timestamp()
        test_build_chapters_text()
        test_write_timestamps_file(tmp)
        test_single_wav_sample_rate(tmp)
        test_cancel_run()
        test_parallel_folder_fast(tmp)
        test_cancel_folder(tmp)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
