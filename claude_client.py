"""Anthropic Claude client routed through the derouter proxy.

Used for:
  - generating image prompts from an uploaded reference video (vision)
  - generating a script / shot list from a brief
  - planning a video edit from generated frames + audio (vision)
  - analysing a single scene image

All Claude features (vision, streaming, tools) are available via the official
SDK — we just point ``base_url`` at the derouter proxy.
"""
import base64
import json
import re
import sys
from typing import List, Optional

import anthropic
from anthropic import (
    APIError,
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
)
import requests

import config


def _log(msg):
    print(f"[claude] {msg}", file=sys.stderr, flush=True)


class ClaudeClient:
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        self.api_key = api_key or config.CLAUDE_API_KEY
        self.base_url = (base_url or config.CLAUDE_BASE_URL).rstrip("/")
        self.model = model or config.CLAUDE_MODEL
        self._sdk = anthropic.Anthropic(
            api_key=self.api_key or "unset",
            base_url=self.base_url,
        )

    def _require_key(self):
        if not self.api_key:
            raise RuntimeError(
                "No Claude API key set. Add CLAUDE_API_KEY to your .env or paste a "
                "key in the Settings panel."
            )

    # ------------------------------------------------------------------ #
    #  Connectivity check
    # ------------------------------------------------------------------ #
    def ping(self):
        """Real auth check via a 1-token messages.create.

        derouter serves ``GET /v1/models`` WITHOUT auth, so a models list alone
        reports "ok" even when the key is invalid. We do a tiny round-trip
        through the Messages API instead — the only thing that proves the key
        works. The (unauthenticated) models list is still fetched for the UI.
        """
        if not self.api_key:
            return {"ok": False, "error": "no api key set"}

        ids = self._list_models_best_effort()

        try:
            self._sdk.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
        except AuthenticationError as e:
            return {"ok": False, "error": f"auth rejected — {self._format_err(e)}",
                    "models": ids, "base_url": self.base_url}
        except (APIConnectionError, APITimeoutError) as e:
            return {"ok": False, "error": f"connection failed — {self._format_err(e)}",
                    "base_url": self.base_url}
        except APIError as e:
            return {"ok": False, "error": f"API error — {self._format_err(e)}",
                    "models": ids, "base_url": self.base_url}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}",
                    "base_url": self.base_url}

        return {"ok": True, "models": ids, "configured_model": self.model,
                "base_url": self.base_url}

    def _list_models_best_effort(self):
        """Fetch the model id list for the UI; never raises."""
        try:
            r = requests.get(
                f"{self.base_url}/v1/models",
                headers={"x-api-key": self.api_key,
                         "anthropic-version": "2023-06-01"},
                timeout=20,
            )
            if r.status_code < 400:
                data = r.json()
                return [m.get("id") for m in (data.get("data") or [])][:30]
        except Exception:
            pass
        return []

    @staticmethod
    def _format_err(e):
        klass = type(e).__name__
        msg = str(e) or "no message"
        body = None
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
    #  Low-level
    # ------------------------------------------------------------------ #
    def _msg(self, content_blocks, system: Optional[str] = None, max_tokens: int = 4096,
             stream: bool = False):
        self._require_key()
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content_blocks}],
        }
        if system:
            kwargs["system"] = system

        # IMPORTANT: default to a NON-streaming messages.create.
        #
        # The derouter proxy does not emit a spec-compliant SSE stream for many
        # calls (especially vision): the Anthropic SDK's streaming helper then
        # finishes with no final-message snapshot and raises a bare
        # AssertionError (empty message), which surfaced to the user as
        # "analysis failed:" / "edit planning failed:" with nothing after the
        # colon. The plain messages.create path works fine against derouter, so
        # we use it for everything by default.
        #
        # Only very long *text* generations (the full paced script) risk idling
        # past the proxy's ~100s edge timeout; those opt into streaming via
        # stream=True and we fall back to non-streaming if the stream yields no
        # message.
        mode = "messages.stream" if stream else "messages.create"
        _log(f"{mode} model={self.model} max_tokens={max_tokens} "
             f"blocks={len(content_blocks)} base={self.base_url}")
        try:
            if stream:
                try:
                    with self._sdk.messages.stream(**kwargs) as s:
                        resp = s.get_final_message()
                except (AssertionError, AttributeError):
                    # Proxy gave us a non-standard stream — retry without it.
                    _log("stream produced no final message; retrying non-streamed")
                    resp = self._sdk.messages.create(**kwargs)
            else:
                resp = self._sdk.messages.create(**kwargs)
        except (APIConnectionError, APITimeoutError) as e:
            raise RuntimeError(
                f"could not reach Claude API at {self.base_url} — {self._format_err(e)}"
            )
        except AuthenticationError as e:
            raise RuntimeError(
                f"Claude API rejected the key — {self._format_err(e)}"
            )
        except APIError as e:
            raise RuntimeError(f"Claude API error — {self._format_err(e)}")

        # Collect text from all text blocks.
        out = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                out.append(block.text)
        text = "\n".join(out).strip()
        # If the model ran out of room, the JSON is truncated → parsing fails
        # downstream with a confusing error. Surface the real cause instead.
        if getattr(resp, "stop_reason", None) == "max_tokens":
            _log(f"WARNING stop_reason=max_tokens (output hit the {max_tokens} "
                 f"cap; response likely truncated)")
        return text

    @staticmethod
    def _sniff_media_type(b: bytes) -> str:
        """Detect the real image format from its magic bytes.

        Vision calls were failing with HTTP 400 from derouter because the
        downsizer re-encodes frames as JPEG but every block was hardcoded as
        ``image/png`` — Anthropic rejects a media_type that doesn't match the
        actual bytes. Sniff it instead of trusting a fixed label.
        """
        if b[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            return "image/webp"
        if b[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        return "image/png"

    @classmethod
    def _image_block(cls, image_bytes: bytes, media_type: str = None):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type or cls._sniff_media_type(image_bytes),
                "data": base64.standard_b64encode(image_bytes).decode("ascii"),
            },
        }

    # ------------------------------------------------------------------ #
    #  Public helpers
    # ------------------------------------------------------------------ #
    def chat_text(self, prompt: str, system: Optional[str] = None, max_tokens: int = 4096,
                  stream: bool = False):
        """Plain text round-trip."""
        return self._msg([{"type": "text", "text": prompt}], system=system,
                         max_tokens=max_tokens, stream=stream)

    def vision_describe(self, images: List[bytes], instruction: str,
                        system: Optional[str] = None, max_tokens: int = 4096):
        """Send images + instruction, return text."""
        blocks = []
        for img in images[:20]:  # cap to keep token usage sane
            blocks.append(self._image_block(img))
        blocks.append({"type": "text", "text": instruction})
        return self._msg(blocks, system=system, max_tokens=max_tokens)

    # ------------------------------------------------------------------ #
    #  Higher-level tasks
    # ------------------------------------------------------------------ #
    def generate_script(self, title: str = "", description: str = "",
                        total_duration: float = 60.0, pacing_seconds: float = 1.0,
                        num_characters: int = 0, style_notes: str = "",
                        master_prompt: str = "", brief: str = ""):
        """Title + description -> full VO script + paced scene list + packed
        per-character sheet prompts.

        ``pacing_seconds`` is how long each image is on screen, so the number of
        scenes (= number of images) is round(total_duration / pacing_seconds).
        The VO is written to be readable in roughly ``total_duration`` seconds.

        Returns the raw model text (STRICT JSON). Caller runs extract_json().
        """
        # Back-compat: an old-style call may pass only `brief`.
        if brief and not description:
            description = brief

        scene_count = max(1, min(120, round((total_duration or 1) /
                                            max(0.1, pacing_seconds or 1))))

        system = (
            "You are a film director, narrator and screenwriter for a "
            "continuity-driven visual series produced as a sequence of still "
            "images shown in time with a voice-over. Return STRICT JSON ONLY "
            "(no prose, no markdown fences) shaped EXACTLY as:\n"
            "{\n"
            '  "title": str,\n'
            '  "logline": str,\n'
            '  "voiceover": str,            // the full narration script, plain text\n'
            '  "pacing_seconds": number,    // seconds each image is on screen\n'
            '  "total_duration": number,    // seconds (approx VO read length)\n'
            '  "scene_count": int,\n'
            '  "characters": [ { "name": str, "sheet_prompt": str } ],\n'
            '  "scenes": [ { "n": int, "heading": str, "action": str, "vo": str, "prompt": str } ]\n'
            "}\n"
            "RULES:\n"
            f"- Produce EXACTLY {scene_count} scenes (one image per scene). The "
            f"narration must read aloud in about {total_duration:.0f} seconds at a "
            "natural pace.\n"
            '- Each scene "vo" is the slice of narration spoken while that image is '
            'on screen; concatenated in order they equal the full "voiceover".\n'
            '- Each scene "prompt" is a self-contained image-generation prompt '
            "(subject, framing, lighting, mood, palette). Name any recurring "
            "character by their exact short name so a reference sheet can be "
            "auto-attached.\n"
            '- Each character "sheet_prompt" is ONE rich paragraph describing that '
            "character's canonical look (face, hair, build, outfit, colors, vibe) — "
            "written so it can be sent straight to a character-sheet generator. Use "
            "the exact same names as in the scene prompts."
        )
        bits = []
        if title.strip():
            bits.append(f"TITLE:\n{title.strip()}")
        if description.strip():
            bits.append(f"DESCRIPTION / HOW IT SHOULD BE:\n{description.strip()}")
        bits.append(f"Target total duration: {total_duration:.0f} seconds")
        bits.append(f"Pacing: {pacing_seconds:g} second(s) per image "
                    f"=> EXACTLY {scene_count} scenes/images")
        if num_characters and num_characters > 0:
            bits.append(f"Number of distinct recurring characters to define: "
                        f"{num_characters}")
        else:
            bits.append("Define however many recurring characters the story needs.")
        if style_notes.strip():
            bits.append(f"STYLE NOTES:\n{style_notes.strip()}")
        if master_prompt.strip():
            bits.append(f"WORLD / STYLE BIBLE:\n{master_prompt.strip()}")
        # The script is the one long generation (up to 16k tokens) that can run
        # long enough to hit the proxy's edge timeout, so stream it — with an
        # automatic non-streamed fallback if the proxy's stream is malformed.
        return self.chat_text("\n\n".join(bits), system=system, max_tokens=16000,
                              stream=True)

    def prompts_from_video_frames(self, frames: List[bytes], count: int = 8,
                                  style_hint: str = "", master_prompt: str = ""):
        """Given sampled frames of a reference video, produce ``count`` image
        prompts that recreate that look as a continuous series."""
        system = (
            "You analyse a reference video (sampled as still frames) and write "
            "image-generation prompts that recreate its world, palette, lighting "
            "and cinematography across a continuous series. Return STRICT JSON only:\n"
            '{ "style_summary": str, "prompts": [str, ...] }\n'
            "Each prompt is a self-contained visual description (subject, framing, "
            "lighting, mood, palette) — no character names unless they obviously "
            "recur. Do NOT include any preamble."
        )
        instr_bits = [
            f"Write {count} image prompts that form a coherent series in the SAME "
            "world, style, palette and lighting language as these reference frames."
        ]
        if style_hint.strip():
            instr_bits.append(f"User style hint: {style_hint.strip()}")
        if master_prompt.strip():
            instr_bits.append(f"Existing world bible: {master_prompt.strip()}")
        return self.vision_describe(frames, "\n\n".join(instr_bits), system=system,
                                    max_tokens=6000)

    def analyse_scene(self, image: bytes, question: str = ""):
        instr = question.strip() or (
            "Describe this scene as a cinematographer would: subject, framing, "
            "lighting, palette, mood, lens, and what's happening. Be concise (4-6 "
            "lines)."
        )
        return self.vision_describe([image], instr,
                                    system="You are an expert cinematographer.",
                                    max_tokens=1200)

    def plan_edit(self, frames: List[bytes], audio_duration: float,
                  user_brief: str = "", master_prompt: str = ""):
        """Look at every frame + know the audio length, produce an EDL.

        Returns STRICT JSON of the form:
          { "total_duration": float,
            "transition": "cut"|"fade"|"crossfade",
            "shots": [ { "index": int (1-based into frames list),
                         "duration": float (seconds),
                         "note": str } ],
            "rationale": str }
        """
        system = (
            "You are a film editor. You receive a sequence of generated frames "
            "(in order, 1-indexed) and a target audio duration. Decide how long "
            "each frame should be on screen, in what order, and what transition "
            "style to use, so the final cut feels intentional and fits the audio. "
            "Return STRICT JSON ONLY (no markdown):\n"
            '{ "total_duration": float, "transition": "cut"|"fade"|"crossfade", '
            '"shots": [{"index": int, "duration": float, "note": str}], '
            '"rationale": str }\n'
            "Rules: index is the 1-based position of the frame in the list as "
            "given. You may repeat or omit frames. Sum of durations should equal "
            "total_duration (= audio length). Keep individual shots between 0.4 "
            "and 8 seconds unless the brief says otherwise."
        )
        instr = [
            f"Audio duration: {audio_duration:.2f} seconds.",
            f"Frames provided: {min(len(frames), 20)} (in display order, labelled "
            f"1..{min(len(frames), 20)}). Only these indices are valid.",
        ]
        if user_brief.strip():
            instr.append(f"USER EDIT DIRECTION:\n{user_brief.strip()}")
        if master_prompt.strip():
            instr.append(f"WORLD / STYLE BIBLE:\n{master_prompt.strip()}")
        instr.append("Now design the edit. JSON only.")
        return self.vision_describe(frames, "\n\n".join(instr), system=system,
                                    max_tokens=4000)


def extract_json(text: str):
    """Pull the first JSON object out of a possibly-fenced Claude response."""
    if not text:
        raise ValueError("empty response")
    # Strip code fences if present.
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # Try direct parse first.
    try:
        return json.loads(t)
    except Exception:
        pass
    # Fall back: greedy find of {...}.
    m = re.search(r"\{[\s\S]*\}", t)
    if not m:
        raise ValueError(f"no JSON found in response: {text[:300]}")
    return json.loads(m.group(0))
