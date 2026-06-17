"""Split an uploaded video into frames with ffmpeg.

These frames are used as 'style anchors' — a few representative frames you
select get attached to every generation so the output matches the look of your
sample video.
"""
import glob
import os
import subprocess

import config
import store


def _probe_duration(video_path: str) -> float:
    """Return video duration in seconds using ffprobe. Returns 0 on failure."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=15,
        )
        return float(proc.stdout.strip())
    except Exception:
        return 0.0


def extract_frames(video_path, fps=None, max_frames=40):
    """Sample frames from `video_path` evenly distributed across its full duration.

    Uses seek-based extraction (one ffmpeg call per frame) so we always pull
    frames from the beginning, middle AND end of the clip — not just the first
    few seconds.  Falls back to fps-filter mode for very short clips.

    Returns a list of web paths (/data/frames/...).
    """
    os.makedirs(store.FRAMES_DIR, exist_ok=True)
    tag = store.new_id("vid")

    duration = _probe_duration(video_path)

    if duration >= 2.0 and max_frames > 1:
        # Seek-based: extract one frame at each evenly-spaced timestamp.
        # Skip the first and last 2% to avoid black fade-in/out frames.
        margin = min(1.0, duration * 0.02)
        usable = duration - 2 * margin
        count = min(max_frames, int(max_frames))
        timestamps = [margin + usable * i / max(1, count - 1)
                      for i in range(count)]
        urls = []
        for i, ts in enumerate(timestamps):
            out_path = os.path.join(store.FRAMES_DIR, f"{tag}_{i+1:04d}.png")
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{ts:.3f}",
                "-i", video_path,
                "-frames:v", "1",
                "-q:v", "2",
                out_path,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode == 0 and os.path.exists(out_path):
                rel = os.path.relpath(out_path, store.DATA_DIR).replace(os.sep, "/")
                urls.append(f"/data/{rel}")
        if urls:
            return urls
        # fall through to fps mode on failure

    # fps-filter fallback (short clips or seek mode failed)
    fps = fps or config.FRAME_FPS
    pattern = os.path.join(store.FRAMES_DIR, f"{tag}_%04d.png")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"fps={fps}",
        "-frames:v", str(int(max_frames)),
        pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-800:]}")

    urls = []
    for p in sorted(glob.glob(os.path.join(store.FRAMES_DIR, f"{tag}_*.png"))):
        rel = os.path.relpath(p, store.DATA_DIR).replace(os.sep, "/")
        urls.append(f"/data/{rel}")
    return urls
