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
    check("pick: tidak ada duplikat dalam 1 output",
          len({p for p, _ in picked}) == len(picked), str(picked))
    check("pick: semua dari stok", all(p in [c[0] for c in clips] for p, _ in picked))
    # Insufficient stock
    picked2, total2 = fu.pick_videos_until_duration(clips, 999.0, rng=random.Random(3))
    check("pick: stok kurang -> kembalikan semua, total < target",
          len(picked2) == 4 and total2 == 20.0, f"{picked2} {total2}")


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


def main():
    print("== Bringas Render PC — self test ==")
    with tempfile.TemporaryDirectory(prefix="bringas_test_") as tmp:
        test_pin_ordering()
        test_shuffle_unpinned()
        test_delete_session_only(tmp)
        test_pick_until_duration()
        test_used_folder_path()
        test_auto_output_name()
        test_integration_ffmpeg(tmp)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
