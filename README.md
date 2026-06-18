<div align="center">

# Full Automation

### AI Video Studio — Audio-to-Video · YouTube Workflow · Continuity Engine

<p>
  <a href="#features"><img src="https://img.shields.io/badge/Features-blue?style=for-the-badge" alt="Features"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Quick_Start-5_min-orange?style=for-the-badge" alt="Quick Start"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/Stack-FastAPI%20%2B%20FFmpeg%20%2B%20Claude-green?style=for-the-badge" alt="Stack"></a>
  <a href="https://github.com/sharmiladevi888/FullAutomation"><img src="https://img.shields.io/badge/GitHub-FullAutomation-black?style=for-the-badge&logo=github" alt="Repo"></a>
</p>

<p>
  <img src="https://img.shields.io/badge/Status-Live-success?style=for-the-badge" alt="Status">
  <img src="https://img.shields.io/badge/OS-Windows%2010%2F11-blue?style=for-the-badge" alt="OS">
  <img src="https://img.shields.io/badge/Python-3.11+-yellow?style=for-the-badge" alt="Python">
</p>

---

</div>

## What is it?

**Full Automation** is an AI-native creative pipeline that generates consistent image sequences, character sheets, narrated scripts, and fully assembled videos — from a YouTube link **or** your own audio.

Two workflows, one studio:

| Workflow | Input | What it does |
|----------|-------|-------------|
| **Audio-to-Video** | Upload audio + paste a sample-video link | Transcribes your audio (word-level timestamps), analyses the sample video's art style, writes one visual scene per transcript segment, renders style-locked frames, builds a final MP4 synced to your actual words |
| **YouTube Autopilot** | Paste a YouTube link | Analyses the video's style + speech, generates topics, writes a script, auto-casts characters, renders frames with continuity, builds a narrated video with ElevenLabs TTS |

Both workflows share the same engine: style-locked frame generation, auto-cast character sheets, micro-cut continuity, and frame-accurate A/V sync.

---

## Features

### Audio-to-Video

- **Upload your own audio** — the final video uses YOUR voice/music, no TTS re-synthesis
- **Paste a sample-video link** — YouTube link to frame extraction to art-style analysis to style anchors
- **Word-level transcription** — Local Whisper (free, on your PC) or ElevenLabs Scribe (reuses your key)
- **Frame-accurate A/V sync** — Whisper word timestamps drive exact per-scene hold durations
- **High-retention pacing** — fast micro-cuts, reacting visuals, Ken-Burns motion
- **Character sheets auto-cast** — Claude decides how many characters the story needs, generates style-anchored sheets

### YouTube Autopilot

- **One-click pipeline** — paste a link to topics to script to characters to frames to video to thumbnail to SEO
- **Smart edit planner** — Claude plans per-scene hold durations from narration energy
- **Continuity engine** — style frames from the source video guide every generated image
- **Micro-cut vs new-beat** — shot_relation tells the renderer when to reuse vs compose fresh

### Core Engine

- **Bulk frame generation** with previous-frame continuity and per-ref labelled contact sheets
- **Character sheet generation** — style-anchored to the source video's look
- **Multi-provider image gen** — DeRouter (gpt-image-2), 9Router, direct OpenAI-compatible
- **Multi-provider AI** — Claude (Anthropic/DeRouter/9Router/AgentRouter), Gemini
- **ElevenLabs TTS** — per-scene or continuous narration, word-level timestamps, chunked for long VO
- **Sound design** — ElevenLabs SFX (rumble bed + contextual point-SFX), cut clicks (5 styles)
- **Video assembly** — FFmpeg, fade/crossfade/motion transitions, concurrent-safe temp dirs
- **Thumbnail generator** — style-matched, click-worthy, YouTube-optimized

### UI

- **8-tab studio** — Universe, YT Analyser, Script Generator, Characters, Sequence, Edit, Audio-to-Video, Timeline
- **Project system** — multiple projects, import/export ZIP
- **Usage dashboard** — per-generation cost tracking
- **WebAudio UI sounds** — toggle with speaker icon

---

## Tech Stack

| Layer | Tooling |
|-------|---------|
| **Frontend** | Vanilla JS, single-file SPA |
| **Backend** | FastAPI + Uvicorn |
| **AI / LLM** | Claude (Anthropic SDK), Gemini, OpenAI-compatible proxies |
| **Image Gen** | GPT-Image-2 via DeRouter / 9Router / direct |
| **Transcription** | Local Whisper (faster-whisper, free) + ElevenLabs Scribe |
| **Voice** | ElevenLabs TTS (with word-level timestamps) |
| **Video** | FFmpeg + FFprobe |
| **Data** | Local JSON (vault, users, project state) |
| **Hosting** | Localhost + Cloudflare Tunnel (optional) |

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/sharmiladevi888/FullAutomation.git
cd FullAutomation

# 2. Env
cp .env.example .env

# 3. Venv
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate   # macOS / Linux

# 4. Install
pip install -r requirements.txt

# 5. Run
python -m uvicorn app:app --port 8000
```

Open **http://localhost:8000**

### Optional: Local Whisper (free transcription)

```bash
pip install faster-whisper
```

No API key needed — runs on your CPU, word-level timestamps, perfect for Audio-to-Video.

### Optional: 9Router (token saver for Claude)

```bash
npm install -g 9router
9router   # dashboard at http://localhost:20128
```

Routes Claude calls through a local proxy for 20-40% token savings. Pick 9Router in Settings, Claude provider.

---

## Configuration

All keys are managed via the in-app Settings panel and stored locally in `vault.json` (git-ignored, encrypted at rest).

### Supported Providers

| Role | Providers |
|------|-----------|
| **AI / Text** | Claude (direct, DeRouter, 9Router, AgentRouter), Gemini |
| **Image Gen** | DeRouter (gpt-image-2), 9Router, direct OpenAI-compatible |
| **Voice** | ElevenLabs, Xiaomi MiMo |
| **Transcription** | Local Whisper (faster-whisper, free), ElevenLabs Scribe |

---

## Project Map

```
app.py              FastAPI routes, auth, autopilot, Audio-to-Video engine
claude_client.py    AI client (Claude/Gemini/OpenAI) + script/scene gen
transcribe.py       Audio transcription (local Whisper + ElevenLabs Scribe)
voice.py            ElevenLabs TTS (with timestamp chunking for long VO)
pipeline.py         Prompt assembly, contact sheets, style locking
editor.py           FFmpeg video assembly (concurrent-safe temp dirs)
video.py            FFmpeg frame extraction
derouter.py         GPT-Image-2 client
image_queue.py      Rate-limited bulk frame generation
store.py            State + asset persistence under data/
config.py           Env-driven settings
vault_crypto.py     Encrypted API key storage
punchup.py          Script enhancement
static/
  index.html        Full UI (8-tab studio)
data/               Generated assets, uploads, renders (git-ignored)
.env.example        Template for local config
requirements.txt    Python dependencies
```

---

## Key API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | /api/autopilot | Full YouTube pipeline (link to video) |
| POST | /api/audio-to-video | Audio-to-Video (your audio + sample link to video) |
| POST | /api/audio-to-video/upload | Upload audio/video files |
| POST | /api/audio-to-video/sample-link | Fetch style frames from YouTube link |
| POST | /api/generate | Render one frame |
| POST | /api/generate/batch | Batch render with continuity |
| POST | /api/script | AI script generator |
| POST | /api/characters | Generate character sheet |
| POST | /api/voiceover/auto-flow | Natural-flow narrated video |
| POST | /api/build-video | Assemble frames + audio to MP4 |
| POST | /api/settings | Save API keys + provider config |
| GET  | /api/health | Connection test for all providers |
| GET  | /api/export/package | ZIP download of project |

All video endpoints accept cut_clicks, cut_click_volume, and cut_click_style — a short SFX is mixed at every frame change (cached in data/sfx_cache/).

---

## Notes

- **Portable:** localhost-first, deploy anywhere with Python 3.11+
- **Cost-aware:** per-generation tracking, rate-limit backoff, budget-conscious defaults
- **No external DB:** local JSON for users, vault, and project state
- **Cloudflare-ready:** tunnels for external access
- **Concurrent-safe:** unique temp dirs per render, locked state writes
- **Security:** vault.json encrypted at rest, all uploads sanitized, secrets git-ignored

---

<div align="center">

Built with love for the continuity-first creative workflow.

[Star on GitHub](https://github.com/sharmiladevi888/FullAutomation)

</div>
