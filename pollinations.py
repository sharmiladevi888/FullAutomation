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

Install: nothing extra (just ``requests``, already required by derouter).
"""
import io
import sys
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


def available_sizes(width: int = 1024, height: int = 1024):
    """Pollinations accepts any width/height; we round to multiples of 64 to
    avoid surprise aspect-ratio crops on some models."""
    return (max(64, (int(width) // 64) * 64),
            max(64, (int(height) // 64) * 64))


class PollinationsImageClient:
    """Free image-generation client backed by Pollinations.ai. No key, no
    signup, no rate-limit-at-our-volume. Same interface as
    ``derouter.ImageClient`` (generate / edit / ping) so callers don't
    care which provider is active."""

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
        if self.seed is not None:
            params["seed"] = int(self.seed)
        return params

    # ---- public ----------------------------------------------------- #

    def ping(self):
        """Cheap connectivity probe — fetch the /prompt endpoint with a
        tiny image and a tiny size so we don't burn bandwidth on a smoke
        test. Returns {'ok', 'models', 'configured_model', 'base_url'}."""
        try:
            url = f"{self.base_url}/prompt/{urllib.parse.quote('ping test')}"
            params = self._request_params("ping test", 64, 64)
            r = requests.get(url, params=params, timeout=min(30, self.timeout))
            if r.status_code >= 400 or not r.content or len(r.content) < 100:
                return {"ok": False, "error": f"HTTP {r.status_code}: empty response",
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
        # If we're being managed by an outer queue, run inside it. The image
        # queue knows nothing about Pollinations' retry semantics so we set
        # ``retry=False`` and do our own bounded retry below.
        w, h = self._parse_size(size)
        return self._generate_once(prompt, w, h)

    def _generate_once(self, prompt: str, width: int, height: int) -> bytes:
        """One shot at the API. 2 retries on transient 5xx/connection errors.

        Uses POST when the prompt + URL params would exceed ~1500 chars
        (GET path is fine for short prompts but Pollinations 404s on very
        long URLs — POST body has no such limit)."""
        last_err = None
        # GET path: fast for short prompts, idempotent, easy to debug.
        url = f"{self.base_url}/prompt/{urllib.parse.quote(prompt)}"
        params = self._request_params(prompt, width, height)
        # If URL would exceed safe GET length, switch to POST with body.
        estimated_url_len = len(url) + sum(len(f"{k}={v}&") for k, v in params.items())
        use_post = estimated_url_len > 1500
        for attempt in range(3):
            try:
                if use_post:
                    # POST body shape documented by pollinations.ai — JSON with
                    # the prompt and the same params as the GET query string.
                    payload = {"prompt": prompt, **params}
                    r = requests.post(
                        f"{self.base_url}/prompt/",
                        json=payload,
                        timeout=self.timeout,
                    )
                else:
                    r = requests.get(url, params=params, timeout=self.timeout)
                if r.status_code in (502, 503, 504):
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(
                        f"Pollinations failed (HTTP {r.status_code}): "
                        f"{r.text[:300]}")
                if not r.content:
                    raise RuntimeError("Pollinations returned empty body")
                # Re-encode JPEG → PNG so callers that expect PNG (and the
                # .png extension on the saved file) keep working unchanged.
                return self._normalise_to_png(r.content)
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(1.5 * (attempt + 1))
                continue
        raise RuntimeError(
            f"Pollinations failed after 3 attempts. Last error: {last_err}. "
            "Check your internet connection or try a different model.")

    def edit(self, prompt, images, size=None, quality=None,
             retry=True, index=0, **kwargs):
        """Pollinations doesn't accept multi-image input — fall back to
        prompt-only generation. We prepend a short style summary derived
        from the reference images so the look still carries across shots
        (the same way gpt-image-2 uses a contact sheet)."""
        if not images:
            return self.generate(prompt, size=size, quality=quality,
                                 retry=retry, index=index)
        # Best-effort style hint: read each image, summarise dominant
        # colour so the prompt at least says "in this colour family".
        hint = ""
        try:
            from PIL import Image
            if isinstance(images, (list, tuple)) and images:
                # ``images`` may be a list of file paths OR raw bytes OR PIL
                # Images — normalise to a single PIL.Image for sniffing.
                first = images[0]
                if isinstance(first, (bytes, bytearray)):
                    im = Image.open(io.BytesIO(first)).convert("RGB")
                elif hasattr(first, "convert"):  # PIL.Image
                    im = first.convert("RGB")
                else:  # file path
                    im = Image.open(first).convert("RGB")
                # Resize to 32x32 and average for a quick "mood colour".
                small = im.resize((32, 32))
                pixels = list(small.getdata())
                r = sum(p[0] for p in pixels) // len(pixels)
                g = sum(p[1] for p in pixels) // len(pixels)
                b = sum(p[2] for p in pixels) // len(pixels)
                # Rough mood tag — not perfect, but better than nothing.
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
        """``size`` may be '1024x1024' or (w, h) tuple."""
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
        """Re-encode whatever Pollinations returns as PNG so callers that
        write ``.png`` and expect ``image/png`` headers keep working
        unchanged. Falls back to the raw bytes on any decode error."""
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(jpeg_or_png_bytes))
            buf = io.BytesIO()
            im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(
                buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception:
            return jpeg_or_png_bytes
