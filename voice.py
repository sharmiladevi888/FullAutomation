"""ElevenLabs text-to-speech client.

Turns the Claude-written voice-over script into real spoken audio (MP3 bytes).
Used by the Edit tab to either (a) synthesize the whole voice-over into one
track that drops straight into the existing `audio` slot, or (b) synthesize one
clip per numbered scene so each image can be held for exactly its own narration.

Keys are resolved per-user from the request settings (see app.py); this client
just takes whatever key/voice/model it is handed.
"""
import requests

import config


class VoiceClient:
    def __init__(self, api_key=None, base_url=None, model=None, voice_id=None,
                 timeout=180):
        self.api_key = api_key or config.ELEVENLABS_API_KEY
        self.base_url = (base_url or config.ELEVENLABS_BASE_URL).rstrip("/")
        self.model = model or config.ELEVENLABS_MODEL
        self.voice_id = voice_id or config.ELEVENLABS_VOICE_ID
        self.timeout = timeout

    def _require_key(self):
        if not self.api_key:
            raise RuntimeError(
                "No ElevenLabs API key set. Add ELEVENLABS_API_KEY to your .env "
                "or paste a key in the Settings panel."
            )

    def ping(self):
        """Lightweight auth check used by the Settings 'Test connection' panel."""
        if not self.api_key:
            return {"ok": False, "detail": "no key"}
        try:
            r = requests.get(f"{self.base_url}/voices",
                             headers={"xi-api-key": self.api_key}, timeout=20)
            if r.status_code >= 400:
                return {"ok": False, "detail": f"[{r.status_code}] {r.text[:160]}"}
            n = len(r.json().get("voices", []))
            return {"ok": True, "detail": f"{n} voices available"}
        except Exception as e:
            return {"ok": False, "detail": str(e)}

    def list_voices(self):
        """Return [{id, name, category}] for the account's available voices."""
        self._require_key()
        r = requests.get(f"{self.base_url}/voices",
                         headers={"xi-api-key": self.api_key}, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"could not list voices [{r.status_code}]: {r.text[:300]}")
        out = []
        for v in r.json().get("voices", []):
            out.append({
                "id": v.get("voice_id"),
                "name": v.get("name") or v.get("voice_id"),
                "category": v.get("category", ""),
            })
        return out

    def generate_sfx(self, text, duration_seconds=None, prompt_influence=0.4):
        """Text -> sound effect via ElevenLabs Sound Generation. Returns MP3
        bytes. ``text`` is a short description ("deep cinematic boom", "lava
        crackle", "fast whoosh transition"). ``duration_seconds`` 0.5-22 (None =
        let ElevenLabs choose). Raises on failure."""
        self._require_key()
        desc = (text or "").strip()
        if not desc:
            raise RuntimeError("generate_sfx needs a description")
        body = {"text": desc,
                "prompt_influence": max(0.0, min(1.0, float(prompt_influence)))}
        if duration_seconds:
            body["duration_seconds"] = max(0.5, min(22.0, float(duration_seconds)))
        r = requests.post(
            f"{self.base_url}/sound-generation",
            headers={"xi-api-key": self.api_key,
                     "Content-Type": "application/json",
                     "Accept": "audio/mpeg"},
            json=body, timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"SFX gen failed [{r.status_code}]: {r.text[:400]}")
        if not r.content:
            raise RuntimeError("SFX gen returned empty audio")
        return r.content

    def synthesize(self, text, voice_id=None, model=None,
                   stability=0.5, similarity_boost=0.75, style=0.0):
        """Text -> spoken audio. Returns MP3 bytes. Raises on failure."""
        self._require_key()
        voice_id = voice_id or self.voice_id
        model = model or self.model
        speak = (text or "").strip() or " "  # ElevenLabs rejects empty text

        url = f"{self.base_url}/text-to-speech/{voice_id}"
        body = {
            "text": speak,
            "model_id": model,
            "voice_settings": {
                "stability": stability,
                "similarity_boost": similarity_boost,
                "style": style,
                "use_speaker_boost": True,
            },
        }
        r = requests.post(
            url,
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json=body,
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"TTS failed [{r.status_code}]: {r.text[:400]}")
        if not r.content:
            raise RuntimeError("TTS returned empty audio")
        return r.content
