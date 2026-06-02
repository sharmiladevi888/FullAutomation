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


def extract_frames(video_path, fps=None, max_frames=40):
    """Sample frames from `video_path` at `fps` frames/second (capped at
    `max_frames`). Returns a list of web paths (/data/frames/...)."""
    fps = fps or config.FRAME_FPS
    os.makedirs(store.FRAMES_DIR, exist_ok=True)

    tag = store.new_id("vid")
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
