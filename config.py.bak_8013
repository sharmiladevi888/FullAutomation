"""Central configuration. All values can be overridden via environment variables
(or a .env file loaded by app.py)."""
import os


def _get(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# --- derouter / OpenAI-compatible IMAGE endpoint ---------------------------
# IMPORTANT: always use the *api-direct* host for image gen — those calls are
# long-running (15–240 s) and the regular host would 524 from Cloudflare's
# 100 s timeout. api-direct gives 600 s.
API_KEY = _get("DEROUTER_API_KEY", "")
BASE_URL = _get("DEROUTER_BASE_URL", "https://api-direct.derouter.network/openai/v1")
MODEL = _get("IMAGE_MODEL", "gpt-image-2")
# Image gen can run long (a 2K/high render was observed at ~7.5 min). The
# api-direct host allows up to 600s, so default to that ceiling rather than
# aborting a good generation early.
TIMEOUT = int(_get("REQUEST_TIMEOUT", "600"))

# --- derouter ANTHROPIC-compatible (Claude) endpoint -----------------------
# Used for: script gen, prompts-from-video (vision), edit planning (vision).
CLAUDE_API_KEY = _get("CLAUDE_API_KEY", _get("ANTHROPIC_API_KEY", ""))
CLAUDE_BASE_URL = _get("CLAUDE_BASE_URL", "https://api.derouter.network/proxy")
CLAUDE_MODEL = _get("CLAUDE_MODEL", "claude-sonnet-4-6")

# --- ElevenLabs (voice-over text-to-speech) --------------------------------
# Turns the script's voice-over text into real spoken audio. Get a key at
# https://elevenlabs.io (Profile -> API key). Can also be pasted in Settings.
ELEVENLABS_API_KEY = _get("ELEVENLABS_API_KEY", "")
ELEVENLABS_BASE_URL = _get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1")
# Default voice id (public "Rachel"). The Edit tab lists your account's voices.
ELEVENLABS_VOICE_ID = _get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = _get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# --- default render settings -----------------------------------------------
DEFAULT_SIZE = _get("DEFAULT_SIZE", "1536x1024")
DEFAULT_QUALITY = _get("DEFAULT_QUALITY", "high")

# Whether the image proxy supports multiple reference images on /images/edits
# via the `image[]` multipart field.  The derouter docs only document a single
# `image` field, so the default is False: we composite all refs into one
# contact-sheet image and send it as the single documented `image` field.
# Flip this on ONLY if you've verified your proxy accepts `image[]` repeated
# fields — otherwise edits with multiple references will fail.
MULTI_IMAGE_EDIT = _get("MULTI_IMAGE_EDIT", "false").lower() == "true"

# ffmpeg: frames-per-second to sample when splitting an uploaded video.
FRAME_FPS = float(_get("FRAME_FPS", "1"))

# Where generated assets + project state live.
DATA_DIR = _get("DATA_DIR", "data")

SUPPORTED_SIZES = [
    "1024x1024", "1024x1536", "1536x1024", "2048x2048", "3840x2160", "auto",
]
SUPPORTED_QUALITIES = ["low", "medium", "high", "auto"]

CLAUDE_MODELS = [
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]
