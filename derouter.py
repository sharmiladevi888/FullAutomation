"""Thin client around the derouter (OpenAI-compatible) image endpoint.

generate() uses the OpenAI SDK (clean), edit() uses raw multipart so we can
attach reference images (character sheets + previous frame + style anchors).

By default we composite multiple references into ONE contact-sheet PNG and
send it as the single documented `image` multipart field — that's the only
field name the derouter docs document. If your proxy supports repeated
`image[]` fields, set MULTI_IMAGE_EDIT=true in .env and pass the list straight
through; this client supports both modes.
"""
import base64
import json
import sys
import time

import requests
from openai import OpenAI, APIError, APIConnectionError, APITimeoutError, AuthenticationError

import config


def _log(msg):
    print(f"[derouter] {msg}", file=sys.stderr, flush=True)


class ImageClient:
    def __init__(self, api_key=None, base_url=None, model=None, timeout=None):
        self.api_key = api_key or config.API_KEY
        self.base_url = (base_url or config.BASE_URL).rstrip("/")
        self.model = model or config.MODEL
        self.timeout = timeout or config.TIMEOUT
        # SDK is used for generations; api_key may be empty until set in UI.
        self._sdk = OpenAI(
            api_key=self.api_key or "unset",
            base_url=self.base_url,
            timeout=self.timeout,
        )

    def _require_key(self):
        if not self.api_key:
            raise RuntimeError(
                "No image API key set. Add DEROUTER_API_KEY to your .env or "
                "paste a key in the Settings panel."
            )

    # ------------------------------------------------------------------ #
    #  Connectivity check — cheap, lists available models.
    # ------------------------------------------------------------------ #
    def ping(self):
        """Hit /models with the configured key to verify auth + reachability.
        Returns {'ok': True, 'models': [...]} or {'ok': False, 'error': str}.
        """
        if not self.api_key:
            return {"ok": False, "error": "no api key set"}
        url = f"{self.base_url}/models"
        try:
            r = requests.get(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=20,
            )
        except requests.RequestException as e:
            return {"ok": False, "error": f"connection failed: {e}"}
        if r.status_code >= 400:
            return {
                "ok": False,
                "error": f"HTTP {r.status_code}: {r.text[:300]}",
            }
        try:
            data = r.json()
            ids = [m.get("id") for m in (data.get("data") or [])][:30]
        except Exception:
            ids = []
        return {"ok": True, "models": ids, "configured_model": self.model,
                "base_url": self.base_url}

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _format_openai_error(e):
        """Pull the real reason out of an OpenAI SDK exception, including
        the raw response body if it's available, so the user sees what
        derouter actually said rather than a generic 'BadRequestError'."""
        klass = type(e).__name__
        msg = str(e) or "no message"
        body = None
        # The SDK exposes the response on most error subclasses.
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                body = resp.text[:600]
            except Exception:
                body = None
        if body:
            return f"{klass}: {msg} | response: {body}"
        return f"{klass}: {msg}"

    # ------------------------------------------------------------------ #
    #  Public
    # ------------------------------------------------------------------ #
    def generate(self, prompt, size=None, quality=None):
        """Text -> image. Returns PNG bytes."""
        self._require_key()
        size = size or config.DEFAULT_SIZE
        quality = quality or config.DEFAULT_QUALITY
        kwargs = {"model": self.model, "prompt": prompt}
        if size and size != "auto":
            kwargs["size"] = size
        if quality and quality != "auto":
            kwargs["quality"] = quality

        _log(f"generate model={self.model} size={size} quality={quality} "
             f"prompt_len={len(prompt)} base={self.base_url}")
        t0 = time.time()
        try:
            r = self._sdk.images.generate(**kwargs)
        except (APIConnectionError, APITimeoutError) as e:
            raise RuntimeError(
                f"could not reach image API at {self.base_url} — "
                f"{self._format_openai_error(e)}"
            )
        except AuthenticationError as e:
            raise RuntimeError(
                f"image API rejected the key — {self._format_openai_error(e)}"
            )
        except APIError as e:
            raise RuntimeError(
                f"image API error — {self._format_openai_error(e)}"
            )
        dt = time.time() - t0
        _log(f"generate ok in {dt:.1f}s")

        if not r.data or not getattr(r.data[0], "b64_json", None):
            raise RuntimeError(
                f"image API returned no b64_json (got: {r.model_dump_json()[:300]})"
            )
        return base64.b64decode(r.data[0].b64_json)

    def edit(self, prompt, images, size=None, quality=None):
        """Reference image(s) + prompt -> image. ``images`` is list[bytes].

        With config.MULTI_IMAGE_EDIT=False (default + only path documented by
        derouter), the caller is expected to have already composited multiple
        refs into a single PNG; we still defensively handle the case where
        len(images)>1 by sending only the first.

        With MULTI_IMAGE_EDIT=True, we send repeated `image[]` fields — only
        do this if you've verified your proxy supports it.

        Returns PNG bytes.
        """
        self._require_key()
        if not images:
            raise ValueError("edit() needs at least one reference image")
        size = size or config.DEFAULT_SIZE
        quality = quality or config.DEFAULT_QUALITY

        files = []
        if config.MULTI_IMAGE_EDIT and len(images) > 1:
            for i, img in enumerate(images):
                files.append(("image[]", (f"ref_{i}.png", img, "image/png")))
            mode = f"image[]x{len(images)}"
        else:
            # The documented derouter path: ONE image field.
            files.append(("image", ("ref.png", images[0], "image/png")))
            mode = "image (single)"

        data = {"model": self.model, "prompt": prompt}
        if size and size != "auto":
            data["size"] = size
        if quality and quality != "auto":
            data["quality"] = quality

        url = f"{self.base_url}/images/edits"
        _log(f"edit url={url} model={self.model} size={size} quality={quality} "
             f"refs={len(images)} mode={mode} prompt_len={len(prompt)}")
        t0 = time.time()
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                files=files,
                data=data,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise RuntimeError(
                f"could not reach image API at {url} — {type(e).__name__}: {e}"
            )
        dt = time.time() - t0
        _log(f"edit -> HTTP {resp.status_code} in {dt:.1f}s")

        if resp.status_code >= 400:
            raise RuntimeError(
                f"image edit failed [HTTP {resp.status_code}] @ {url} "
                f"response: {resp.text[:600]}"
            )
        try:
            out = resp.json()
        except ValueError:
            raise RuntimeError(
                f"image edit returned non-JSON: {resp.text[:400]}"
            )
        if not out.get("data") or not out["data"][0].get("b64_json"):
            raise RuntimeError(
                f"image edit returned no b64_json: {json.dumps(out)[:400]}"
            )
        return base64.b64decode(out["data"][0]["b64_json"])
