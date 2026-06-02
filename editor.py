"""ffmpeg-based video assembly + scene detection.

We assemble final cuts from generated frames + uploaded audio according to an
EDL produced by Claude (see claude_client.ClaudeClient.plan_edit).
"""
import json
import os
import subprocess
from typing import List, Dict, Optional

import store


def probe_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-400:]}")
    data = json.loads(proc.stdout or "{}")
    return float((data.get("format") or {}).get("duration") or 0)


def detect_scenes(video_path: str, threshold: float = 0.4) -> List[float]:
    """Return a list of scene-change timestamps (seconds) using ffmpeg's
    scene detector. Threshold 0..1 (lower = more cuts)."""
    cmd = [
        "ffmpeg", "-i", video_path, "-filter:v",
        f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # showinfo writes to stderr.
    times = []
    for line in proc.stderr.splitlines():
        # parse pts_time:XX.XXX
        idx = line.find("pts_time:")
        if idx >= 0:
            tail = line[idx + len("pts_time:"):]
            num = ""
            for ch in tail:
                if ch.isdigit() or ch == ".":
                    num += ch
                else:
                    break
            if num:
                try:
                    times.append(float(num))
                except Exception:
                    pass
    return times


def assemble_video(
    shots: List[Dict],         # [{path:str, duration:float, note?:str}]
    audio_path: Optional[str],
    output_path: str,
    transition: str = "cut",   # cut | fade | crossfade
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    fit: str = "fill",         # fill = crop to fill (no bars) | pad = letterbox
) -> str:
    """Stitch ``shots`` into a single MP4 with optional audio overlay.

    ``cut``        -> concat demuxer
    ``fade``       -> per-shot fade-in/out (uniform 0.25s)
    ``crossfade``  -> xfade chain between adjacent shots (0.4s)
    """
    if not shots:
        raise ValueError("no shots provided")

    workdir = os.path.dirname(output_path) or "."
    os.makedirs(workdir, exist_ok=True)
    tmp_dir = os.path.join(workdir, "_assemble_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # 1. Build per-shot video clips, all normalised to the same size+fps.
    clip_paths = []
    for i, sh in enumerate(shots):
        clip = os.path.join(tmp_dir, f"clip_{i:04d}.mp4")
        dur = max(0.1, float(sh.get("duration") or 0))
        if fit == "pad":
            # letterbox: fit whole frame, black bars where aspect differs.
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
            )
        else:
            # fill: scale up to cover, then center-crop to the exact size — no
            # black bars (best for a clean 16:9 / 9:16 slideshow).
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1"
            )
        if transition == "fade":
            fade_d = min(0.25, dur / 4)
            vf += f",fade=t=in:st=0:d={fade_d},fade=t=out:st={max(0,dur-fade_d):.3f}:d={fade_d}"
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", f"{dur:.3f}",
            "-i", sh["path"],
            "-vf", vf,
            "-r", str(fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
            clip,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg clip failed [{i}]: {proc.stderr[-500:]}")
        clip_paths.append((clip, dur))

    # 2. Combine clips.
    silent_video = os.path.join(tmp_dir, "video.mp4")
    if transition == "crossfade" and len(clip_paths) > 1:
        # Chain xfades. We rebuild via a single filter graph using the inputs.
        inputs = []
        for cp, _ in clip_paths:
            inputs += ["-i", cp]
        xfade_d = 0.4
        filtergraph = ""
        last_label = "0:v"
        cum = clip_paths[0][1]
        for i in range(1, len(clip_paths)):
            offset = max(0, cum - xfade_d)
            new_label = f"v{i}"
            filtergraph += (
                f"[{last_label}][{i}:v]xfade=transition=fade:duration={xfade_d}:"
                f"offset={offset:.3f}[{new_label}];"
            )
            last_label = new_label
            cum += clip_paths[i][1] - xfade_d
        filtergraph = filtergraph.rstrip(";")
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filtergraph,
               "-map", f"[{last_label}]",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20",
               silent_video]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg xfade failed: {proc.stderr[-600:]}")
    else:
        # concat demuxer
        list_path = os.path.join(tmp_dir, "list.txt")
        with open(list_path, "w") as f:
            for cp, _ in clip_paths:
                # ffmpeg concat needs forward slashes / escaped quotes
                f.write(f"file '{os.path.abspath(cp)}'\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
               "-c", "copy", silent_video]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {proc.stderr[-600:]}")

    # 3. Mux audio if provided, else leave silent.
    if audio_path and os.path.exists(audio_path):
        cmd = ["ffmpeg", "-y",
               "-i", silent_video, "-i", audio_path,
               "-map", "0:v:0", "-map", "1:a:0",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
               "-shortest", output_path]
    else:
        cmd = ["ffmpeg", "-y", "-i", silent_video, "-c", "copy", output_path]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {proc.stderr[-600:]}")

    # cleanup
    try:
        for cp, _ in clip_paths:
            os.remove(cp)
        if os.path.exists(silent_video):
            os.remove(silent_video)
        if os.path.exists(os.path.join(tmp_dir, "list.txt")):
            os.remove(os.path.join(tmp_dir, "list.txt"))
        os.rmdir(tmp_dir)
    except Exception:
        pass

    return output_path
