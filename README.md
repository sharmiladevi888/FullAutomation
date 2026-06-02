# Continuity Studio

In-universe image-sequence engine with **bulk prompts**, **bulk character
sheets**, **Claude-powered script generation**, **prompts-from-video** (Claude
vision), **scene detection** (ffmpeg), and **audio-aware video editing** (Claude
plans the cut, ffmpeg assembles the MP4).

Two backends, both via the **derouter** proxy:

| Job | Endpoint | Model |
|-----|----------|-------|
| Image generate / edit | `https://api-direct.derouter.network/openai/v1` | `gpt-image-2` |
| Claude (script, vision, edit planning) | `https://api.derouter.network/proxy` | `claude-sonnet-4-6` (or any 4.x) |

---

**Tab order:** `01 Universe · 02 Script Generator · 03 Characters · 04 Sequence · 05 Edit`.
A **⤓ Export ZIP** button (top-right) bundles the whole project — script, voice-over,
character prompts, scene prompts, generated character sheets and rendered frames — into
one downloadable folder.

---

## What's new vs the original

0. **Tab 02 — Script Generator** (new)
   - Give a **title** + a **description of how the video should be**, choose a
     **Claude model** (default `claude-opus-4-8`), set **pacing** (sec/image) and
     **target duration**. Claude returns a **voice-over script**, exactly
     `round(duration / pacing)` **paced scenes** (one image each, with the VO slice
     for that image), and **packed per-character sheet prompts**.
   - One click pushes character prompts → Characters bulk gen, and scene prompts →
     Sequence batch. Everything also exports as a project ZIP.

1. **Tab 03 — Characters**
   - **Single generate** (as before).
   - **Bulk generate** — paste many characters separated by blank lines (first
     line is the name, rest is description). Generates a sheet for each.
   - **Load existing sheet** — upload your own reference image to use as a
     character sheet (no generation, no cost).

2. **Tab 04 — Sequence**
   - **Single render** (as before).
   - **Batch mode** — each line (or blank-line-separated block) becomes its
     own prompt. Each prompt independently re-runs character matching, so
     swapping `@Kael` for `@Mira` on the next line **swaps the reference sheet
     automatically**. Continuity (previous frame + style anchors) is preserved
     across the batch.

3. **Reference video → prompts** (in the Script tab)
   - Extract frames in tab 01, Claude reads them and writes N matching image
     prompts that recreate the look.

4. **Tab 05 — Edit (Audio + Video)** (new)
   - **🎙 Voice-over (ElevenLabs)** — turn the script's narration into real
     spoken audio. Pick a voice from your ElevenLabs account, then either:
     - **Whole script → audio track** — synthesizes one narration track and
       drops it into the Audio slot; then Plan + Render as usual.
     - **Per-scene → auto-timed video** — voices each numbered scene
       separately, measures each clip, and builds a video where sequence frame
       *N* is held for exactly the length of scene *N*'s line (no manual EDL).
   - Or **upload audio** (mp3/wav/m4a/…) yourself. Claude looks at every
     rendered frame + the audio length + your edit notes, returns an EDL (which
     frame, what order, for how long, what transition).
   - **Render video** — ffmpeg assembles the cut from your frames and muxes
     the audio. Cut / fade / crossfade transitions supported.

5. **Scene tools**
   - **ffmpeg scene detection** in tab 01 (timestamps of cuts in your sample
     clip).
   - **Per-frame analyse** — point Claude at any frame and ask "what's the
     lighting setup here?".

---

## Setup

```bash
cd continuity-studio
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # edit and paste your keys
```

`.env` (the **same derouter key works for both** image + Claude):

```
DEROUTER_API_KEY=sk-...
DEROUTER_BASE_URL=https://api-direct.derouter.network/openai/v1
IMAGE_MODEL=gpt-image-2
DEFAULT_SIZE=1536x1024
DEFAULT_QUALITY=high
MULTI_IMAGE_EDIT=true

CLAUDE_API_KEY=sk-...        # same key as above
CLAUDE_BASE_URL=https://api.derouter.network/proxy
CLAUDE_MODEL=claude-opus-4-8
```

Or paste the key in the in-app **Settings** panel → **Test connection** verifies
both APIs with a real authenticated call (not just a models list).

You also need **ffmpeg** + **ffprobe** on `PATH` (the video tab, scene
detection, and the editor all use it).

## Run

Windows — just double-click **`run.bat`** (installs deps on first run), or:

```bash
python -m uvicorn app:app --port 8000
```

> ⚠️ Use **`python -m uvicorn`**, not a bare `uvicorn`. A stray `uvicorn.exe` on
> PATH may belong to a different virtualenv that lacks Pillow and will crash with
> `ModuleNotFoundError: No module named 'PIL'`.

Open <http://localhost:8000>.

---

## Pipeline reference

| Endpoint | Job |
|---|---|
| `POST /api/master` | save world bible |
| `POST /api/video` | upload sample clip → extract frames (ffmpeg) |
| `POST /api/scene-detect` | timestamps of scene changes (ffmpeg) |
| `POST /api/style-frames` | mark selected frames as style anchors |
| `POST /api/characters` | generate one character sheet |
| `POST /api/characters/batch` | generate many sheets from one block of text |
| `POST /api/characters/upload` | upload an existing image as a sheet |
| `POST /api/generate` | render one frame (continuation engine) |
| `POST /api/generate/batch` | render many frames; each line gets its own char matching |
| `POST /api/script` | Claude: title+description → JSON script (VO + paced scenes + character prompts) |
| `GET /api/script/character-prompts` | packed character prompts from current script |
| `GET /api/export/package` | download whole project as a ZIP folder |
| `POST /api/prompts-from-video` | Claude vision: reference frames → N image prompts |
| `POST /api/analyse-scene` | Claude vision: describe one frame |
| `GET /api/voices` | list the ElevenLabs voices on your account |
| `POST /api/voiceover` | ElevenLabs: synthesize the whole script VO → one audio track (into the edit audio slot) |
| `POST /api/voiceover/scenes` | ElevenLabs: one clip per scene → auto-timed MP4 (each frame held for its line) |
| `POST /api/audio` | upload audio (probes duration with ffprobe) |
| `POST /api/edit-plan` | Claude: frames + audio length + brief → EDL |
| `POST /api/render-video` | ffmpeg: assemble final MP4 from EDL + audio |

## File map

| File | Role |
|------|------|
| `app.py` | FastAPI routes |
| `derouter.py` | gpt-image-2 client (OpenAI-compatible) |
| `claude_client.py` | Claude client via Anthropic SDK + derouter proxy |
| `voice.py` | ElevenLabs text-to-speech client (list voices, synthesize) |
| `pipeline.py` | name matching, prompt assembly, batch parsing, contact sheet |
| `editor.py` | ffmpeg scene detection + video assembly |
| `video.py` | ffmpeg frame extraction |
| `store.py` | JSON state + media persistence under `data/` |
| `config.py` | env-driven settings |
| `static/index.html` | the whole UI (vanilla JS, 5 tabs) |
