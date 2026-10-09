import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI, BadRequestError

logger = logging.getLogger("hypercheap.llm")

SYSTEM_PROMPT = """
Eres Sofía, una asistente de voz amable, cercana y muy concisa que conversa en español latinoamericano.
Responde siempre en español, con una o dos frases como máximo, en un tono natural y conversacional.
Tu respuesta se convertirá en audio: no uses emojis, listas, markdown ni símbolos, y escribe los números con palabras.
"""

OPTIONAL_AUDIO_MARKUP_PROMPT = """
Audio Markups: use at most one leading emotion/delivery tag—[happy],
[sad],[angry], [surprised], [fearful],[disgusted], [laughing],
or [whispering]—which applies to the rest of the sentence; if
multiple are given, use only the first. Allow inline non-verbal tags
anywhere: [breathe], [clear_throat], [cough], [laugh], [sigh], [yawn].
Use tags verbatim; do not invent new ones.
"""


def reasoning_options(reasoning_effort: Optional[str]) -> Dict[str, Any]:
    """
    Request options that set how much the model "thinks" before answering ("none", "high", ...).

    Reasoning models spend hidden tokens first, and those count against max_tokens: with the model's default
    effort a spoken reply can run out of budget and stop in the middle of a word. Empty = model default.
    """
    effort = (reasoning_effort or "").strip().lower()
    return {"extra_body": {"reasoning_effort": effort}} if effort else {}


class BasetenChat:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 512,
        reasoning_effort: Optional[str] = "none",
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.system_prompt = (system_prompt or SYSTEM_PROMPT).strip()
        self.max_tokens = max(64, int(max_tokens))
        self.reasoning_effort = (reasoning_effort or "").strip().lower()
        # "stop" when the last reply ended on its own, "length" when it ran out of max_tokens (cut short).
        self.last_finish_reason: Optional[str] = None
        self._current_stream = None

    async def cancel(self):
        """Cancels any in-flight streaming call."""
        s = getattr(self, "_current_stream", None)
        if not s:
            return
        # The openai client's stream object has a `close` method.
        for name in ("aclose", "close", "cancel", "stop"):
            fn = getattr(s, name, None)
            if fn:
                try:
                    result = fn()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
                break

    async def stream_reply(
        self,
        user_text: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[str]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": self.system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        params: Dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            stream=True,
            top_p=1,
            max_tokens=self.max_tokens,
            temperature=0.2,
            presence_penalty=0,
            frequency_penalty=0,
        )
        self.last_finish_reason = None
        try:
            stream = await self.client.chat.completions.create(**params, **reasoning_options(self.reasoning_effort))
        except BadRequestError as e:
            if not self.reasoning_effort:
                raise
            # This model does not take reasoning_effort: stop sending it for the rest of the session.
            logger.warning("[llm] reasoning_effort=%s rejected (%s); retrying without it", self.reasoning_effort, e)
            self.reasoning_effort = ""
            stream = await self.client.chat.completions.create(**params)

        self._current_stream = stream
        reasoning_chars = 0
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    self.last_finish_reason = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    continue
                reasoning_chars += len(getattr(delta, "reasoning_content", None) or "")
                if delta.content is not None:
                    yield delta.content
        finally:
            self._current_stream = None
            if self.last_finish_reason == "length":
                logger.warning(
                    "[llm] reply cut by max_tokens=%d (hidden reasoning: %d chars)", self.max_tokens, reasoning_chars
                )
            elif reasoning_chars:
                logger.info("[llm] finish=%s, hidden reasoning: %d chars", self.last_finish_reason, reasoning_chars)
