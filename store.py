"""Tiny file-based persistence layer. Single project, stored under DATA_DIR.

Layout:
    data/
      project.json         <- the whole project state
      images/              <- generated sequence frames
      characters/          <- generated character sheets
      frames/              <- frames extracted from uploaded videos
      uploads/             <- raw uploaded videos
      audio/               <- uploaded audio files
      videos/              <- final assembled videos
"""
import json
import os
import time
import uuid

import config

DATA_DIR = config.DATA_DIR
IMAGES_DIR = os.path.join(DATA_DIR, "images")
CHARS_DIR = os.path.join(DATA_DIR, "characters")
FRAMES_DIR = os.path.join(DATA_DIR, "frames")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")
VIDEOS_DIR = os.path.join(DATA_DIR, "videos")
STATE_PATH = os.path.join(DATA_DIR, "project.json")

_DEFAULT_STATE = {
    "master_prompt": "",
    "style_frames": [],    # [{id, url}]
    "characters": [],      # [{id, name, description, sheet_url, prompt, created, source}]
    "sequence": [],        # [{id, index, prompt, full_prompt, image_url, ...}]
    "script": None,        # {title, logline, scenes:[{n, heading, action, prompt}]}
    "suggested_prompts": [], # raw prompts produced by Claude (vision-from-video)
    "audio": None,         # {id, url, name, duration}  (current edit audio track)
    "voiceover": None,     # {id, url, voice_id, voice_name, mode, scenes, created}
    "edits": [],           # [{id, url, audio_id, transition, plan, created}]
}


def init():
    for d in (DATA_DIR, IMAGES_DIR, CHARS_DIR, FRAMES_DIR, UPLOADS_DIR,
              AUDIO_DIR, VIDEOS_DIR):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(STATE_PATH):
        save_state(json.loads(json.dumps(_DEFAULT_STATE)))


def load_state():
    if not os.path.exists(STATE_PATH):
        init()
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        st = json.load(f)
    for k, v in _DEFAULT_STATE.items():
        st.setdefault(k, json.loads(json.dumps(v)))
    return st


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)
    return state


def new_id(prefix="id"):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now():
    return int(time.time())


_FOLDER = {
    "images": IMAGES_DIR,
    "characters": CHARS_DIR,
    "frames": FRAMES_DIR,
    "audio": AUDIO_DIR,
    "videos": VIDEOS_DIR,
}


def write_image(kind, data, ext="png"):
    folder = _FOLDER[kind]
    fname = f"{new_id(kind.rstrip('s'))}.{ext}"
    path = os.path.join(folder, fname)
    with open(path, "wb") as f:
        f.write(data)
    rel = os.path.relpath(path, DATA_DIR).replace(os.sep, "/")
    return f"/data/{rel}"


def write_binary(kind, data, ext, name_hint=None):
    folder = _FOLDER[kind]
    base = new_id(kind.rstrip("s"))
    if name_hint:
        # sanitize
        safe = "".join(c for c in name_hint if c.isalnum() or c in "._-")[:60]
        fname = f"{base}_{safe}" if safe else f"{base}.{ext}"
        if not fname.endswith("." + ext):
            fname += "." + ext
    else:
        fname = f"{base}.{ext}"
    path = os.path.join(folder, fname)
    with open(path, "wb") as f:
        f.write(data)
    rel = os.path.relpath(path, DATA_DIR).replace(os.sep, "/")
    return f"/data/{rel}", path


def url_to_path(url):
    if not url or not url.startswith("/data/"):
        raise ValueError(f"not a managed media url: {url!r}")
    return os.path.join(DATA_DIR, url[len("/data/"):])


def read_image(url):
    with open(url_to_path(url), "rb") as f:
        return f.read()
