"""Pollinations.ai image client — 100% FREE, no API key.

Pollinations (https://image.pollinations.ai) is a free public image-generation
API that proxies multiple open models (FLUX, SDXL, DALL·E 3, etc.) with no
auth and no rate limits at the level this app uses. Same role as the
``ImageClient`` (derouter / 9router / direct) but with $0 cost.

Surface mirrors ``derouter.ImageClient`` so it slots straight into
``app.get_image_client()``:
  * ``generate(prompt, size=None, quality=None) -> PNG bytes``
  * ``edit(prompt, images, size=None, quality=None) -> PNG bytes``
    (Pollinations doesn't support multi-image input — we fall back to
    prompt-only generation, optionally prepending a short style summary
    derived from the reference images so the style still carries.)
  * ``ping() -> {'ok', 'models', 'configured_model', 'base_url'}``

Quality knobs Pollinations accepts: ``model``, ``seed``, ``nologo``,
``private``, ``enhance``, ``nologo``. We expose them via constructor args.

Rate-limit reality (2026): Pollinations enforces ~1 queued request per IP
and serves a fallback image for over-limit calls. To stay under the cap we
serialize requests through a process-wide lock + 2s spacing and treat
JSON / non-image responses as hard errors with a clear retry hint.

Install: nothing extra (just ``requests``, already required by derouter).
"""
import io
import json
import sys
import threading
import time
import urllib.parse

import requests


def _log(msg):
    print(f"[pollinations] {msg}", file=sys.stderr, flush=True)


# Catalogue of curated Pollinations models. Order = default order shown in
# the Settings UI. ``display`` is the human label, ``model`` is the value
# passed in the ?model= query string.
POLLINATIONS_MODELS = [
    {"id": "flux",         "name": "FLUX (default, fast + good quality)",      "model": "flux"},
    {"id": "flux-schnell", "name": "FLUX Schnell (4-step, fastest)",            "model": "flux-schnell"},
    {"id": "turbo",        "name": "SDXL Turbo (real-time, looser prompt)",     "model": "turbo"},
    {"id": "sd-xl",        "name": "Stable Diffusion XL",                       "model": "sd-xl"},
    {"id": "dall-e-3",     "name": "DALL·E 3 (best prompt adherence)",          "model": "dall-e-3"},
    {"id": "kd-midjourney","name": "KD-Midjourney (stylised)",                  "model": "kd-midjourney"},
    {"id": "sana",         "name": "Sana (compact)",                            "model": "sana"},
]

_MODEL_INDEX = {m["id"]: m for m in POLLINATIONS_MODELS}
DEFAULT_MODEL_ID = "flux"
DEFAULT_BASE_URL = "https://image.pollinations.ai"


# --------------------------------------------------------------------------- #
#  Global rate-limit guard
# --------------------------------------------------------------------------- #
# Pollinations allows ~1 queued request per IP. Multiple parallel calls from
# the same box get throttled and served the same fallback image (looks like
# "Pollinations keeps generating the same image"). To stay under the cap:
#   * A process-wide lock so requests go out one at a time.
#   * A 2-second floor between consecutive calls (configurable).
#   * On 429 / JSON-error response: exponential backoff up to 30s.
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_T = 0.0
_MIN_SPACING_S = 2.0
_BACKOFF_UNTIL = 0.0     # epoch seconds; client sleeps until this on next call


def _respect_rate_limit():
    """Block the calling thread until enough time has passed since the
    last Pollinations request (per-process). Also honours any active
    backoff window from a recent 429 / JSON-error response."""
    global _LAST_REQUEST_T
    while True:
        with _RATE_LOCK:
            now = time.time()
            sleep_for = max(0.0, _BACKOFF_UNTIL - now)
            gap = _LAST_REQUEST_T + _MIN_SPACING_S - now
            sleep_for = max(sleep_for, gap)
        if sleep_for <= 0:
            break
        time.sleep(sleep_for)
    with _RATE_LOCK:
        _LAST_REQUEST_T = time.time()


def _note_rate_limit_failure(retry_after: float = 10.0):
    """Called after a 429 / JSON-error to set a backoff window so the next
    Pollinations call sleeps before retrying."""
    global _BACKOFF_UNTIL
    with _RATE_LOCK:
        _BACKOFF_UNTIL = max(_BACKOFF_UNTIL, time.time() + retry_after)


def _note_rate_limit_success():
    """Clear the backoff window after a confirmed good response."""
    global _BACKOFF_UNTIL
    with _RATE_LOCK:
        _BACKOFF_UNTIL = 0.0


def _is_error_payload(body: bytes, content_type: str) -> bool:
    """Pollinations returns JSON ``{"error":"...", "message":"..."}`` when
    rate-limited or the queue is full — even on a HTTP 200 sometimes, when
    the CDN is over capacity. Treat any JSON-looking body as an error so
    we don't accidentally write a tiny JSON blob into the image store
    as if it were a successful PNG."""
    if not body or len(body) < 64:
        return True
    # Content-type can lie — sniff the first non-whitespace byte.
    head = body.lstrip()[:1]
    if head in (b"{", b"["):
        try:
            payload = json.loads(body[:4096].decode("utf-8", "ignore"))
        except Exception:
            return True
        if isinstance(payload, dict) and ("error" in payload or "message" in payload):
            return True
    if content_type and "json" in content_type.lower():
        return True
    return False


# --------------------------------------------------------------------------- #
#  Rate-limit fallback fingerprint detection
# --------------------------------------------------------------------------- #
# Pollinations' queue-full fallback image is byte-identical across requests
# even with different prompts + seeds (it's a cached "please wait" JPG).
# Track a small ring of recent response hashes — if the current response
# matches ANY of the last few, we know we're being rate-limited even though
# the HTTP status was 200 and the body looks like a real image. Reject
# loudly so the caller doesn't write the fallback into the image store.
_RECENT_HASHES = []   # FIFO of (timestamp, md5) tuples
_HASH_WINDOW_S = 60.0
_MAX_HASH_MEM = 8


def _record_hash(body: bytes) -> str:
    import hashlib as _hl
    h = _hl.md5(body).hexdigest()
    now = time.time()
    _RECENT_HASHES.append((now, h))
    # Trim old entries.
    while _RECENT_HASHES and now - _RECENT_HASHES[0][0] > _HASH_WINDOW_S:
        _RECENT_HASHES.pop(0)
    while len(_RECENT_HASHES) > _MAX_HASH_MEM:
        _RECENT_HASHES.pop(0)
    return h


def _is_rate_limit_fallback(body: bytes) -> bool:
    """True if ``body`` matches a recently-seen Pollinations response —
    i.e. Pollinations served its cached queue-full fallback instead of a
    fresh image. Different prompts + different seeds returning the same
    md5 within ~60s is the unmistakable signature."""
    if not body or len(body) < 200:
        return False
    h = _record_hash(body)
    seen = {hh for _t, hh in _RECENT_HASHES[:-1]}  # exclude the one we just added
    return h in seen


# --------------------------------------------------------------------------- #
#  Client
# --------------------------------------------------------------------------- #
def available_sizes(width: int = 1024, height: int = 1024):
    """Pollinations accepts any width/height; we round to multiples of 64 to
    avoid surprise aspect-ratio crops on some models."""
    return (max(64, (int(width) // 64) * 64),
            max(64, (int(height) // 64) * 64))


class PollinationsImageClient:
    """Free image-generation client backed by Pollinations.ai. No key, no
    signup, no rate-limit-at-our-volume. Same interface as
    ``derouter.ImageClient`` (generate / edit / ping) so callers don't
    care which provider is active.

    Rate-limit handling:
      * Every call goes through a process-wide lock + 2s floor so we stay
        under Pollinations' ~1-queued-per-IP cap.
      * On 429 or JSON-error response: exponential backoff up to 30s.
      * Tiny / JSON responses are rejected loudly with a retry hint,
        instead of being written to disk as if they were images.
    """

    def __init__(self, base_url=None, model=None, timeout=None,
                 enhance=False, private=False, nologo=True, seed=None):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.model = (model or DEFAULT_MODEL_ID)
        if self.model not in _MODEL_INDEX:
            # Unknown id from saved settings — fall back gracefully instead of 404.
            _log(f"unknown model id '{self.model}', falling back to '{DEFAULT_MODEL_ID}'")
            self.model = DEFAULT_MODEL_ID
        self.timeout = timeout or 180
        self.enhance = bool(enhance)
        self.private = bool(private)
        self.nologo = bool(nologo)
        self.seed = seed  # None → random per call

    # ---- helpers ---------------------------------------------------- #

    @staticmethod
    def _size_for(width: int = None, height: int = None):
        """Resolve size: caller-provided > config DEFAULT_SIZE > 1024x1024."""
        if not width or not height:
            try:
                import config
                w, h = (config.DEFAULT_SIZE or "1024x1024").lower().split("x")
                width = int(w) if not width else width
                height = int(h) if not height else height
            except Exception:
                width = width or 1024
                height = height or 1024
        return available_sizes(int(width), int(height))

    def _request_params(self, prompt, width, height):
        params = {
            "width": int(width),
            "height": int(height),
            "model": _MODEL_INDEX.get(self.model, _MODEL_INDEX[DEFAULT_MODEL_ID])["model"],
            "nologo": "true" if self.nologo else "false",
            "private": "true" if self.private else "false",
        }
        if self.enhance:
            params["enhance"] = "true"
        # ALWAYS pass a seed so Pollinations doesn't serve a cached image
        # for the same prompt. When the user wants deterministic output
        # (re-render the same exact frame) they can pass an int via the
        # constructor or future Settings UI. Random seed per call here =
        # every render gets a fresh image, even for identical prompts.
        import random as _random
        params["seed"] = int(self.seed) if self.seed is not None else _random.randint(0, 2**31 - 1)
        return params

    # ---- public ----------------------------------------------------- #

    def ping(self):
        """Cheap connectivity probe — fetch the /prompt endpoint with a
        tiny image and a tiny size so we don't burn bandwidth on a smoke
        test. Returns {'ok', 'models', 'configured_model', 'base_url'}."""
        try:
            _respect_rate_limit()
            url = f"{self.base_url}/prompt/{urllib.parse.quote('ping test')}"
            params = self._request_params("ping test", 64, 64)
            r = requests.get(url, params=params, timeout=min(30, self.timeout))
            if r.status_code >= 400 or _is_error_payload(r.content, r.headers.get("content-type", "")):
                return {"ok": False, "error": f"HTTP {r.status_code}: rate-limited or error",
                        "base_url": self.base_url}
            return {"ok": True,
                    "models": [m["id"] for m in POLLINATIONS_MODELS],
                    "configured_model": self.model,
                    "base_url": self.base_url}
        except Exception as e:
            return {"ok": False, "error": str(e), "base_url": self.base_url}

    def generate(self, prompt, size=None, quality=None, retry=True, index=0):
        """Text -> PNG bytes. Always returns a ``.png`` filename-friendly
        payload (Pollinations emits JPEG by default; we re-encode the
        downloaded bytes through Pillow to normalise the container so
        downstream code that expects ``image/png`` doesn't choke).
        """
        w, h = self._parse_size(size)
        return self._generate_once(prompt, w, h)

    def _generate_once(self, prompt: str, width: int, height: int) -> bytes:
        """One shot at the API. Honours the global rate-limit lock and
        retries on transient 5xx/429 with exponential backoff.

        Uses POST when the prompt + URL params would exceed ~1500 chars
        (GET path is fine for short prompts but Pollinations 404s on very
        long URLs — POST body has no such limit)."""
        last_err = None
        url = f"{self.base_url}/prompt/{urllib.parse.quote(prompt)}"
        params = self._request_params(prompt, width, height)
        estimated_url_len = len(url) + sum(len(f"{k}={v}&") for k, v in params.items())
        use_post = estimated_url_len > 1500
        for attempt in range(3):
            _respect_rate_limit()
            try:
                if use_post:
                    payload = {"prompt": prompt, **params}
                    r = requests.post(
                        f"{self.base_url}/prompt/",
                        json=payload,
                        timeout=self.timeout,
                    )
                else:
                    r = requests.get(url, params=params, timeout=self.timeout)
                ct = r.headers.get("content-type", "")
                # Detect Pollinations' JSON error envelopes (rate limits,
                # queue full) regardless of HTTP status — they often come
                # back as 200 with a JSON body when the CDN is over quota.
                if _is_error_payload(r.content, ct):
                    # Try to pull a human message out of the JSON.
                    msg = ""
                    try:
                        msg = json.loads(r.content[:4096].decode("utf-8", "ignore")).get("message", "")
                    except Exception:
                        msg = (r.content[:200] or b"").decode("utf-8", "ignore")
                    wait = min(30.0, 5.0 * (attempt + 1))
                    _note_rate_limit_failure(wait)
                    last_err = f"rate-limit / queue-full: {msg}"
                    time.sleep(wait)
                    continue
                # Pollinations' cached queue-full fallback returns HTTP 200
                # with what LOOKS like a real image. Detect it by hashing the
                # body and rejecting if it matches a recent response within
                # the last ~60s — different prompts + different seeds
                # producing the same md5 is the unmistakable rate-limit
                # signature. Saves us from writing the fallback into the
                # image store as if it were a successful render.
                if _is_rate_limit_fallback(r.content):
                    wait = min(30.0, 5.0 * (attempt + 1))
                    _note_rate_limit_failure(wait)
                    last_err = "queue-full fallback image detected (same bytes as recent response) — Pollinations is rate-limiting this IP. Wait a few seconds and retry, or switch to 'local' (diffusers) for sustained work."
                    time.sleep(wait)
                    continue
                if r.status_code in (429, 502, 503, 504):
                    wait = min(30.0, 5.0 * (attempt + 1))
                    _note_rate_limit_failure(wait)
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(wait)
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(
                        f"Pollinations failed (HTTP {r.status_code}): "
                        f"{r.text[:300]}")
                if not r.content or len(r.content) < 200:
                    last_err = f"tiny response ({len(r.content)} bytes)"
                    time.sleep(2.0)
                    continue
                _note_rate_limit_success()
                return self._normalise_to_png(r.content)
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(1.5 * (attempt + 1))
                continue
        raise RuntimeError(
            f"Pollinations failed after 3 attempts. Last error: {last_err}. "
            "The free pollinations.ai endpoint rate-limits each IP — wait a "
            "few seconds and try again, or use derouter for sustained work.")

    def edit(self, prompt, images, size=None, quality=None,
             retry=True, index=0, **kwargs):
        """Pollinations doesn't accept multi-image input — fall back to
        prompt-only generation. We prepend a short style summary derived
        from the reference images so the look still carries across shots
        (the same way gpt-image-2 uses a contact sheet)."""
        if not images:
            return self.generate(prompt, size=size, quality=quality,
                                 retry=retry, index=index)
        hint = ""
        try:
            from PIL import Image
            if isinstance(images, (list, tuple)) and images:
                first = images[0]
                if isinstance(first, (bytes, bytearray)):
                    im = Image.open(io.BytesIO(first)).convert("RGB")
                elif hasattr(first, "convert"):
                    im = first.convert("RGB")
                else:
                    im = Image.open(first).convert("RGB")
                small = im.resize((32, 32))
                pixels = list(small.getdata())
                r = sum(p[0] for p in pixels) // len(pixels)
                g = sum(p[1] for p in pixels) // len(pixels)
                b = sum(p[2] for p in pixels) // len(pixels)
                if max(r, g, b) - min(r, g, b) < 18:
                    mood = f"neutral grey RGB({r},{g},{b})"
                elif r > g and r > b:
                    mood = f"warm red/orange RGB({r},{g},{b})"
                elif b > r and b > g:
                    mood = f"cool blue RGB({r},{g},{b})"
                elif g > r and g > b:
                    mood = f"green RGB({r},{g},{b})"
                else:
                    mood = f"mixed RGB({r},{g},{b})"
                hint = (f" Style anchor: dominant palette {mood}. "
                        f"Keep the same look as the reference frames.")
        except Exception:
            pass
        return self.generate(prompt + hint, size=size, quality=quality,
                             retry=retry, index=index)

    # ---- utilities -------------------------------------------------- #

    @staticmethod
    def _parse_size(size):
        if size is None:
            return PollinationsImageClient._size_for()
        if isinstance(size, str):
            try:
                w, h = size.lower().split("x")
                return PollinationsImageClient._size_for(int(w), int(h))
            except Exception:
                return PollinationsImageClient._size_for()
        if isinstance(size, (tuple, list)) and len(size) == 2:
            return PollinationsImageClient._size_for(int(size[0]), int(size[1]))
        return PollinationsImageClient._size_for()

    @staticmethod
    def _normalise_to_png(jpeg_or_png_bytes: bytes) -> bytes:
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(jpeg_or_png_bytes))
            buf = io.BytesIO()
            im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(
                buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception:
            return jpeg_or_png_bytes
