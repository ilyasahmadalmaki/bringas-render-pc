"""
Bringas Render PC — ffmpeg_utils.py
Core logic (no GUI) for Bringas Render PC.

Locked spec (2026-11-08, user discussion):
- Video 1 File: seamless loop, -stream_loop -1, -c:v copy, duration = audio.
- Video Folder: use ALL short clips (2-10s) in random order; if the total
  is shorter than the audio, repeat the set (reshuffled each pass) until
  it covers the audio; if longer, trim to audio duration. Nothing is
  consumed, moved, or deleted — the source folder stays intact.
- Audio 1 File: WAV/FLAC -> AAC 320k 48kHz stereo (loudnorm -14 LUFS);
  MP3 -> -c:a copy by default.
- Audio Folder: combine ALL files, alphabetical default, pins first in
  pin order (unlimited), unpinned alphabetical (Random OFF) or true
  shuffle (Random ON), session-only delete from list, hard cut, each
  file loudnorm dual-pass -14 LUFS / -1 dBTP linear=true, soxr 48kHz,
  encode one-by-one to AAC 320k, then concat AAC with -c copy.
- Final audio: AAC 320k 48kHz stereo (except single-file MP3 copy).
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

AUDIO_EXTS = {".wav", ".mp3", ".flac"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts"}
LOUDNESS_TARGET_I = -14.0   # LUFS, locked in v1
LOUDNESS_TARGET_TP = -1.0   # dBTP, locked in v1
LOUDNESS_TARGET_LRA = 11.0
AUDIO_BITRATE = "320k"
AUDIO_RATE = 48000

LogFn = Callable[[str], None]


def _noop_log(msg: str) -> None:
    pass


# --------------------------------------------------------------------------
# ffmpeg / ffprobe discovery
# --------------------------------------------------------------------------

def find_ffmpeg(explicit: Optional[str] = None) -> str:
    """Return ffmpeg executable path. App folder first, then PATH."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    here = Path(__file__).resolve().parent
    for name in ("ffmpeg.exe", "ffmpeg"):
        candidates.append(str(here / name))
    candidates.append("ffmpeg")
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
        if os.path.isfile(c):
            return c
    return "ffmpeg"


def find_ffprobe(explicit: Optional[str] = None) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    here = Path(__file__).resolve().parent
    for name in ("ffprobe.exe", "ffprobe"):
        candidates.append(str(here / name))
    candidates.append("ffprobe")
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
        if os.path.isfile(c):
            return c
    return "ffprobe"


def _no_window_kwargs() -> dict:
    """Windows: hide the console window for ffmpeg/ffprobe children.

    The app is built --windowed (no console of its own), but ffmpeg.exe
    and ffprobe.exe are console programs: without CREATE_NO_WINDOW every
    spawn pops up a visible black cmd window (during render, for the
    whole render; during probing, a flash per file).
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _run(cmd: List[str], log: LogFn = _noop_log) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **_no_window_kwargs())


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------

@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    codec: str = ""
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0


def _to_float(v) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _fps(fr) -> float:
    try:
        num, den = str(fr).split("/")
        return float(num) / float(den) if float(den) else 0.0
    except Exception:
        return 0.0


def _duration_from_probe(data: dict) -> float:
    """Best-effort duration from one ffprobe JSON dump.

    Fallback chain (some AI-generated MP4s carry no duration in the
    container header, so -show_format alone reports 0 and the file
    used to be silently dropped from the video folder):
    1. format duration
    2. any stream duration (video first, then the rest)
    3. video frames / fps
    """
    dur = _to_float((data.get("format") or {}).get("duration"))
    if dur > 0:
        return dur
    streams = data.get("streams") or []
    for s in streams:
        if s.get("codec_type") == "video":
            d = _to_float(s.get("duration"))
            if d > 0:
                return d
    for s in streams:
        d = _to_float(s.get("duration"))
        if d > 0:
            return d
    for s in streams:
        if s.get("codec_type") == "video":
            frames = _to_float(s.get("nb_frames"))
            fps = _fps(s.get("avg_frame_rate") or s.get("r_frame_rate"))
            if frames > 0 and fps > 0:
                return frames / fps
    return 0.0


def probe_duration(path: str, ffprobe: Optional[str] = None) -> float:
    """Duration in seconds via ffprobe, 0.0 on failure."""
    fp = find_ffprobe(ffprobe)
    cmd = [fp, "-v", "quiet", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           **_no_window_kwargs())
        data = json.loads(r.stdout or "{}")
        return _duration_from_probe(data)
    except Exception:
        return 0.0


def probe_specs(path: str, ffprobe: Optional[str] = None) -> MediaInfo:
    """Width/height/fps/codec + duration via ffprobe."""
    info = MediaInfo(path=path)
    fp = find_ffprobe(ffprobe)
    cmd = [fp, "-v", "quiet", "-print_format", "json",
           "-show_format", "-show_streams", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                           **_no_window_kwargs())
        data = json.loads(r.stdout or "{}")
        info.duration = _duration_from_probe(data)
        for s in data.get("streams", []):
            if s.get("codec_type") == "video" and not info.codec:
                info.codec = s.get("codec_name", "")
                info.width = int(s.get("width", 0) or 0)
                info.height = int(s.get("height", 0) or 0)
                fr = s.get("r_frame_rate") or s.get("avg_frame_rate") or "0/1"
                try:
                    num, den = fr.split("/")
                    info.fps = float(num) / float(den) if float(den) else 0.0
                except Exception:
                    info.fps = 0.0
            elif s.get("codec_type") == "audio" and not info.audio_codec:
                info.audio_codec = s.get("codec_name", "")
                info.sample_rate = int(s.get("sample_rate", 0) or 0)
                info.channels = int(s.get("channels", 0) or 0)
    except Exception:
        pass
    return info


# --------------------------------------------------------------------------
# Pure ordering logic (testable without ffmpeg)
# --------------------------------------------------------------------------

def order_audio_files(
    files: Sequence[str],
    pin_order: Sequence[str],
    random_shuffle: bool = False,
    rng: Optional[random.Random] = None,
) -> List[str]:
    """
    Pinned files first, in pin order (1st pinned = #1, unlimited pins).
    Unpinned: alphabetical when Random OFF, true random.shuffle when ON.
    Files in pin_order that are not in `files` are ignored.
    """
    file_set = list(files)
    pinned = [f for f in pin_order if f in file_set]
    pinned_set = set(pinned)
    rest = [f for f in file_set if f not in pinned_set]
    if random_shuffle:
        r = rng or random
        r.shuffle(rest)
    else:
        rest.sort(key=lambda p: os.path.basename(p).lower())
    return pinned + rest


def remove_from_list(files: Sequence[str], to_remove: str,
                     pin_order: Sequence[str]) -> Tuple[List[str], List[str]]:
    """
    Session-only delete: remove from the in-memory list only.
    Disk is never touched. If the removed file was pinned, its pin is
    removed and remaining pins shift up (they keep their relative order).
    Returns (new_files, new_pin_order).
    """
    new_files = [f for f in files if f != to_remove]
    new_pins = [f for f in pin_order if f != to_remove]
    return new_files, new_pins


def pick_videos_until_duration(
    clips: Sequence[Tuple[str, float]],
    target_duration: float,
    rng: Optional[random.Random] = None,
) -> Tuple[List[Tuple[str, float]], float]:
    """
    Use ALL clips in the folder (shuffled, no bias by name/date), then
    repeat the set (reshuffled each pass) until total >= target_duration.
    The last clip's duration_used is trimmed so the sum equals
    target_duration exactly (when the raw sum overshoots).

    Returns (picked, total_picked_duration) where each picked entry is
    (path, duration_used). A clip may appear several times when the
    stock is shorter than the target (duplication); within one pass no
    clip repeats. Nothing is consumed or removed — the source folder
    stays intact.
    """
    usable = [(p, d) for p, d in clips if d > 0]
    if not usable or target_duration <= 0:
        return [], 0.0
    r = rng or random
    picked: List[Tuple[str, float]] = []
    total = 0.0
    while total < target_duration:
        shuffled = list(usable)
        r.shuffle(shuffled)
        for path, dur in shuffled:
            if total >= target_duration:
                break
            if total + dur > target_duration:
                used = target_duration - total
                picked.append((path, used))
                total = target_duration
                break
            picked.append((path, dur))
            total += dur
    return picked, total


def used_folder_path(source_folder: str) -> str:
    """
    Sibling folder "<source_folder>_used", auto-created by the caller.
    Handles both Windows (D:\\stok-video) and POSIX (/x/stok-video) paths,
    including when running on Linux with a Windows-style string (tests).
    """
    s = source_folder.rstrip("/\\")
    if "\\" in s:
        # Windows-style path, even on a POSIX host.
        return s + "_used"
    p = Path(s)
    return str(p.parent / (p.name + "_used"))


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:120] or "output"


def auto_output_name(video_name: str, audio_name: str,
                     when: Optional[datetime] = None) -> str:
    """video name + audio name + date, sanitized, without extension."""
    when = when or datetime.now()
    v = sanitize_filename(Path(video_name).stem)
    a = sanitize_filename(Path(audio_name).stem)
    return f"{v}_{a}_{when.strftime('%Y%m%d_%H%M')}"


# --------------------------------------------------------------------------
# Loudnorm (dual-pass, best quality)
# --------------------------------------------------------------------------

@dataclass
class LoudnormMeasured:
    input_i: float = 0.0
    input_tp: float = 0.0
    input_lra: float = 0.0
    input_thresh: float = 0.0
    target_offset: float = 0.0
    linear: bool = True

    def to_filter_args(self) -> str:
        return (
            f"measured_I={self.input_i}:measured_TP={self.input_tp}:"
            f"measured_LRA={self.input_lra}:measured_thresh={self.input_thresh}:"
            f"offset={self.target_offset}:linear={'true' if self.linear else 'false'}"
        )


_LOUDNORM_JSON_RE = re.compile(r"\{[^}]*\"input_i\"[^}]*\}", re.DOTALL)


def loudnorm_measure(path: str, log: LogFn = _noop_log,
                     ffmpeg: Optional[str] = None) -> Optional[LoudnormMeasured]:
    """Pass 1: measure loudness with loudnorm, return measured values."""
    ff = find_ffmpeg(ffmpeg)
    filt = (f"loudnorm=I={LOUDNESS_TARGET_I}:TP={LOUDNESS_TARGET_TP}:"
            f"LRA={LOUDNESS_TARGET_LRA}:print_format=json")
    cmd = [ff, "-hide_banner", "-i", path, "-vn", "-af", filt,
           "-f", "null", "-"]
    r = _run(cmd, log)
    m = _LOUDNORM_JSON_RE.search(r.stderr or "")
    if not m:
        log(f"  loudnorm measure: no JSON for {os.path.basename(path)}")
        return None
    try:
        d = json.loads(m.group(0))
        return LoudnormMeasured(
            input_i=float(d["input_i"]), input_tp=float(d["input_tp"]),
            input_lra=float(d["input_lra"]),
            input_thresh=float(d["input_thresh"]),
            target_offset=float(d.get("target_offset", 0.0)),
            linear=True)
    except Exception as e:
        log(f"  loudnorm measure parse failed: {e}")
        return None


def loudnorm_encode_file(
    src: str, dst: str, log: LogFn = _noop_log,
    ffmpeg: Optional[str] = None,
    measured: Optional[LoudnormMeasured] = None,
) -> Tuple[bool, Optional[float], Optional[float]]:
    """
    Pass 2 (or single-pass fallback): encode src -> dst AAC 320k 48kHz
    stereo, normalized to -14 LUFS / -1 dBTP, linear=true, soxr resample.
    Returns (ok, input_lufs, output_lufs). Output LUFS is re-measured
    only if the caller asks via a second measure call; here we return
    the measured input LUFS and None for output (cheap path).
    If soxr is unavailable in this ffmpeg build, falls back to the
    default resampler.
    """
    ff = find_ffmpeg(ffmpeg)
    if measured is None:
        measured = loudnorm_measure(src, log=log, ffmpeg=ffmpeg)
    base = (f"loudnorm=I={LOUDNESS_TARGET_I}:TP={LOUDNESS_TARGET_TP}:"
            f"LRA={LOUDNESS_TARGET_LRA}:linear=true")
    if measured is not None:
        base += ":" + measured.to_filter_args()
    for resampler in ("soxr", None):
        if resampler:
            af = f"aresample={AUDIO_RATE}:resampler={resampler},{base}"
        else:
            af = f"aresample={AUDIO_RATE},{base}"
        cmd = [ff, "-hide_banner", "-y", "-i", src, "-vn",
               "-af", af, "-c:a", "aac", "-b:a", AUDIO_BITRATE,
               "-ac", "2", dst]
        r = _run(cmd, log)
        if r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
            return True, (measured.input_i if measured else None), None
        log(f"  encode failed (resampler={resampler}), retrying" if resampler
            else "  encode failed with default resampler")
    return False, (measured.input_i if measured else None), None


def encode_audio_aac(src: str, dst: str, log: LogFn = _noop_log,
                     ffmpeg: Optional[str] = None) -> bool:
    """Plain WAV/FLAC -> AAC 320k 48kHz stereo (no loudnorm)."""
    ff = find_ffmpeg(ffmpeg)
    for resampler in ("soxr", None):
        af = (f"aresample={AUDIO_RATE}:resampler={resampler}" if resampler
              else f"aresample={AUDIO_RATE}")
        cmd = [ff, "-hide_banner", "-y", "-i", src, "-vn", "-af", af,
               "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2", dst]
        if _run(cmd, log).returncode == 0 and os.path.isfile(dst):
            return True
    return False


# --------------------------------------------------------------------------
# Audio builds
# --------------------------------------------------------------------------

def build_audio_single(src: str, workdir: str, log: LogFn = _noop_log,
                       ffmpeg: Optional[str] = None,
                       normalize: bool = True) -> Tuple[Optional[str], float]:
    """
    Single-file audio -> prepared audio file (AAC .m4a, or the original
    MP3 itself when copying). Returns (prepared_path, duration).
    WAV/FLAC: loudnorm dual-pass (default) -> AAC 320k 48kHz stereo.
    MP3: -c:a copy by default (avoid double lossy encode); the caller
    muxes it directly. Set normalize=False + force re-encode only via
    the GUI option (not used by default).
    """
    ext = Path(src).suffix.lower()
    dur = probe_duration(src)
    os.makedirs(workdir, exist_ok=True)
    if ext == ".mp3":
        log(f"Audio single: MP3 copy (tanpa re-encode): {os.path.basename(src)}")
        return src, dur
    dst = os.path.join(workdir, "audio_single.m4a")
    if normalize:
        ok, in_lufs, _ = loudnorm_encode_file(src, dst, log=log, ffmpeg=ffmpeg)
        if ok:
            log(f"Audio single: WAV/FLAC loudnorm {in_lufs} -> -14 LUFS, AAC 320k")
            return dst, dur
        log("Audio single: loudnorm gagal, fallback encode AAC biasa")
    if encode_audio_aac(src, dst, log=log, ffmpeg=ffmpeg):
        return dst, dur
    return None, dur


def concat_aac_copy(inputs: Sequence[str], dst: str,
                    log: LogFn = _noop_log,
                    ffmpeg: Optional[str] = None) -> bool:
    """Concat AAC (.m4a) intermediates with the concat demuxer, -c copy."""
    ff = find_ffmpeg(ffmpeg)
    list_path = os.path.join(os.path.dirname(dst) or ".", "concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in inputs:
            esc = str(p).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")
    cmd = [ff, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
           "-i", list_path, "-c", "copy", dst]
    r = _run(cmd, log)
    return r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0


def build_audio_folder(ordered_files: Sequence[str], workdir: str,
                       log: LogFn = _noop_log,
                       ffmpeg: Optional[str] = None,
                       progress: Optional[Callable[[int, int, str], None]] = None,
                       ) -> Tuple[Optional[str], float, List[Tuple[str, Optional[float]]]]:
    """
    Folder audio: normalize EACH file individually (dual-pass loudnorm,
    -14 LUFS / -1 dBTP, soxr 48kHz, AAC 320k, one-by-one, best quality),
    then concat the AAC intermediates with -c copy (no 2nd lossy encode).
    Hard cut between files, no gap/crossfade.
    Returns (combined_audio_path, total_duration, [(file, input_lufs)]).
    """
    os.makedirs(workdir, exist_ok=True)
    total = 0.0
    intermediates: List[str] = []
    lufs_list: List[Tuple[str, Optional[float]]] = []
    for i, src in enumerate(ordered_files, 1):
        if progress:
            progress(i, len(ordered_files), os.path.basename(src))
        dur = probe_duration(src)
        total += dur
        dst = os.path.join(workdir, f"norm_{i:03d}.m4a")
        ok, in_lufs, _ = loudnorm_encode_file(src, dst, log=log, ffmpeg=ffmpeg)
        lufs_list.append((src, in_lufs))
        if not ok:
            log(f"  GAGAL normalize: {os.path.basename(src)}")
            return None, total, lufs_list
        intermediates.append(dst)
        log(f"  [{i}/{len(ordered_files)}] {os.path.basename(src)} "
            f"{dur:.1f}s LUFS in={in_lufs} -> -14")
    combined = os.path.join(workdir, "audio_combined.m4a")
    if len(intermediates) == 1:
        shutil.copyfile(intermediates[0], combined)
        return combined, total, lufs_list
    if concat_aac_copy(intermediates, combined, log=log, ffmpeg=ffmpeg):
        return combined, total, lufs_list
    return None, total, lufs_list


# --------------------------------------------------------------------------
# Video builds
# --------------------------------------------------------------------------

def build_video_loop(video_path: str, audio_path: str, audio_duration: float,
                     output_path: str, log: LogFn = _noop_log,
                     ffmpeg: Optional[str] = None) -> bool:
    """1-file video: seamless loop, -c:v copy, -t audio_duration, faststart."""
    ff = find_ffmpeg(ffmpeg)
    cmd = [ff, "-hide_banner", "-y",
           "-stream_loop", "-1", "-i", video_path,
           "-i", audio_path,
           "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy", "-c:a", "copy",
           "-t", f"{audio_duration:.3f}",
           "-movflags", "+faststart", output_path]
    r = _run(cmd, log)
    return r.returncode == 0 and os.path.isfile(output_path)


def build_video_folder(picked: Sequence[Tuple[str, float]],
                       audio_path: str, audio_duration: float,
                       output_path: str, workdir: str,
                       log: LogFn = _noop_log,
                       ffmpeg: Optional[str] = None) -> bool:
    """
    Folder video: picked = [(path, duration_used)] already trimmed so
    sum == audio_duration. If all clips share width/height/fps/codec,
    concat demuxer + -c:v copy. Else re-encode via concat filter to
    1920x1080 30fps libx264 crf 18 preset veryfast yuv420p.
    -t audio_duration trims any overshoot on the last clip.
    """
    ff = find_ffmpeg(ffmpeg)
    os.makedirs(workdir, exist_ok=True)
    paths = [p for p, _ in picked]
    infos = [probe_specs(p) for p in paths]
    same = len({(i.width, i.height, round(i.fps, 2), i.codec) for i in infos}) <= 1
    log(f"Video folder: {len(paths)} klip, spek sama={same}")
    if same:
        list_path = os.path.join(workdir, "video_concat_list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for p in paths:
                esc = str(p).replace("'", "'\\''")
                f.write(f"file '{esc}'\n")
        cmd = [ff, "-hide_banner", "-y",
               "-f", "concat", "-safe", "0", "-i", list_path,
               "-i", audio_path,
               "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "copy", "-c:a", "copy",
               "-t", f"{audio_duration:.3f}",
               "-movflags", "+faststart", output_path]
        r = _run(cmd, log)
        return r.returncode == 0 and os.path.isfile(output_path)

    # Different specs -> re-encode via concat filter to common spec.
    inputs: List[str] = []
    for p in paths:
        inputs += ["-i", p]
    filter_parts = []
    for i in range(len(paths)):
        filter_parts.append(
            f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{i}]")
    filter_parts.append(
        "".join(f"[v{i}]" for i in range(len(paths))) +
        f"concat=n={len(paths)}:v=1:a=0[vout]")
    filt = ";".join(filter_parts)
    cmd = ([ff, "-hide_banner", "-y"] + inputs +
           ["-i", audio_path, "-filter_complex", filt,
            "-map", "[vout]", "-map", f"{len(paths)}:a:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-t", f"{audio_duration:.3f}",
            "-movflags", "+faststart", output_path])
    r = _run(cmd, log)
    return r.returncode == 0 and os.path.isfile(output_path)


# --------------------------------------------------------------------------
# Verify + move used videos
# --------------------------------------------------------------------------

def verify_output(output_path: str, expected_duration: float,
                  tolerance: float = 1.0,
                  ffprobe: Optional[str] = None) -> Tuple[bool, str]:
    """Check output exists, size > 0, ffprobe duration within tolerance."""
    if not os.path.isfile(output_path):
        return False, "file output tidak ada"
    if os.path.getsize(output_path) == 0:
        return False, "file output 0 byte"
    dur = probe_duration(output_path, ffprobe=ffprobe)
    if dur <= 0:
        size_mb = os.path.getsize(output_path) / 1_000_000
        return False, (f"ffprobe tidak bisa baca durasi output "
                       f"(file ada, {size_mb:.1f} MB — coba putar manual dulu)")
    if abs(dur - expected_duration) > tolerance:
        return False, (f"durasi output {dur:.1f}s != "
                       f"ekspektasi {expected_duration:.1f}s (toleransi {tolerance}s)")
    return True, f"OK durasi {dur:.1f}s"


def move_used_videos(used_paths: Sequence[str], source_folder: str,
                     output_name: str, log: LogFn = _noop_log) -> Tuple[str, List[str]]:
    """
    Move every used video to the single sibling folder "<src>_used"
    (auto-created). Never deletes. Returns (used_folder, moved_paths).
    Name collisions get a (1), (2)... suffix.
    Writes used_log.txt in the _used folder (date, output, files).
    """
    used_folder = used_folder_path(source_folder)
    os.makedirs(used_folder, exist_ok=True)
    moved: List[str] = []
    for src in used_paths:
        if not os.path.isfile(src):
            log(f"  skip pindah (sudah tidak ada): {src}")
            continue
        dest = os.path.join(used_folder, os.path.basename(src))
        if os.path.abspath(dest) == os.path.abspath(src):
            continue
        base, ext = os.path.splitext(dest)
        n = 1
        while os.path.exists(dest):
            dest = f"{base} ({n}){ext}"
            n += 1
        try:
            shutil.move(src, dest)
            moved.append(dest)
            log(f"  dipindah ke used: {os.path.basename(src)}")
        except Exception as e:
            log(f"  GAGAL pindah {src}: {e}")
    log_path = os.path.join(used_folder, "used_log.txt")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] "
                    f"output={output_name}\n")
            for m in moved:
                f.write(f"  {m}\n")
    except Exception as e:
        log(f"  GAGAL tulis used_log.txt: {e}")
    return used_folder, moved
