# agent/inworld_stt.py

import asyncio
import base64
import contextlib
import json
import logging
from typing import Awaitable, Callable, Optional

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

logger = logging.getLogger("hypercheap.inworld_stt")


class InworldSTTClient:
    """
    Streaming speech-to-text over Inworld's bidirectional WebSocket.

    Drop-in replacement for FennecWSClient: same public API (start(), send_pcm(), stop(), close())
    and the same callbacks, so AgentSession does not need to know which ASR is behind it.

    Protocol (https://docs.inworld.ai/api-reference/sttAPI/speechtotext/transcribe-stream-websocket):
      1) Connect with `Authorization: Basic <base64 key>` (the same key used for Inworld TTS).
      2) First frame: {"transcribeConfig": {...}}. The server sends no acknowledgement.
      3) Audio: {"audioChunk": {"content": "<base64 PCM16>"}} as JSON text frames (not binary).
      4) Server: {"result": {"transcription": {"transcript", "isFinal"}}}, plus
         {"result": {"speechStarted": {...}}} / {"result": {"speechStopped": {...}}}.
      5) Finish: {"closeStream": {}}.

    Speech events are re-emitted in the shape the rest of the app already understands
    ({"type": "utterance", "phase": "begin" | "end"}) so barge-in keeps working.
    """

    def __init__(
        self,
        api_key_basic_b64: str,
        sample_rate: int = 16000,
        channels: int = 1,
        *,
        language: Optional[str] = "es",
        model_id: str = "inworld/inworld-stt-1",
        end_of_turn_confidence: float = 0.5,
        min_end_of_turn_silence_ms: int = 200,
        max_turn_silence_ms: int = 800,
        url: str = "wss://api.inworld.ai/stt/v1/transcribe:streamBidirectional",
    ) -> None:
        self._auth = f"Basic {api_key_basic_b64}"
        self._sr = sample_rate
        self._ch = channels
        self._language = language
        self._model = model_id
        self._eot_confidence = end_of_turn_confidence
        self._min_eot_silence_ms = min_end_of_turn_silence_ms
        self._max_turn_silence_ms = max_turn_silence_ms
        self._url = url

        self._ws = None
        self._recv_task: Optional[asyncio.Task] = None
        self._on_final: Optional[Callable[[str], Awaitable[None]]] = None
        self._on_vad: Optional[Callable[[dict], Awaitable[None]]] = None
        self._ready = asyncio.Event()
        self._closed = False

    def _config_message(self) -> dict:
        config: dict = {
            "modelId": self._model,
            "audioEncoding": "LINEAR16",
            "sampleRateHertz": self._sr,
            "numberOfChannels": self._ch,
            "endOfTurnConfidenceThreshold": self._eot_confidence,
            "inworldSttV1Config": {
                # The minimum is checked first, so it must not exceed the maximum.
                "minEndOfTurnSilenceWhenConfident": min(self._min_eot_silence_ms, self._max_turn_silence_ms),
                "maxTurnSilence": self._max_turn_silence_ms,
            },
        }
        if self._language:
            config["language"] = self._language
        return {"transcribeConfig": config}

    async def start(
        self,
        on_final: Callable[[str], Awaitable[None]],
        on_partial: Optional[Callable[[str], Awaitable[None]]] = None,  # reserved; interim results are ignored
        on_vad: Optional[Callable[[dict], Awaitable[None]]] = None,
    ):
        if self._ws is not None:
            return
        if not self._auth.removeprefix("Basic ").strip():
            raise RuntimeError("Inworld API key is required for speech-to-text.")

        self._on_final = on_final
        self._on_vad = on_vad

        logger.info("[inworld-stt] connect %s", self._url)
        self._ws = await ws_connect(
            self._url,
            additional_headers={"Authorization": self._auth},
            compression=None,
            max_size=None,
            open_timeout=15,
        )
        self._recv_task = asyncio.create_task(self._recv_loop(), name="inworld_stt_recv")

        await self._ws.send(json.dumps(self._config_message()))
        self._ready.set()
        logger.info("[inworld-stt] started (model=%s, language=%s)", self._model, self._language or "auto")

    async def send_pcm(self, pcm_le16: bytes) -> None:
        await self._ready.wait()
        if not self._ws or self._closed or not pcm_le16:
            return
        try:
            content = base64.b64encode(pcm_le16).decode("ascii")
            await self._ws.send(json.dumps({"audioChunk": {"content": content}}))
        except Exception as e:
            logger.warning("[inworld-stt] send error: %s", e)

    async def _emit_vad(self, evt: dict) -> None:
        if not self._on_vad:
            return
        try:
            await self._on_vad(evt)
        except Exception:
            logger.exception("[inworld-stt] on_vad raised")

    async def _recv_loop(self):
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if isinstance(msg, (bytes, bytearray)):
                    continue
                try:
                    data = json.loads(msg)
                except Exception:
                    continue

                if err := data.get("error"):
                    logger.error("[inworld-stt][error] %s", err)
                    continue

                result = data.get("result") or {}

                if "speechStarted" in result:
                    await self._emit_vad({"type": "utterance", "phase": "begin"})
                    continue
                if "speechStopped" in result:
                    await self._emit_vad({"type": "utterance", "phase": "end"})
                    continue

                transcription = result.get("transcription")
                if not transcription or not transcription.get("isFinal"):
                    continue  # interim results replace each other; only finals are committed

                text = (transcription.get("transcript") or "").strip()
                if text and self._on_final:
                    try:
                        await self._on_final(text)
                    except Exception:
                        logger.exception("[inworld-stt] on_final raised")

        except ConnectionClosed as e:
            logger.info("[inworld-stt] connection closed by server (code=%s)", getattr(e, "code", "?"))
        except Exception as e:
            logger.warning("[inworld-stt] recv error: %s", e)

    async def stop(self):
        if self._closed:
            return
        self._closed = True

        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"closeStream": {}}))
            with contextlib.suppress(Exception):
                await self._ws.close()

        if self._recv_task:
            try:
                await asyncio.wait_for(self._recv_task, timeout=1.5)
            except Exception:
                self._recv_task.cancel()

        self._ws = None
        self._recv_task = None
        self._on_final = None
        self._on_vad = None
        self._ready = asyncio.Event()
        logger.info("[inworld-stt] stopped")

    async def close(self):
        await self.stop()
