from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from app.agent.llm_client import BasetenChat, reasoning_options
from app.agent.session import AgentSession


def _chunk(content=None, finish=None, reasoning=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


class _FakeCompletions:
    def __init__(self, chunks, reject_reasoning=False):
        self.chunks = chunks
        self.reject_reasoning = reject_reasoning
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_reasoning and "extra_body" in kwargs:
            response = httpx.Response(400, request=httpx.Request("POST", "https://llm.test/v1/chat/completions"))
            raise BadRequestError("unknown parameter", response=response, body=None)

        async def gen():
            for c in self.chunks:
                yield c

        return gen()


def _chat(chunks, **kwargs):
    chat = BasetenChat("key", "https://llm.test/v1", "model", **kwargs)
    fake = _FakeCompletions(chunks, reject_reasoning=kwargs.pop("_reject", False))
    chat.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return chat, fake


def test_reasoning_options():
    assert reasoning_options("None ") == {"extra_body": {"reasoning_effort": "none"}}
    assert reasoning_options("") == {}
    assert reasoning_options(None) == {}


@pytest.mark.asyncio
async def test_reply_asks_for_a_direct_answer_with_room_to_finish():
    chat, fake = _chat([_chunk("Hola, "), _chunk("¿cómo está?"), _chunk(None, finish="stop")])
    text = "".join([t async for t in chat.stream_reply("buenas")])
    assert text == "Hola, ¿cómo está?"
    assert chat.last_finish_reason == "stop"
    assert fake.calls[0]["extra_body"] == {"reasoning_effort": "none"}
    assert fake.calls[0]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_hidden_reasoning_is_never_spoken_and_cut_is_reported():
    chat, _ = _chat([_chunk(None, reasoning="pensando..."), _chunk("Claro que"), _chunk(None, finish="length")])
    text = "".join([t async for t in chat.stream_reply("buenas")])
    assert text == "Claro que"
    assert chat.last_finish_reason == "length"


@pytest.mark.asyncio
async def test_model_that_rejects_reasoning_effort_still_answers():
    chat, fake = _chat([_chunk("Listo."), _chunk(None, finish="stop")])
    fake.reject_reasoning = True
    assert "".join([t async for t in chat.stream_reply("a")]) == "Listo."
    assert len(fake.calls) == 2 and "extra_body" not in fake.calls[1]
    assert "".join([t async for t in chat.stream_reply("b")]) == "Listo."
    assert len(fake.calls) == 3 and "extra_body" not in fake.calls[2]


@pytest.mark.asyncio
async def test_empty_effort_keeps_the_model_default():
    chat, fake = _chat([_chunk("Sí."), _chunk(None, finish="stop")], reasoning_effort="", max_tokens=900)
    assert "".join([t async for t in chat.stream_reply("a")]) == "Sí."
    assert "extra_body" not in fake.calls[0] and fake.calls[0]["max_tokens"] == 900


class _FakeLLM:
    def __init__(self, tokens, finish):
        self.tokens, self.finish = tokens, finish
        self.last_finish_reason = None

    async def stream_reply(self, user_text, history=None):
        self.last_finish_reason = None
        for tok in self.tokens:
            yield tok
        self.last_finish_reason = self.finish


class _FakeTTS:
    def __init__(self):
        self.spoken = []

    async def synthesize(self, text):
        self.spoken.append(text)
        yield b"\x00\x00"

    async def close(self):
        pass


CUT = (
    "Ah, diez W treinta. Para equipo pesado le puedo ofrecer el Gonher Multigrado mineral, que previene lodos. "
    "Si quiere algo superior, está el Gonher Select semisintético, también en die"
)


async def _say(text, finish):
    tts = _FakeTTS()
    session = AgentSession(None, _FakeLLM([text[i : i + 7] for i in range(0, len(text), 7)], finish), tts)
    await session._generate_and_stream("el diez W treinta")
    return tts.spoken, session._history


@pytest.mark.asyncio
async def test_cut_reply_never_speaks_half_a_sentence():
    spoken, history = await _say(CUT, "length")
    said = " ".join(spoken)
    assert said.endswith("que previene lodos.")
    assert "también en die" not in said
    assert history[-1] == {"role": "assistant", "content": said}


@pytest.mark.asyncio
async def test_cut_keeps_the_finished_sentences_of_the_last_part():
    text = "Claro que sí, don Carlos, con mucho gusto le ayudo ahora mismo. ¿Es gasolina? Entonces le recomiendo el"
    spoken, history = await _say(text, "length")
    assert " ".join(spoken).endswith("¿Es gasolina?")
    assert history[-1]["content"].endswith("¿Es gasolina?")


@pytest.mark.asyncio
async def test_complete_reply_is_spoken_whole():
    text = "Con gusto, don Carlos. ¿Me regala un número de WhatsApp para que un asesor le mande la cotización"
    spoken, history = await _say(text, "stop")
    assert " ".join(spoken) == text
    assert history[-1]["content"] == text


@pytest.mark.asyncio
async def test_a_single_cut_phrase_is_still_spoken():
    spoken, _ = await _say("Con mucho gusto le", "length")
    assert spoken == ["Con mucho gusto le"]
