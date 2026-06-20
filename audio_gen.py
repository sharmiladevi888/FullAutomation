"""Audio Studio — voice, music, and SFX generation in one place.

Three providers:

* **Voice** (text-to-speech) — delegates to ``voice.VoiceClient /
  MimoVoiceClient / DeepgramVoiceClient / PiperVoiceClient`` via
  ``app.get_voice_client``. Supports ElevenLabs, Xiaomi MiMo, Deepgram Aura,
  and local Piper (free, CPU/GPU).

* **Music** (text-to-music) — wraps Meta's MusicGen via the ``audiocraft``
  package. Heavyweight (torch + audiocraft, several GB). Lazy-imported so the
  module loads cleanly when audiocraft isn't installed — callers get a clear
  ``RuntimeError`` with install instructions instead of an ImportError at
  import time. CPU-capable (slow), GPU strongly recommended.

* **SFX** (text-to-sound-effect) — delegates to whichever voice client is
  active (``voice_client.generate_sfx(text, duration_seconds=...)``), which
  handles ElevenLabs Sound Generation natively. Falls back to AudioGen
  (local, Meta audiocraft) if audiocraft is installed — also lazy-imported.

All clients return ``(audio_bytes, audio_format)`` so callers can write the
file with the right extension. Audio is cached on disk keyed by an SHA-1 of
(prompt + provider + knobs) so identical re-generations are free.

Design choice: we don't subclass the existing voice client for music/SFX
because those are completely different models (audiocraft vs TTS API).
Voice uses the existing client surface so the 4 voice providers work
identically here and in the Edit tab.
"""
import hashlib
import io
import os
import shutil
import subprocess
import tempfile
import time
import wave

import config
import store

# --------------------------------------------------------------------------- #
#  Public constants
# --------------------------------------------------------------------------- #

# Where generated audio clips are persisted (gitignored, encrypted-vault-style
# is unnecessary — these are MP3s/WAVs the user explicitly generated).
AUDIO_GEN_DIR_NAME = "audio_gen"

# Audacity-friendly WAV params for everything we synthesise in-process.
_WAV_SAMPLE_RATE = 44100
_WAV_CHANNELS = 1
_WAV_SAMPLE_WIDTH = 2  # 16-bit PCM


# --------------------------------------------------------------------------- #
#  Path helpers
# --------------------------------------------------------------------------- #
def audio_gen_dir():
    """Resolve the on-disk folder for the Audio Studio library. Created on
    first call."""
    d = os.path.join(getattr(config, "DATA_DIR", "data"), AUDIO_GEN_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def audio_cache_path(key: str, ext: str) -> str:
    """Stable on-disk path for a cached generated clip. ``key`` is the
    SHA-1 hex of (prompt + provider + knobs); ``ext`` is the file extension
    (mp3 / wav)."""
    safe = "".join(c for c in key if c.isalnum())[:40] or "audio"
    return os.path.join(audio_gen_dir(), f"{safe}.{ext.lstrip('.')}")


def cache_key(prompt: str, provider: str, **knobs) -> str:
    """Deterministic SHA-1 for a (prompt, provider, knobs) tuple — used as
    the dedup key for cached audio so identical re-generations are free."""
    parts = [provider or "", (prompt or "").strip()]
    for k in sorted(knobs):
        v = knobs[k]
        if v is None:
            continue
        parts.append(f"{k}={v}")
    raw = "\n".join(parts).encode("utf-8", "ignore")
    return hashlib.sha1(raw).hexdigest()


def write_audio_clip(audio: bytes, ext: str, name_hint: str = "",
                     prompt: str = "", provider: str = "") -> dict:
    """Persist a generated clip to the audio library and return a metadata
    dict (id, url, path, ext, duration_seconds, size_bytes, prompt, provider,
    created). The clip is also cached by content hash for fast re-render."""
    ext = (ext or "wav").lstrip(".")
    d = audio_gen_dir()
    clip_id = store.new_id("aud")
    fname = f"{clip_id}"
    if name_hint:
        safe = "".join(c for c in name_hint if c.isalnum() or c in "._-")[:60]
        if safe:
            fname += "_" + safe
    fname += "." + ext
    path = os.path.join(d, fname)
    with open(path, "wb") as f:
        f.write(audio)
    duration = _wav_duration(audio) if ext == "wav" else None
    meta = {
        "id": clip_id,
        "url": f"/data/{AUDIO_GEN_DIR_NAME}/{fname}",
        "path": path,
        "ext": ext,
        "size_bytes": len(audio),
        "duration_seconds": duration,
        "prompt": (prompt or "")[:500],
        "provider": provider or "",
        "name_hint": name_hint or "",
        "created": store.now(),
    }
    # Sidecar JSON so the library survives across server restarts.
    try:
        import json as _json
        with open(path + ".json", "w", encoding="utf-8") as f:
            _json.dump(meta, f, indent=2)
    except Exception:
        pass
    return meta


def list_audio_clips(limit: int = 200) -> list:
    """List saved Audio Studio clips, newest first. Reads sidecar JSON."""
    import json as _json
    d = audio_gen_dir()
    out = []
    try:
        names = os.listdir(d)
    except Exception:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                meta = _json.load(f)
        except Exception:
            continue
        # Drop orphaned entries (audio file deleted out from under us).
        if not os.path.exists(meta.get("path", "")):
            try:
                os.remove(path)
            except Exception:
                pass
            continue
        out.append(meta)
    out.sort(key=lambda m: m.get("created") or "", reverse=True)
    return out[:limit]


def delete_audio_clip(clip_id: str) -> bool:
    """Delete a clip + its sidecar JSON. Returns True if anything was removed."""
    d = audio_gen_dir()
    removed = False
    for name in os.listdir(d):
        if not name.startswith(clip_id):
            continue
        try:
            os.remove(os.path.join(d, name))
            removed = True
        except Exception:
            pass
    return removed


# --------------------------------------------------------------------------- #
#  Voice — delegates to the active voice client (no copy of provider logic)
# --------------------------------------------------------------------------- #
def synth_voice(request, get_voice_client_fn, text: str, voice_id: str = None,
                stability: float = 0.5, similarity_boost: float = 0.75,
                style: float = 0.0, use_timestamps: bool = False):
    """Synthesise ``text`` using the active voice provider.

    ``get_voice_client_fn`` is the existing ``app.get_voice_client`` so this
    respects the user's saved voice_provider choice (ElevenLabs / MiMo /
    Deepgram / Piper) and its credentials. ``request`` is the FastAPI
    request — forwarded into get_voice_client so settings resolve correctly.

    Returns ``(audio_bytes, ext, alignment_or_None)``. ``ext`` is 'mp3' for
    cloud providers and 'wav' for Piper.
    """
    text = (text or "").strip()
    if not text:
        raise RuntimeError("Voice prompt is empty.")
    client = get_voice_client_fn(request, voice_id=voice_id)
    if use_timestamps and hasattr(client, "synthesize_with_timestamps"):
        audio, alignment = client.synthesize_with_timestamps(
            text, voice_id=voice_id,
            stability=stability, similarity_boost=similarity_boost, style=style,
        )
    else:
        audio = client.synthesize(
            text, voice_id=voice_id,
            stability=stability, similarity_boost=similarity_boost, style=style,
        )
        alignment = None
    # Detect container: Piper always returns WAV, cloud providers return MP3.
    ext = "wav" if (audio[:4] == b"RIFF") else "mp3"
    return audio, ext, alignment


# --------------------------------------------------------------------------- #
#  Music — Meta MusicGen via audiocraft (lazy import)
# --------------------------------------------------------------------------- #
MUSICGEN_MODELS = {
    # short_id         display name                  HF repo (audiocraft)        params
    "small":          ("MusicGen Small (local)",     "facebook/musicgen-small",  "300M"),
    "medium":         ("MusicGen Medium (local)",    "facebook/musicgen-medium", "1.5B"),
    "large":          ("MusicGen Large (local)",     "facebook/musicgen-large",  "3.3B"),
    "melody":         ("MusicGen Melody (local)",    "facebook/musicgen-melody", "1.5B"),
}

_musicgen_model_cache = {}


def musicgen_available() -> bool:
    """True iff the audiocraft package + torch can be imported. CPU-only is
    OK; we don't require CUDA."""
    try:
        import torch  # noqa: F401
        import audiocraft  # noqa: F401
        return True
    except Exception:
        return False


def musicgen_install_hint() -> str:
    return ("MusicGen isn't installed. To enable local music generation:\n"
            "    pip install audiocraft torch\n"
            "(first run downloads ~1.5 GB of model weights into "
            "data/audio_models/). A CUDA GPU is strongly recommended; CPU "
            "works but is ~10x slower.")


def _get_musicgen_model(size: str):
    """Lazy-load MusicGen, caching across calls. size ∈ {small, medium,
    large, melody}."""
    if size in _musicgen_model_cache:
        return _musicgen_model_cache[size]
    if size not in MUSICGEN_MODELS:
        raise RuntimeError(
            f"Unknown MusicGen size '{size}'. Pick one of: "
            + ", ".join(sorted(MUSICGEN_MODELS.keys())))
    try:
        from audiocraft.models import MusicGen
    except Exception as e:
        raise RuntimeError(musicgen_install_hint()) from e
    import torch
    name, hf_repo, _params = MUSICGEN_MODELS[size]
    use_cuda = bool(torch.cuda.is_available())
    # audiocraft reads TORCH_HOME / AUDIOCRAFT_CACHE for model storage; we
    # override per-app so downloads don't pollute ~/.cache.
    cache_dir = os.path.join(getattr(config, "DATA_DIR", "data"), "audio_models")
    os.makedirs(cache_dir, exist_ok=True)
    os.environ.setdefault("AUDIOCRAFT_CACHE_DIR", cache_dir)
    t0 = time.time()
    model = MusicGen.get_pretrained(hf_repo, device="cuda" if use_cuda else "cpu")
    _musicgen_model_cache[size] = model
    print(f"[audio_gen] loaded {name} in {time.time()-t0:.1f}s "
          f"({'CUDA' if use_cuda else 'CPU'})", flush=True)
    return model


def synth_music(prompt: str, duration_seconds: float = 10.0,
                size: str = "small", temperature: float = 1.0,
                top_k: int = 250, top_p: float = 0.0) -> bytes:
    """Text prompt → WAV bytes via local MusicGen.

    * ``prompt``     descriptive text (e.g. "lo-fi hip hop, mellow piano,
                      soft drums, vinyl crackle")
    * ``duration_seconds``  1-30s (MusicGen hard-cap is ~30s)
    * ``size``       small (300M, fast) / medium (1.5B, default quality) /
                     large (3.3B, best quality, GPU strongly recommended) /
                     melody (1.5B, accepts a melody reference)
    * ``temperature``prosody / variation, 0.0-2.0 (default 1.0)
    * ``top_k``      sampling parameter (default 250)
    * ``top_p``      nucleus sampling, 0.0-1.0 (default 0.0)
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise RuntimeError("Music prompt is empty.")
    dur = max(1.0, min(30.0, float(duration_seconds)))
    model = _get_musicgen_model(size)
    model.set_generation_params(
        duration=dur,
        temperature=float(temperature),
        top_k=int(top_k),
        top_p=float(top_p),
    )
    # MusicGen accepts a list of descriptions, one per output. We generate
    # one clip per request — callers can request N by calling N times.
    import torch
    with torch.no_grad():
        wav = model.generate([prompt], progress=False)
    # wav shape: (1, channels, samples). MusicGen emits mono 32 kHz float32.
    audio_f32 = wav[0, 0].cpu().numpy()
    return _float32_to_wav_bytes(audio_f32, sample_rate=32000)


# --------------------------------------------------------------------------- #
#  SFX — ElevenLabs Sound Generation (cloud) + AudioGen (local, lazy)
# --------------------------------------------------------------------------- #
AUDIOGEN_MODELS = {
    "default": ("AudioGen (local)", "facebook/audiogen-medium", "1.5B"),
}

_audiogen_model_cache = {}


def audiogen_available() -> bool:
    try:
        import torch  # noqa: F401
        import audiocraft  # noqa: F401
        return True
    except Exception:
        return False


def audiogen_install_hint() -> str:
    return ("AudioGen isn't installed. To enable local SFX generation:\n"
            "    pip install audiocraft torch\n"
            "(first run downloads ~1.5 GB of model weights into "
            "data/audio_models/). A CUDA GPU is strongly recommended; CPU "
            "works but is ~10x slower.")


def _get_audiogen_model():
    """Lazy-load AudioGen medium, caching across calls."""
    if "default" in _audiogen_model_cache:
        return _audiogen_model_cache["default"]
    try:
        from audiocraft.models import AudioGen
    except Exception as e:
        raise RuntimeError(audiogen_install_hint()) from e
    import torch
    cache_dir = os.path.join(getattr(config, "DATA_DIR", "data"), "audio_models")
    os.makedirs(cache_dir, exist_ok=True)
    os.environ.setdefault("AUDIOCRAFT_CACHE_DIR", cache_dir)
    use_cuda = bool(torch.cuda.is_available())
    t0 = time.time()
    model = AudioGen.get_pretrained(
        "facebook/audiogen-medium", device="cuda" if use_cuda else "cpu")
    _audiogen_model_cache["default"] = model
    print(f"[audio_gen] loaded AudioGen medium in {time.time()-t0:.1f}s "
          f"({'CUDA' if use_cuda else 'CPU'})", flush=True)
    return model


def synth_sfx_local(prompt: str, duration_seconds: float = 2.0) -> bytes:
    """Text → WAV bytes via local AudioGen. 1-10s. CPU-capable, GPU faster."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise RuntimeError("SFX prompt is empty.")
    dur = max(0.5, min(10.0, float(duration_seconds)))
    model = _get_audiogen_model()
    model.set_generation_params(duration=dur)
    import torch
    with torch.no_grad():
        wav = model.generate([prompt], progress=False)
    audio_f32 = wav[0, 0].cpu().numpy()
    return _float32_to_wav_bytes(audio_f32, sample_rate=16000)


# --------------------------------------------------------------------------- #
#  Small helpers
# --------------------------------------------------------------------------- #
def _float32_to_wav_bytes(audio_f32, sample_rate: int) -> bytes:
    """Convert a [-1, 1] float32 numpy array to a 16-bit PCM WAV byte string.
    Clips out-of-range samples to prevent wrap-around distortion."""
    import numpy as _np
    clipped = _np.clip(audio_f32, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(_np.int16)
    buf = io.BytesIO()
    wf = wave.open(buf, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(int(sample_rate))
    wf.writeframes(pcm.tobytes())
    wf.close()
    return buf.getvalue()


def _wav_duration(audio: bytes) -> float:
    """Return the duration in seconds of a WAV byte string, or 0 on failure."""
    try:
        wf = wave.open(io.BytesIO(audio))
        dur = wf.getnframes() / max(1, wf.getframerate())
        wf.close()
        return float(dur)
    except Exception:
        return 0.0
