"""Continuity Studio — FastAPI backend (extended).

Run:  uvicorn app:app --reload --port 8000   then open http://localhost:8000
"""
import io
import json
import os
import re
import time
import zipfile
from typing import List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import pipeline
import store
import editor
from derouter import ImageClient
from claude_client import ClaudeClient, extract_json

store.init()
app = FastAPI(title="Continuity Studio")
app.mount("/data", StaticFiles(directory=config.DATA_DIR), name="data")

# Runtime-overridable settings (so a key can be pasted in the UI).
_settings = {
    "api_key": config.API_KEY,
    "base_url": config.BASE_URL,
    "model": config.MODEL,
    "multi_image_edit": config.MULTI_IMAGE_EDIT,
    "claude_api_key": config.CLAUDE_API_KEY,
    "claude_base_url": config.CLAUDE_BASE_URL,
    "claude_model": config.CLAUDE_MODEL,
}


def get_image_client() -> ImageClient:
    return ImageClient(
        api_key=_settings["api_key"],
        base_url=_settings["base_url"],
        model=_settings["model"],
    )


def get_claude_client() -> ClaudeClient:
    return ClaudeClient(
        api_key=_settings["claude_api_key"],
        base_url=_settings["claude_base_url"],
        model=_settings["claude_model"],
    )


# --------------------------------------------------------------------------- #
#  Static page
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


# --------------------------------------------------------------------------- #
#  State + settings
# --------------------------------------------------------------------------- #
@app.get("/api/state")
def api_state():
    return {
        "state": store.load_state(),
        "config": {
            "model": _settings["model"],
            "base_url": _settings["base_url"],
            "has_api_key": bool(_settings["api_key"]),
            "multi_image_edit": _settings["multi_image_edit"],
            "claude_model": _settings["claude_model"],
            "claude_base_url": _settings["claude_base_url"],
            "has_claude_key": bool(_settings["claude_api_key"]),
            "claude_models": config.CLAUDE_MODELS,
            "default_size": config.DEFAULT_SIZE,
            "default_quality": config.DEFAULT_QUALITY,
            "sizes": config.SUPPORTED_SIZES,
            "qualities": config.SUPPORTED_QUALITIES,
        },
    }


class SettingsIn(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    multi_image_edit: Optional[bool] = None
    claude_api_key: Optional[str] = None
    claude_base_url: Optional[str] = None
    claude_model: Optional[str] = None


@app.post("/api/settings")
def api_settings(s: SettingsIn):
    if s.api_key is not None:
        _settings["api_key"] = s.api_key.strip()
    if s.base_url:
        _settings["base_url"] = s.base_url.strip().rstrip("/")
    if s.model:
        _settings["model"] = s.model.strip()
    if s.multi_image_edit is not None:
        _settings["multi_image_edit"] = s.multi_image_edit
    if s.claude_api_key is not None:
        _settings["claude_api_key"] = s.claude_api_key.strip()
    if s.claude_base_url:
        _settings["claude_base_url"] = s.claude_base_url.strip().rstrip("/")
    if s.claude_model:
        _settings["claude_model"] = s.claude_model.strip()
    return {
        "ok": True,
        "has_api_key": bool(_settings["api_key"]),
        "has_claude_key": bool(_settings["claude_api_key"]),
    }


@app.get("/api/health")
def api_health():
    """Cheap connectivity + auth check for both upstream APIs.

    Returns one entry per service with ok/error so the UI can show a clear
    light next to each key. Doesn't generate anything, just hits /models on
    each upstream. Safe to spam.
    """
    image_status = get_image_client().ping()
    # Keep ImageClient ping aware of the runtime MULTI_IMAGE_EDIT toggle.
    image_status["multi_image_edit"] = _settings["multi_image_edit"]
    claude_status = get_claude_client().ping()
    return {"image": image_status, "claude": claude_status}


# --------------------------------------------------------------------------- #
#  Master prompt
# --------------------------------------------------------------------------- #
class MasterIn(BaseModel):
    master_prompt: str = ""


@app.post("/api/master")
def api_master(m: MasterIn):
    st = store.load_state()
    st["master_prompt"] = m.master_prompt
    store.save_state(st)
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Video -> frames -> style anchors
# --------------------------------------------------------------------------- #
@app.post("/api/video")
async def api_video(
    file: UploadFile = File(...),
    fps: float = Form(1.0),
    max_frames: int = Form(40),
):
    import video as videomod
    dest = os.path.join(
        store.UPLOADS_DIR, store.new_id("upload") + "_" + (file.filename or "video.mp4")
    )
    with open(dest, "wb") as f:
        f.write(await file.read())
    try:
        urls = videomod.extract_frames(dest, fps=fps, max_frames=max_frames)
    except Exception as e:
        raise HTTPException(500, f"frame extraction failed: {e}")
    return {"frames": urls, "video_path": dest}


class StyleFramesIn(BaseModel):
    urls: List[str] = []


@app.post("/api/style-frames")
def api_style_frames(s: StyleFramesIn):
    st = store.load_state()
    st["style_frames"] = [{"id": store.new_id("frame"), "url": u} for u in s.urls]
    store.save_state(st)
    return {"ok": True, "count": len(st["style_frames"])}


# --------------------------------------------------------------------------- #
#  Scene detection / per-frame analysis
# --------------------------------------------------------------------------- #
@app.post("/api/scene-detect")
async def api_scene_detect(file: UploadFile = File(...), threshold: float = Form(0.4)):
    dest = os.path.join(
        store.UPLOADS_DIR, store.new_id("scene") + "_" + (file.filename or "video.mp4")
    )
    with open(dest, "wb") as f:
        f.write(await file.read())
    try:
        times = editor.detect_scenes(dest, threshold=threshold)
        dur = editor.probe_duration(dest)
    except Exception as e:
        raise HTTPException(500, f"scene detection failed: {e}")
    return {"scene_changes": times, "duration": dur, "video_path": dest}


class AnalyseIn(BaseModel):
    image_url: str
    question: str = ""


@app.post("/api/analyse-scene")
def api_analyse_scene(a: AnalyseIn):
    try:
        img = store.read_image(a.image_url)
    except Exception as e:
        raise HTTPException(400, f"unreadable image: {e}")
    try:
        text = get_claude_client().analyse_scene(
            pipeline.downsize_for_vision(img), a.question
        )
    except Exception as e:
        raise HTTPException(500, f"analysis failed: {e}")
    return {"analysis": text}


# --------------------------------------------------------------------------- #
#  Characters: single + bulk + upload
# --------------------------------------------------------------------------- #
class CharacterIn(BaseModel):
    name: str
    description: str = ""
    size: Optional[str] = None
    quality: Optional[str] = None


@app.post("/api/characters")
def api_create_character(c: CharacterIn):
    if not c.name.strip():
        raise HTTPException(400, "name is required")
    st = store.load_state()
    client = get_image_client()
    prompt = pipeline.build_sheet_prompt(st["master_prompt"], c.name, c.description)
    try:
        img = client.generate(
            prompt,
            size=c.size or config.DEFAULT_SIZE,
            quality=c.quality or config.DEFAULT_QUALITY,
        )
    except Exception as e:
        raise HTTPException(500, f"sheet generation failed: {e}")
    rec = {
        "id": store.new_id("char"),
        "name": c.name.strip(),
        "description": c.description.strip(),
        "sheet_url": store.write_image("characters", img),
        "prompt": prompt,
        "source": "generated",
        "created": store.now(),
    }
    st["characters"].append(rec)
    store.save_state(st)
    return rec


class CharacterBatchIn(BaseModel):
    text: str
    size: Optional[str] = None
    quality: Optional[str] = None


@app.post("/api/characters/batch")
def api_create_characters_batch(b: CharacterBatchIn):
    entries = pipeline.parse_character_batch(b.text)
    if not entries:
        raise HTTPException(400, "no character entries found (separate with blank lines)")
    st = store.load_state()
    client = get_image_client()
    created, errors = [], []
    for e in entries:
        try:
            prompt = pipeline.build_sheet_prompt(st["master_prompt"], e["name"], e["description"])
            img = client.generate(
                prompt,
                size=b.size or config.DEFAULT_SIZE,
                quality=b.quality or config.DEFAULT_QUALITY,
            )
            rec = {
                "id": store.new_id("char"),
                "name": e["name"],
                "description": e["description"],
                "sheet_url": store.write_image("characters", img),
                "prompt": prompt,
                "source": "generated",
                "created": store.now(),
            }
            st["characters"].append(rec)
            store.save_state(st)
            created.append(rec)
        except Exception as ex:
            errors.append({"name": e["name"], "error": str(ex)})
    return {"created": created, "errors": errors}


@app.post("/api/characters/upload")
async def api_upload_character(
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
):
    if not name.strip():
        raise HTTPException(400, "name is required")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    # Determine extension by the upload's filename, default .png.
    ext = (os.path.splitext(file.filename or "")[1] or ".png").lstrip(".").lower()
    if ext not in {"png", "jpg", "jpeg", "webp"}:
        ext = "png"
    url = store.write_image("characters", data, ext=ext)
    st = store.load_state()
    rec = {
        "id": store.new_id("char"),
        "name": name.strip(),
        "description": description.strip(),
        "sheet_url": url,
        "prompt": "(uploaded sheet)",
        "source": "uploaded",
        "created": store.now(),
    }
    st["characters"].append(rec)
    store.save_state(st)
    return rec


@app.delete("/api/characters/{cid}")
def api_delete_character(cid: str):
    st = store.load_state()
    st["characters"] = [c for c in st["characters"] if c["id"] != cid]
    store.save_state(st)
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Single-frame generation (the original continuation engine)
# --------------------------------------------------------------------------- #
class GenerateIn(BaseModel):
    prompt: str
    size: Optional[str] = None
    quality: Optional[str] = None
    continue_prev: bool = True
    style_lock: bool = True
    character_ids: Optional[List[str]] = None


def _render_one(g_prompt, size, quality, continue_prev, style_lock,
                character_ids=None):
    """Shared engine for /api/generate and /api/generate-batch."""
    st = store.load_state()
    client = get_image_client()

    # 1. characters
    if character_ids:
        wanted = set(character_ids)
        matched = [c for c in st["characters"] if c["id"] in wanted]
    else:
        matched = pipeline.match_characters(g_prompt, st["characters"])

    # 2. previous frame
    prev = st["sequence"][-1] if (continue_prev and st["sequence"]) else None

    # 3. style anchors
    style_frames = st["style_frames"] if style_lock else []

    # 4. assemble refs
    refs, ref_meta = [], []
    for c in matched:
        try:
            refs.append(store.read_image(c["sheet_url"]))
            ref_meta.append({"type": "character", "name": c["name"]})
        except Exception:
            pass
    if prev:
        try:
            refs.append(store.read_image(prev["image_url"]))
            ref_meta.append({"type": "previous", "id": prev["id"]})
        except Exception:
            pass
    for sf in style_frames[:3]:
        try:
            refs.append(store.read_image(sf["url"]))
            ref_meta.append({"type": "style"})
        except Exception:
            pass

    full_prompt = pipeline.build_full_prompt(
        st["master_prompt"], g_prompt, matched,
        has_previous=bool(prev), style_locked=bool(style_frames),
    )

    if refs:
        # If the proxy isn't confirmed to support repeated `image[]` fields,
        # composite multiple refs into a single contact-sheet PNG so we hit
        # the documented one-`image`-field path.
        if not _settings["multi_image_edit"] and len(refs) > 1:
            send = [pipeline.contact_sheet(refs)]
            mode_note = f"edit (contact-sheet of {len(refs)} refs)"
        else:
            send = refs
            mode_note = f"edit ({len(refs)} refs)"
        print(f"[render] {mode_note} prompt_len={len(full_prompt)}", flush=True)
        try:
            img = client.edit(full_prompt, send, size=size, quality=quality)
        except Exception as edit_err:
            # Multi-image `image[]` mode isn't supported by every proxy. If we
            # sent more than one ref and it failed, fall back to compositing all
            # refs into ONE contact-sheet PNG (the documented single-`image`
            # path) and retry once before giving up.
            if len(send) > 1:
                print(f"[render] multi-ref edit failed ({edit_err}); "
                      f"retrying as contact-sheet", flush=True)
                img = client.edit(full_prompt, [pipeline.contact_sheet(refs)],
                                  size=size, quality=quality)
            else:
                raise
        mode = "edit"
    else:
        print(f"[render] generate (no refs) prompt_len={len(full_prompt)}",
              flush=True)
        img = client.generate(full_prompt, size=size, quality=quality)
        mode = "generate"

    rec = {
        "id": store.new_id("shot"),
        "index": len(st["sequence"]) + 1,
        "prompt": g_prompt.strip(),
        "full_prompt": full_prompt,
        "image_url": store.write_image("images", img),
        "mode": mode,
        "size": size,
        "quality": quality,
        "characters": [c["name"] for c in matched],
        "refs": ref_meta,
        "continued_from": prev["id"] if prev else None,
        "created": store.now(),
    }
    st["sequence"].append(rec)
    store.save_state(st)
    return rec


@app.post("/api/generate")
def api_generate(g: GenerateIn):
    if not g.prompt.strip():
        raise HTTPException(400, "prompt is required")
    size = g.size or config.DEFAULT_SIZE
    quality = g.quality or config.DEFAULT_QUALITY
    try:
        return _render_one(g.prompt, size, quality, g.continue_prev, g.style_lock,
                           g.character_ids)
    except Exception as e:
        raise HTTPException(500, f"generation failed: {e}")


class BatchGenerateIn(BaseModel):
    text: str                          # newline-separated prompts (one per line)
    mode: str = "line"                 # 'line' or 'blank'
    size: Optional[str] = None
    quality: Optional[str] = None
    continue_prev: bool = True
    style_lock: bool = True


@app.post("/api/generate/batch")
def api_generate_batch(b: BatchGenerateIn):
    prompts = pipeline.split_lines_batch(b.text, mode=b.mode)
    if not prompts:
        raise HTTPException(400, "no prompts found")
    size = b.size or config.DEFAULT_SIZE
    quality = b.quality or config.DEFAULT_QUALITY
    created, errors = [], []
    for p in prompts:
        try:
            # Each prompt independently auto-matches characters by @tags / names
            rec = _render_one(p, size, quality, b.continue_prev, b.style_lock,
                              character_ids=None)
            created.append(rec)
        except Exception as ex:
            errors.append({"prompt": p, "error": str(ex)})
    return {"created": created, "errors": errors}


@app.delete("/api/sequence/{sid}")
def api_delete_shot(sid: str):
    st = store.load_state()
    st["sequence"] = [s for s in st["sequence"] if s["id"] != sid]
    for i, s in enumerate(st["sequence"], 1):
        s["index"] = i
    store.save_state(st)
    return {"ok": True}


@app.post("/api/reset-sequence")
def api_reset_sequence():
    st = store.load_state()
    st["sequence"] = []
    store.save_state(st)
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Claude: script generation
# --------------------------------------------------------------------------- #
class ScriptIn(BaseModel):
    title: str = ""
    description: str = ""
    total_duration: float = 60.0
    pacing_seconds: float = 1.0
    num_characters: int = 0
    style_notes: str = ""
    model: Optional[str] = None
    # Back-compat with the old simple form.
    brief: str = ""
    scene_count: Optional[int] = None


def _claude_client_for(model: Optional[str]) -> ClaudeClient:
    """Claude client honouring a per-request model override."""
    return ClaudeClient(
        api_key=_settings["claude_api_key"],
        base_url=_settings["claude_base_url"],
        model=(model or _settings["claude_model"]),
    )


@app.post("/api/script")
def api_script(s: ScriptIn):
    if not (s.title.strip() or s.description.strip() or s.brief.strip()):
        raise HTTPException(400, "a title or description is required")
    st = store.load_state()
    # If the old scene_count form is used, derive a matching duration.
    total_duration = s.total_duration
    pacing = max(0.1, s.pacing_seconds or 1.0)
    if s.scene_count and not s.total_duration:
        total_duration = s.scene_count * pacing
    try:
        raw = _claude_client_for(s.model).generate_script(
            title=s.title,
            description=s.description,
            total_duration=max(1.0, total_duration or 60.0),
            pacing_seconds=pacing,
            num_characters=max(0, s.num_characters or 0),
            style_notes=s.style_notes,
            master_prompt=st["master_prompt"],
            brief=s.brief,
        )
        data = extract_json(raw)
    except Exception as e:
        raise HTTPException(500, f"script generation failed: {e}")
    st["script"] = data
    store.save_state(st)
    return data


@app.get("/api/script/character-prompts")
def api_script_character_prompts():
    """Packed character sheet prompts from the current script, formatted for the
    bulk character generator (name line, paragraph, blank line between)."""
    st = store.load_state()
    sc = st.get("script") or {}
    chars = sc.get("characters") or []
    blocks = []
    for c in chars:
        name = (c.get("name") or "").strip()
        sheet = (c.get("sheet_prompt") or c.get("description") or "").strip()
        if name:
            blocks.append(f"{name}\n{sheet}".strip())
    return {"text": "\n\n".join(blocks), "count": len(blocks)}


class ScriptToBatchIn(BaseModel):
    pass


@app.get("/api/script/prompts")
def api_script_prompts():
    st = store.load_state()
    if not st.get("script"):
        return {"prompts": []}
    out = []
    for sc in (st["script"].get("scenes") or []):
        p = (sc.get("prompt") or "").strip()
        if p:
            out.append(p)
    return {"prompts": out}


# --------------------------------------------------------------------------- #
#  Claude vision: prompts from uploaded reference video
# --------------------------------------------------------------------------- #
class PromptsFromVideoIn(BaseModel):
    frame_urls: List[str]
    count: int = 8
    style_hint: str = ""


@app.post("/api/prompts-from-video")
def api_prompts_from_video(p: PromptsFromVideoIn):
    if not p.frame_urls:
        raise HTTPException(400, "frame_urls is required (extract frames first)")
    st = store.load_state()
    try:
        frames = []
        for u in p.frame_urls[:10]:
            try:
                frames.append(pipeline.downsize_for_vision(store.read_image(u)))
            except Exception:
                pass
        if not frames:
            raise RuntimeError("no readable frames")
        raw = get_claude_client().prompts_from_video_frames(
            frames, count=max(1, min(20, p.count)),
            style_hint=p.style_hint, master_prompt=st["master_prompt"],
        )
        data = extract_json(raw)
    except Exception as e:
        raise HTTPException(500, f"prompt generation failed: {e}")
    st["suggested_prompts"] = data.get("prompts") or []
    store.save_state(st)
    return data


# --------------------------------------------------------------------------- #
#  Audio upload
# --------------------------------------------------------------------------- #
@app.post("/api/audio")
async def api_audio(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    ext = (os.path.splitext(file.filename or "")[1] or ".mp3").lstrip(".").lower()
    if ext not in {"mp3", "wav", "m4a", "aac", "ogg", "flac"}:
        ext = "mp3"
    url, path = store.write_binary("audio", data, ext=ext, name_hint=file.filename)
    try:
        dur = editor.probe_duration(path)
    except Exception:
        dur = 0
    st = store.load_state()
    rec = {
        "id": store.new_id("audio"),
        "url": url,
        "name": file.filename or f"audio.{ext}",
        "duration": dur,
    }
    st["audio"] = rec
    store.save_state(st)
    return rec


@app.delete("/api/audio")
def api_delete_audio():
    st = store.load_state()
    st["audio"] = None
    store.save_state(st)
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Claude: plan an edit  +  ffmpeg: assemble the video
# --------------------------------------------------------------------------- #
class EditPlanIn(BaseModel):
    user_brief: str = ""
    transition: Optional[str] = None     # override Claude's suggestion


@app.post("/api/edit-plan")
def api_edit_plan(e: EditPlanIn):
    st = store.load_state()
    if not st["sequence"]:
        raise HTTPException(400, "sequence is empty — render some frames first")
    if not st.get("audio"):
        raise HTTPException(400, "upload an audio file first")
    frames = []
    for s in st["sequence"]:
        try:
            frames.append(pipeline.downsize_for_vision(store.read_image(s["image_url"])))
        except Exception:
            pass
    if not frames:
        raise HTTPException(400, "no readable frames in sequence")
    # Claude vision input is capped to 20 images per call; if the user has more,
    # we send the first 20 and tell the planner that 1..N are the only valid
    # indices so we don't get hallucinated indices.
    frames_capped = frames[:20]
    try:
        raw = get_claude_client().plan_edit(
            frames=frames_capped,
            audio_duration=float(st["audio"]["duration"]) or 0,
            user_brief=e.user_brief,
            master_prompt=st["master_prompt"],
        )
        plan = extract_json(raw)
    except Exception as ex:
        raise HTTPException(500, f"edit planning failed: {ex}")
    if e.transition:
        plan["transition"] = e.transition
    return plan


class RenderVideoIn(BaseModel):
    plan: dict
    transition: Optional[str] = None
    width: int = 1536
    height: int = 1024
    fps: int = 30


@app.post("/api/render-video")
def api_render_video(r: RenderVideoIn):
    st = store.load_state()
    if not st["sequence"]:
        raise HTTPException(400, "no sequence")
    seq = st["sequence"]
    shots_in = (r.plan or {}).get("shots") or []
    if not shots_in:
        raise HTTPException(400, "plan.shots is empty")
    audio_path = None
    if st.get("audio"):
        try:
            audio_path = store.url_to_path(st["audio"]["url"])
        except Exception:
            audio_path = None

    shots_out = []
    for sh in shots_in:
        idx = int(sh.get("index", 0))
        if idx < 1 or idx > len(seq):
            continue
        try:
            path = store.url_to_path(seq[idx - 1]["image_url"])
        except Exception:
            continue
        shots_out.append({
            "path": path,
            "duration": float(sh.get("duration") or 1.0),
            "note": sh.get("note", ""),
        })
    if not shots_out:
        raise HTTPException(400, "no valid shots after resolving indices")

    out_name = f"edit_{int(time.time())}.mp4"
    out_path = os.path.join(store.VIDEOS_DIR, out_name)
    transition = (r.transition or (r.plan or {}).get("transition") or "cut").lower()
    try:
        editor.assemble_video(
            shots_out, audio_path, out_path,
            transition=transition,
            width=r.width, height=r.height, fps=r.fps,
        )
    except Exception as ex:
        raise HTTPException(500, f"video assembly failed: {ex}")

    rel = os.path.relpath(out_path, store.DATA_DIR).replace(os.sep, "/")
    url = f"/data/{rel}"

    rec = {
        "id": store.new_id("edit"),
        "url": url,
        "audio_id": (st.get("audio") or {}).get("id"),
        "transition": transition,
        "plan": r.plan,
        "created": store.now(),
    }
    st.setdefault("edits", []).append(rec)
    store.save_state(st)
    return rec


@app.delete("/api/edits/{eid}")
def api_delete_edit(eid: str):
    st = store.load_state()
    st["edits"] = [e for e in st.get("edits", []) if e["id"] != eid]
    store.save_state(st)
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Export — bundle the whole project into a single downloadable ZIP
# --------------------------------------------------------------------------- #
def _safe_name(s: str, fallback: str = "item") -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_")
    return s[:60] or fallback


@app.get("/api/export/package")
def api_export_package():
    """Bundle script, voiceover, prompts, character sheets and rendered frames
    into one ZIP the browser downloads automatically. Everything is nested under
    a single <title>/ folder so it unzips as a tidy project folder."""
    st = store.load_state()
    script = st.get("script") or {}
    title = (script.get("title") or "").strip() or "continuity-project"
    root = _safe_name(title, "continuity-project")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # 1. script.json (full structured script)
        if script:
            z.writestr(f"{root}/script.json",
                       json.dumps(script, indent=2, ensure_ascii=False))

        # 2. voiceover.txt — prefer the top-level VO, else stitch scene VO lines.
        vo = (script.get("voiceover") or "").strip()
        if not vo:
            vo = "\n\n".join(
                (sc.get("vo") or "").strip()
                for sc in (script.get("scenes") or [])
                if (sc.get("vo") or "").strip()
            )
        if vo:
            z.writestr(f"{root}/voiceover.txt", vo)

        # 3. character_prompts.txt — packed, ready to paste into bulk generator.
        char_blocks = []
        for c in (script.get("characters") or []):
            name = (c.get("name") or "").strip()
            sheet = (c.get("sheet_prompt") or c.get("description") or "").strip()
            if name:
                char_blocks.append(f"{name}\n{sheet}".strip())
        if char_blocks:
            z.writestr(f"{root}/character_prompts.txt", "\n\n".join(char_blocks))

        # 4. scene_prompts.txt — one image prompt per line.
        scene_prompts = [
            (sc.get("prompt") or "").strip()
            for sc in (script.get("scenes") or [])
            if (sc.get("prompt") or "").strip()
        ]
        if scene_prompts:
            z.writestr(f"{root}/scene_prompts.txt", "\n".join(scene_prompts))

        # 5. master_prompt.txt
        if (st.get("master_prompt") or "").strip():
            z.writestr(f"{root}/master_prompt.txt", st["master_prompt"].strip())

        # 6. character sheets, named by character.
        used = {}
        for c in st.get("characters", []):
            try:
                data = store.read_image(c["sheet_url"])
            except Exception:
                continue
            ext = os.path.splitext(c["sheet_url"])[1].lstrip(".") or "png"
            base = _safe_name(c.get("name") or "character", "character")
            used[base] = used.get(base, 0) + 1
            suffix = "" if used[base] == 1 else f"_{used[base]}"
            z.writestr(f"{root}/characters/{base}{suffix}.{ext}", data)

        # 7. rendered sequence frames, in order.
        for s in st.get("sequence", []):
            try:
                data = store.read_image(s["image_url"])
            except Exception:
                continue
            ext = os.path.splitext(s["image_url"])[1].lstrip(".") or "png"
            z.writestr(f"{root}/frames/frame_{int(s.get('index', 0)):03d}.{ext}",
                       data)

        # 8. assembled edit videos, if any.
        for e in st.get("edits", []):
            try:
                path = store.url_to_path(e["url"])
                with open(path, "rb") as fh:
                    z.writestr(f"{root}/video/{os.path.basename(path)}", fh.read())
            except Exception:
                continue

        # Guarantee the folder exists even on an empty project.
        if not z.namelist():
            z.writestr(f"{root}/README.txt",
                       "Empty project — generate a script, characters or frames "
                       "first, then export again.")

    buf.seek(0)
    fname = f"{root}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
