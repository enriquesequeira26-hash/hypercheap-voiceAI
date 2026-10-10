import asyncio
import base64
import contextlib
import json
import logging
from typing import AsyncIterator, Optional

import httpx

from .pronunciation import Pronouncer

logger = logging.getLogger("hypercheap.inworld")


class InworldTTS:
    def __init__(
        self,
        api_key_basic_b64: str,
        model_id: str = "inworld-tts-2-flash",
        voice_id: str = "Ashley",
        sample_rate_hz: int = 48000,
        language: Optional[str] = None,
        instruction: Optional[str] = None,
        pronunciations: Optional[str] = None,
    ) -> None:
        self._auth = f"Basic {api_key_basic_b64}"
        self._model = model_id
        self._voice = voice_id
        self._sr = sample_rate_hz
        self._language = language
        self._instruction = (instruction or "").strip() or None
        # Words the voice reads wrong, respelled only for the audio (see pronunciation.py)
        self._pronouncer = Pronouncer(pronunciations)
        self._url = "https://api.inworld.ai/tts/v1/voice:stream"
        self._active_resp: Optional[httpx.Response] = None
        self._stop_evt = asyncio.Event()

        # Reuse a single HTTP/2 client/connection for all segments in the session
        self._client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(20, read=120),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=30),
        )

    @property
    def sample_rate(self) -> int:
        """Rate of the PCM16 mono audio that synthesize() yields."""
        return self._sr

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def stop(self) -> None:
        """Cooperatively stops the current synthesizer stream and aborts the connection."""
        self._stop_evt.set()
        resp = self._active_resp
        if resp is not None:
            with contextlib.suppress(Exception):
                await resp.aclose()

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        if not text or not text.strip():
            return

        self._stop_evt.clear()

        # LINEAR16: every streamed chunk carries its own 44-byte WAV header (stripped below).
        payload: dict = {
            "text": self._pronouncer.apply(text),
            "voiceId": self._voice,
            "modelId": self._model,
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": self._sr,
            },
        }
        if self._language:
            payload["language"] = self._language
        if self._instruction:
            # Request-level tone steering for this segment (ignored by inworld-tts-2-flash)
            payload["instruction"] = self._instruction

        headers = {
            "Authorization": self._auth,
            "Content-Type": "application/json",
        }

        async with self._client.stream("POST", self._url, headers=headers, json=payload) as resp:
            self._active_resp = resp
            try:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if self._stop_evt.is_set():
                        break
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if obj.get("error"):
                            # Inworld can report errors as NDJSON lines after an HTTP 200
                            logger.error("[inworld] stream error: %s", obj["error"])
                            continue
                        data_b64 = (obj.get("result") or {}).get("audioContent")
                        if not data_b64:
                            continue
                        wav_bytes = base64.b64decode(data_b64)
                        if len(wav_bytes) > 44:
                            yield wav_bytes[44:]
                    except Exception as e:
                        logger.debug("[inworld] skip line parse err: %s", e)
            except httpx.HTTPStatusError as e:
                logger.error("[inworld] HTTP error: %s", e)
                try:
                    snippet = (await resp.aread())[:256]
                    if snippet:
                        logger.error("[inworld] error body: %r", snippet)
                except Exception:
                    pass
                return
            finally:
                self._active_resp = None

        await asyncio.sleep(0)
