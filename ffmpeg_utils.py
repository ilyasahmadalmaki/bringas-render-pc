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

import concurrent.futures
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
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
# Cancellation — Batalkan wajib benar-benar berhenti (bukan cuma popup)
# --------------------------------------------------------------------------

class RenderCancelled(Exception):
    """Raised when the user cancels a render (Batalkan)."""


class RenderControl:
    """Shared handle between the GUI thread and the render worker.

    - The worker registers every ffmpeg process it spawns.
    - The GUI calls request_cancel(): the flag is set AND every
      registered ffmpeg process is terminated (terminate, then kill
      if it refuses to die within 3s). The worker's _run loop polls
      the flag and raises RenderCancelled, so the render stops at
      the next check — mid-measure, mid-encode, or mid-mux.
    - terminate_running() kills the processes WITHOUT setting the
      flag: used internally when a step fails, so a failure is
      still reported as a failure, not as a cancel.
    """

    def __init__(self):
        self._cancelled = threading.Event()
        self._procs: set = set()
        self._lock = threading.Lock()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise RenderCancelled("Render dibatalkan.")

    def request_cancel(self) -> None:
        self._cancelled.set()
        self.terminate_running()

    def register(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.add(proc)

    def unregister(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)

    def terminate_running(self) -> None:
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            _terminate_proc(proc)


def _terminate_proc(proc: subprocess.Popen) -> None:
    """Terminate a process, escalate to kill if it ignores SIGTERM
    (Windows: terminate() already kills). Never raises."""
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    except Exception:
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


def _run(cmd: List[str], log: LogFn = _noop_log,
         control: Optional[RenderControl] = None) -> subprocess.CompletedProcess:
    """Run a command, capturing output (same result shape as before).

    Uses Popen instead of subprocess.run so a cancel can actually
    kill the process: the loop polls the control flag every 0.2s
    while the process runs, and raises RenderCancelled (after
    terminating the process) as soon as cancel is requested.
    Without a control this behaves exactly like the old _run.
    """
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace",
                            **_no_window_kwargs())
    if control is not None:
        control.register(proc)
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if control is not None and control.is_cancelled():
                    control.terminate_running()
                    try:
                        out, err = proc.communicate(timeout=5)
                    except Exception:
                        out, err = "", ""
                    raise RenderCancelled("Render dibatalkan.")
                continue
        # Race: cancel may have landed (and killed the process) just
        # as communicate() returned normally — the render is still
        # cancelled, so report it as cancelled, not as success.
        if control is not None and control.is_cancelled():
            raise RenderCancelled("Render dibatalkan.")
    finally:
        if control is not None:
            control.unregister(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


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
# GPU detection (NVENC / Quick Sync / AMF) — faster re-encode when present
# --------------------------------------------------------------------------

# Priority order: NVIDIA NVENC first (most common on Windows desktops),
# then Intel Quick Sync, then AMD AMF.
_GPU_ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf")

# Cache per ffmpeg binary path: detection spawns ffmpeg once per binary.
_GPU_CACHE: dict = {}


def parse_encoders_list(text: str) -> List[str]:
    """Hardware H.264 encoders in `ffmpeg -encoders` output, in
    priority order (NVENC > QSV > AMF). Pure function, easy to test."""
    found = set()
    for line in (text or "").splitlines():
        for tok in line.split():
            if tok in _GPU_ENCODERS:
                found.add(tok)
    return [name for name in _GPU_ENCODERS if name in found]


def parse_encoders_output(text: str) -> Optional[str]:
    """Best hardware H.264 encoder found in `ffmpeg -encoders` output,
    or None."""
    lst = parse_encoders_list(text)
    return lst[0] if lst else None


def _encoder_probe_ok(ffmpeg: str, encoder: str) -> bool:
    """True if `encoder` actually works on this machine.

    `ffmpeg -encoders` only lists compiled-in encoders — NVENC is
    listed even without an NVIDIA GPU, so a tiny 1-frame encode is
    the real test (fails fast without the driver/hardware).
    """
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-y",
             "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
             "-frames:v", "1", "-c:v", encoder, "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
            **_no_window_kwargs())
        return r.returncode == 0
    except Exception:
        return False


def detect_gpu_encoder(ffmpeg: Optional[str] = None) -> Optional[str]:
    """Detect the best hardware H.264 encoder this ffmpeg supports
    AND that actually works on this machine.

    Each candidate from `ffmpeg -encoders` is verified with a tiny
    1-frame encode before it is trusted. Runs once per ffmpeg path
    (cached). Never raises — any failure means CPU fallback.
    """
    ff = find_ffmpeg(ffmpeg)
    if ff in _GPU_CACHE:
        return _GPU_CACHE[ff]
    result: Optional[str] = None
    try:
        r = subprocess.run([ff, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=30,
                           **_no_window_kwargs())
        for name in parse_encoders_list(r.stdout or ""):
            if _encoder_probe_ok(ff, name):
                result = name
                break
    except Exception:
        result = None
    _GPU_CACHE[ff] = result
    return result


def video_encoder_args(encoder: Optional[str] = None) -> List[str]:
    """ffmpeg video-encode flags for the chosen encoder.

    None (or unknown) -> CPU libx264 crf 18 (current quality bar).
    GPU encoders use a comparable constant-quality setting:
    NVENC p4 + CQ 19, Quick Sync medium + ICQ 19, AMF balanced + CQP 19.
    """
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "19",
                "-pix_fmt", "yuv420p"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium",
                "-global_quality", "19", "-pix_fmt", "yuv420p"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp",
                "-qp_i", "19", "-qp_p", "19", "-qp_b", "19",
                "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p"]


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


def unique_output_path(path: str) -> str:
    """Never overwrite: if `path` exists, append ' (1)', ' (2)', ...
    before the extension (Windows-Explorer style). A name that already
    ends with ' (N)' counts up from N."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    m = re.match(r"^(.*) \((\d+)\)$", base)
    n = int(m.group(2)) if m else 0
    if m:
        base = m.group(1)
    while True:
        n += 1
        cand = f"{base} ({n}){ext}"
        if not os.path.exists(cand):
            return cand


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


def default_cache_dir() -> str:
    """Persistent cache for normalized audio (survives app restarts).
    Lives in the OS temp dir; files are keyed by content identity,
    so a stale entry can never be mistaken for a fresh one."""
    d = os.path.join(tempfile.gettempdir(), "bringas_render_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _norm_cache_key(path: str, fast: bool) -> str:
    """Identity of a normalize job: file content (path+size+mtime)
    + mode (fast/best) + loudness targets. Changing any of these
    yields a different key, so the cache can never serve a wrong
    result for a changed file or a changed setting."""
    st = os.stat(path)
    raw = (f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|"
           f"fast={fast}|I={LOUDNESS_TARGET_I}|TP={LOUDNESS_TARGET_TP}|"
           f"LRA={LOUDNESS_TARGET_LRA}|br={AUDIO_BITRATE}|rate={AUDIO_RATE}")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def loudnorm_measure(path: str, log: LogFn = _noop_log,
                     ffmpeg: Optional[str] = None,
                     control: Optional[RenderControl] = None,
                     ) -> Optional[LoudnormMeasured]:
    """Pass 1: measure loudness with loudnorm, return measured values."""
    ff = find_ffmpeg(ffmpeg)
    filt = (f"loudnorm=I={LOUDNESS_TARGET_I}:TP={LOUDNESS_TARGET_TP}:"
            f"LRA={LOUDNESS_TARGET_LRA}:print_format=json")
    cmd = [ff, "-hide_banner", "-i", path, "-vn", "-af", filt,
           "-f", "null", "-"]
    r = _run(cmd, log, control=control)
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
    fast: bool = False,
    control: Optional[RenderControl] = None,
    cache_dir: Optional[str] = None,
) -> Tuple[bool, Optional[float], Optional[float]]:
    """
    Encode src -> dst AAC 320k 48kHz stereo, normalized to
    -14 LUFS / -1 dBTP, linear=true.

    Best mode (fast=False, default — unchanged from v1): dual-pass.
    Pass 1 measures loudness, pass 2 encodes with the measured
    values, soxr resample. Slowest, most accurate.

    Fast mode (fast=True): single-pass loudnorm, no measure pass
    and the default resampler (no soxr attempt). Roughly 2x faster
    because the file is only decoded once; the price is loudness
    accuracy (can miss the -14 LUFS target by ~1-3 LUFS and the
    true-peak limit is less strict). YouTube re-normalizes on
    playback anyway, so the audible difference is small.

    cache_dir: if given, a successful encode is stored under a
    content+settings key and reused on later renders, so a file
    that was already normalized is never re-encoded.

    Returns (ok, input_lufs, output_lufs). Output LUFS is
    re-measured only if the caller asks via a second measure call;
    here we return the measured input LUFS and None for output
    (cheap path). Raises RenderCancelled when cancelled.
    """
    ff = find_ffmpeg(ffmpeg)
    if control is not None:
        control.check()
    cache_path = None
    if cache_dir:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(
                cache_dir, _norm_cache_key(src, fast) + ".m4a")
            if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
                shutil.copyfile(cache_path, dst)
                log(f"  cache: pakai hasil normalize sebelumnya: "
                    f"{os.path.basename(src)}")
                return True, None, None
        except Exception as e:
            log(f"  cache: tidak bisa dipakai ({e})")
            cache_path = None
    if fast:
        # Single pass: no measure step, no measured_* args.
        measured = None
    elif measured is None:
        measured = loudnorm_measure(src, log=log, ffmpeg=ffmpeg,
                                    control=control)
    base = (f"loudnorm=I={LOUDNESS_TARGET_I}:TP={LOUDNESS_TARGET_TP}:"
            f"LRA={LOUDNESS_TARGET_LRA}:linear=true")
    if measured is not None:
        base += ":" + measured.to_filter_args()
    # Best: soxr first, default resampler as fallback. Fast: default
    # resampler straight away (soxr is the slowest link in the chain).
    resamplers = (None,) if fast else ("soxr", None)
    for resampler in resamplers:
        if control is not None:
            control.check()
        if resampler:
            af = f"aresample={AUDIO_RATE}:resampler={resampler},{base}"
        else:
            af = f"aresample={AUDIO_RATE},{base}"
        cmd = [ff, "-hide_banner", "-y", "-i", src, "-vn",
               "-af", af, "-c:a", "aac", "-b:a", AUDIO_BITRATE,
               "-ac", "2", dst]
        r = _run(cmd, log, control=control)
        if r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
            if cache_path:
                try:
                    shutil.copyfile(dst, cache_path)
                except Exception:
                    pass
            return True, (measured.input_i if measured else None), None
        log(f"  encode failed (resampler={resampler}), retrying" if resampler
            else "  encode failed with default resampler")
    return False, (measured.input_i if measured else None), None


def encode_audio_aac(src: str, dst: str, log: LogFn = _noop_log,
                     ffmpeg: Optional[str] = None,
                     control: Optional[RenderControl] = None) -> bool:
    """Plain WAV/FLAC -> AAC 320k 48kHz stereo (no loudnorm)."""
    ff = find_ffmpeg(ffmpeg)
    for resampler in ("soxr", None):
        af = (f"aresample={AUDIO_RATE}:resampler={resampler}" if resampler
              else f"aresample={AUDIO_RATE}")
        cmd = [ff, "-hide_banner", "-y", "-i", src, "-vn", "-af", af,
               "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2", dst]
        if _run(cmd, log, control=control).returncode == 0 and os.path.isfile(dst):
            return True
    return False


# --------------------------------------------------------------------------
# Audio builds
# --------------------------------------------------------------------------

def build_audio_single(src: str, workdir: str, log: LogFn = _noop_log,
                       ffmpeg: Optional[str] = None,
                       normalize: bool = True,
                       fast: bool = False,
                       control: Optional[RenderControl] = None,
                       cache_dir: Optional[str] = None,
                       ) -> Tuple[Optional[str], float]:
    """
    Single-file audio -> prepared audio file (AAC .m4a, or the original
    MP3 itself when copying). Returns (prepared_path, duration).
    WAV/FLAC: loudnorm (dual-pass by default, single-pass when
    fast=True) -> AAC 320k 48kHz stereo.
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
        ok, in_lufs, _ = loudnorm_encode_file(
            src, dst, log=log, ffmpeg=ffmpeg, fast=fast,
            control=control, cache_dir=cache_dir)
        if ok:
            mode = "cepat (1 pass)" if fast else "dual-pass"
            log(f"Audio single: WAV/FLAC loudnorm {mode} "
                f"{in_lufs} -> -14 LUFS, AAC 320k")
            return dst, dur
        log("Audio single: loudnorm gagal, fallback encode AAC biasa")
    if encode_audio_aac(src, dst, log=log, ffmpeg=ffmpeg, control=control):
        return dst, dur
    return None, dur


def concat_aac_copy(inputs: Sequence[str], dst: str,
                    log: LogFn = _noop_log,
                    ffmpeg: Optional[str] = None,
                    control: Optional[RenderControl] = None) -> bool:
    """Concat AAC (.m4a) intermediates with the concat demuxer, -c copy."""
    ff = find_ffmpeg(ffmpeg)
    list_path = os.path.join(os.path.dirname(dst) or ".", "concat_list.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in inputs:
            esc = str(p).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")
    cmd = [ff, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
           "-i", list_path, "-c", "copy", dst]
    r = _run(cmd, log, control=control)
    return r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0


def build_audio_folder(ordered_files: Sequence[str], workdir: str,
                       log: LogFn = _noop_log,
                       ffmpeg: Optional[str] = None,
                       progress: Optional[Callable[[int, int, str], None]] = None,
                       fast: bool = False,
                       control: Optional[RenderControl] = None,
                       workers: Optional[int] = None,
                       cache_dir: Optional[str] = None,
                       ) -> Tuple[Optional[str], float, List[Tuple[str, Optional[float]]]]:
    """
    Folder audio: normalize EACH file individually (loudnorm
    -14 LUFS / -1 dBTP, AAC 320k, one-by-one, best quality by
    default), then concat the AAC intermediates with -c copy
    (no 2nd lossy encode). Hard cut between files, no gap/crossfade.

    Speed (v1.1): files are encoded in parallel (default up to 4
    workers, following the CPU count), and with fast=True each file
    uses single-pass loudnorm instead of dual-pass. A cache_dir
    reuses already-normalized results across renders. The output
    order is always the input order, no matter how the workers
    finish. Raises RenderCancelled when cancelled.
    Returns (combined_audio_path, total_duration, [(file, input_lufs)]).
    """
    os.makedirs(workdir, exist_ok=True)
    total = 0.0
    durations: List[float] = []
    for src in ordered_files:
        durations.append(probe_duration(src))
    total = sum(durations)
    n = len(ordered_files)
    if n == 0:
        return None, 0.0, []

    def _one(idx: int) -> Tuple[str, Optional[float], str]:
        src = ordered_files[idx]
        dst = os.path.join(workdir, f"norm_{idx + 1:03d}.m4a")
        ok, in_lufs, _ = loudnorm_encode_file(
            src, dst, log=log, ffmpeg=ffmpeg, fast=fast,
            control=control, cache_dir=cache_dir)
        if not ok:
            raise RuntimeError(f"GAGAL normalize: {os.path.basename(src)}")
        return dst, in_lufs, src

    if workers is None:
        workers = min(4, os.cpu_count() or 2)
    workers = max(1, min(workers, n))

    intermediates: List[str] = [""] * n
    lufs_list: List[Tuple[str, Optional[float]]] = [("", None)] * n
    done = 0
    done_lock = threading.Lock()
    failed: List[BaseException] = []

    def _report(name: str) -> None:
        nonlocal done
        with done_lock:
            done += 1
            if progress:
                progress(done, n, name)

    if workers == 1:
        for idx in range(n):
            if control is not None:
                control.check()
            dst, in_lufs, src = _one(idx)
            intermediates[idx] = dst
            lufs_list[idx] = (src, in_lufs)
            _report(os.path.basename(src))
            log(f"  [{idx + 1}/{n}] {os.path.basename(src)} "
                f"{durations[idx]:.1f}s LUFS in={in_lufs} -> -14")
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_one, idx): idx for idx in range(n)}
            try:
                for fut in concurrent.futures.as_completed(futs):
                    idx = futs[fut]
                    try:
                        dst, in_lufs, src = fut.result()
                    except BaseException as e:  # noqa: BLE001 — re-raised below
                        failed.append(e)
                        # Stop the still-running ffmpeg processes, but
                        # do NOT set the cancel flag: a failure is a
                        # failure, not a user cancel.
                        if control is not None:
                            control.terminate_running()
                        break
                    intermediates[idx] = dst
                    lufs_list[idx] = (src, in_lufs)
                    _report(os.path.basename(src))
                    log(f"  [{idx + 1}/{n}] {os.path.basename(src)} "
                        f"{durations[idx]:.1f}s LUFS in={in_lufs} -> -14")
            finally:
                for fut in futs:
                    fut.cancel()
        if failed:
            first = failed[0]
            if isinstance(first, RenderCancelled):
                raise first
            log(f"  {first}")
            return None, total, lufs_list
    if control is not None:
        control.check()
    combined = os.path.join(workdir, "audio_combined.m4a")
    if len(intermediates) == 1:
        shutil.copyfile(intermediates[0], combined)
        return combined, total, lufs_list
    if concat_aac_copy(intermediates, combined, log=log, ffmpeg=ffmpeg,
                       control=control):
        return combined, total, lufs_list
    return None, total, lufs_list


# --------------------------------------------------------------------------
# Video builds
# --------------------------------------------------------------------------

def build_video_loop(video_path: str, audio_path: str, audio_duration: float,
                     output_path: str, log: LogFn = _noop_log,
                     ffmpeg: Optional[str] = None,
                     control: Optional[RenderControl] = None) -> bool:
    """1-file video: seamless loop, -c:v copy, -t audio_duration, faststart."""
    ff = find_ffmpeg(ffmpeg)
    cmd = [ff, "-hide_banner", "-y",
           "-stream_loop", "-1", "-i", video_path,
           "-i", audio_path,
           "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy", "-c:a", "copy",
           "-t", f"{audio_duration:.3f}",
           "-movflags", "+faststart", output_path]
    r = _run(cmd, log, control=control)
    return r.returncode == 0 and os.path.isfile(output_path)


def build_video_folder(picked: Sequence[Tuple[str, float]],
                       audio_path: str, audio_duration: float,
                       output_path: str, workdir: str,
                       log: LogFn = _noop_log,
                       ffmpeg: Optional[str] = None,
                       encoder: Optional[str] = None,
                       control: Optional[RenderControl] = None) -> bool:
    """
    Folder video: picked = [(path, duration_used)] already trimmed so
    sum == audio_duration. If all clips share width/height/fps/codec,
    concat demuxer + -c:v copy. Else re-encode via concat filter to
    1920x1080 30fps — libx264 crf 18 by default, or a hardware encoder
    (NVENC / Quick Sync / AMF) when `encoder` is given.
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
        r = _run(cmd, log, control=control)
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
    v_args = video_encoder_args(encoder)
    log(f"Video folder: re-encode pakai {encoder or 'libx264 (CPU)'}")
    cmd = ([ff, "-hide_banner", "-y"] + inputs +
           ["-i", audio_path, "-filter_complex", filt,
            "-map", "[vout]", "-map", f"{len(paths)}:a:0"] +
           v_args + ["-c:a", "copy",
            "-t", f"{audio_duration:.3f}",
            "-movflags", "+faststart", output_path])
    r = _run(cmd, log, control=control)
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
