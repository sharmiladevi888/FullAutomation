<div align="center">

# 🎬 Continuity Studio

### AI-Powered Image-Sequence & Video Pipeline

<p>
  <a href="#features"><img src="https://img.shields.io/badge/Features-blue?style=for-the-badge" alt="Features"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/Stack-FastAPI%20%2B%20FFmpeg%20%2B%20Claude-green?style=for-the-badge" alt="Stack"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Quick_Start-5_min-orange?style=for-the-badge" alt="Quick Start"></a>
  <a href="https://github.com/sharmiladevi888/continuity-studio-automation"><img src="https://img.shields.io/badge/GitHub-Repo-black?style=for-the-badge&logo=github" alt="Repo"></a>
</p>

<p>
  <img src="https://img.shields.io/badge/Status-Live-success?style=for-the-badge" alt="Status">
  <img src="https://img.shields.io/badge/OS-Windows%2010%2F11-blue?style=for-the-badge" alt="OS">
  <img src="https://img.shields.io/badge/License-Personal%20%2F%20Demo-lightgrey?style=for-the-badge" alt="License">
</p>

---

</div>

## 🧠 What is it?

**Continuity Studio** is an AI-native creative pipeline for generating consistent image sequences, character sheets, narrated scripts, and fully assembled videos.

It ties together **Claude (Anthropic)** for reasoning and **DeRouter** for image generation, all wrapped in a polished local web app.

> Designed for creators who want fast iteration between **prompt → image → voice → video**.

---

## ⚡ Features

<div align="center">

| Feature | Description |
|---------|-------------|
| 🎨 **Bulk Image Generation** | Generate hundreds of frames with prompt batching and previous-frame continuity |
| 🤖 **AI Script Generator** | Turn a title + concept into a paced script with voice-over lines |
| 👤 **Character Sheets** | Auto-consistent character sheets for production pipelines |
| 🎥 **Video → Prompts** | Upload a reference video; Claude Vision creates matching image prompts |
| ✂️ **Scene Detection** | FFmpeg-powered cut detection and timestamp extraction |
| 🗣️ **Voice-Over** | ElevenLabs integration for per-scene or full-script narration |
| 🎞️ **Auto Edit + Render** | Claude plans the EDL **with the narration text** so cuts match what's being said; FFmpeg assembles the final MP4 |
| 🖱️ **Cut Clicks** | ElevenLabs-generated click/whoosh/pop SFX mixed at **every frame change** (cached, style + volume configurable) |
| 🎮 **Neon Arena UI** | Premium gaming-style interface: HUD tabs, energy bars, cyber grid, WebAudio UI sounds (toggle with 🔊) |
| 📦 **Project Export** | One-click ZIP export of assets, scripts, prompts, and renders |

</div>

---

## 🛠️ Tech Stack

<div align="center">

| Layer | Tooling |
|-------|---------|
| **Frontend** | Vanilla JS, 5-tab app |
| **Backend** | FastAPI + Uvicorn |
| **Vision / LLM** | Claude Sonnet / Opus via Anthropic SDK + DeRouter proxy |
| **Image Gen** | GPT-Image-2 via DeRouter |
| **Audio** | ElevenLabs TTS |
| **Video** | FFmpeg + FFprobe |
| **Data** | Local JSON (`users.json`, `vault.json`, `project.json`) |
| **Hosting** | Localhost + Cloudflare Tunnel (optional VPS deploy) |

</div>

---

## 🧩 Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Continuity Studio                     │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│  01      │  02      │  03      │  04      │  05         │
│ Universe │ Script   │ Chars    │ Sequence │ Edit        │
│ & Tools  │ Gen      │          │          │ & Render    │
└──────────┴──────────┴──────────┴──────────┴─────────────┘
        ↓           ↓          ↓           ↓           ↓
   ┌──────────────────────────────────────────────────────┐
   │  FastAPI (app.py)                                    │
   │  • derouter.py           Image Generation            │
   │  • claude_client.py      Claude / Vision             │
   │  • voice.py              ElevenLabs                  │
   │  • pipeline.py           Prompts + continuity        │
   │  • editor.py + video.py  FFmpeg assembly             │
   └──────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

```bash
# 1. Clone
git clone https://github.com/sharmiladevi888/continuity-studio-automation.git
cd continuity-studio-automation

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

Open 👉 **http://localhost:8000**

---

## ⚙️ Configuration

All keys are handled via the in-app **Settings** panel and stored locally in `vault.json`.  
No API keys are committed to Git.

```txt
# Example .env structure (not tracked)
DEROUTER_API_KEY=sk-...
CLAUDE_BASE_URL=https://api.derouter.network/proxy
CLAUDE_MODEL=claude-sonnet-4-6
ELEVENLABS_API_KEY=...
```

### 🔀 Optional: route Claude through 9Router (token saver)

Continuity Studio can route its **Claude/text calls** (script gen, vision
prompts, edit planning) through a locally-running
[**9Router**](https://github.com/decolua/9router) instance for 20–40% token
savings and free/cheap multi-provider fallback. It's a drop-in alternative
provider — image generation is unaffected.

```bash
npm install -g 9router
9router            # dashboard at http://localhost:20128
```

Then in **Settings → Claude account → Provider**, pick **9Router**, paste the
API key from the 9Router dashboard, choose a model (e.g.
`kr/claude-sonnet-4.5`), and **Connect**. Or set it via `.env`:

```txt
NINEROUTER_API_KEY=...
NINEROUTER_BASE_URL=http://localhost:20128
NINEROUTER_MODEL=kr/claude-sonnet-4.5
```

---

## 📁 Project Map

```
├── app.py              # FastAPI routes and auth middleware
├── derouter.py         # GPT-Image-2 client
├── claude_client.py    # Anthropic SDK + DeRouter proxy
├── voice.py            # ElevenLabs client
├── pipeline.py         # Prompt assembly, batch parsing, continuity
├── editor.py           # Scene detection and video assembly
├── video.py            # FFmpeg frame extraction
├── store.py            # State + asset persistence under data/
├── config.py           # Env-driven settings
├── static/
│   └── index.html      # Full UI (5-tab studio)
├── data/               # Generated assets, uploads, renders
├── codes.json          # Beta access codes
├── users.json          # Registered users
└── vault.json          # Local API key storage (not committed)
```

---

## 🔌 API Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/api/auth` | Gmail login / registration |
| `POST` | `/api/master` | Save world bible / master prompt |
| `POST` | `/api/video` | Upload clip → extract frames |
| `POST` | `/api/scene-detect` | Detect scene changes |
| `POST` | `/api/characters` | Generate single character sheet |
| `POST` | `/api/characters/batch` | Bulk character generation |
| `POST` | `/api/generate` | Render one frame |
| `POST` | `/api/generate/batch` | Batch render with continuity |
| `POST` | `/api/script` | Claude script generator |
| `POST` | `/api/edit-plan` | Claude EDL planning |
| `POST` | `/api/render-video` | FFmpeg final render |
| `GET`  | `/api/export/package` | ZIP download |

> All video-building endpoints (`/api/render-video`, `/api/voiceover/scenes`,
> `/api/voiceover/auto`, `/api/voiceover/auto-synced`, `/api/voiceover/auto-flow`,
> `/api/build-video`, `/api/autopilot`) accept `cut_clicks: bool`,
> `cut_click_volume: float` and `cut_click_style: "click"|"camera"|"whoosh"|"pop"|"tick"` —
> a short ElevenLabs-generated sound is mixed at every frame change
> (generated once per style, then disk-cached in `data/sfx_cache/`).

---

## 🧪 Notes

- **Portable:** Designed for localhost-first; deploy anywhere with Python 3.11+
- **Cost-aware:** Each `gpt-image-2` high render is tracked; batch scripts avoid orphaned workers
- **No external DB:** Uses local JSON for users, vault, and project state
- **Cloudflare-ready:** Tunnels for external access without router config

---

<div align="center">

Built with 🖤 for the continuity-first creative workflow.

<br/>
<a href="https://github.com/sharmiladevi888/continuity-studio-automation"> ⭐ Star on GitHub </a>

</div>
