import shutil
import subprocess
import uuid
from pathlib import Path


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} is required. Install FFmpeg and make sure it is on PATH.")
    return path


def probe_duration(video_path: str) -> float | None:
    try:
        result = subprocess.run(
            [_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        return None


def extract_frames(video_path: str, output_dir: str, interval: int = 8, max_frames: int = 18):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    duration = probe_duration(video_path)
    if duration is None:
        raise RuntimeError("Could not read the video duration. Check the video format.")
    actual_interval = max(interval, int(duration / max_frames) + 1)
    subprocess.run(
        [_tool("ffmpeg"), "-y", "-i", video_path, "-vf", f"fps=1/{actual_interval},scale=1120:-2",
         "-q:v", "4", "-frames:v", str(max_frames), str(out / "frame_%04d.jpg")],
        capture_output=True, text=True, timeout=180, check=True)
    frames = sorted(out.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError("No frames could be extracted from this video.")
    return frames, duration, actual_interval


def frame_at(video_path: str, seconds: float, output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    seconds = max(0.0, float(seconds))
    target = out / f"at_{int(round(seconds * 10)):06d}.jpg"
    if target.exists() and target.stat().st_size > 0:
        return target
    tmp = target.with_name(f"{target.stem}.{uuid.uuid4().hex[:8]}.part.jpg")
    subprocess.run(
        [_tool("ffmpeg"), "-y", "-ss", f"{seconds:.2f}", "-i", video_path, "-frames:v", "1",
         "-vf", "scale=1120:-2", "-q:v", "3", str(tmp)],
        capture_output=True, text=True, timeout=60, check=True)
    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("No frame could be extracted at that moment.")
    tmp.replace(target)
    return target


def extract_audio_wav(video_path: str, output_path: str):
    subprocess.run(
        [_tool("ffmpeg"), "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "24000",
         "-c:a", "pcm_s16le", "-map_metadata", "-1", output_path],
        capture_output=True, text=True, timeout=180, check=True)
