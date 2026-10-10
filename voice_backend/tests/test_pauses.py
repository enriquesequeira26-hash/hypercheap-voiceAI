import math
from array import array

import pytest

from app.agent.pauses import (
    CLAUSE_PAUSE_MS,
    MAX_INNER_PAUSE_MS,
    SENTENCE_PAUSE_MS,
    SegmentTrimmer,
    pause_after_ms,
    silence,
)
from app.agent.session import AgentSession

RATE = 8000


def tone(ms: int, amp: int = 6000) -> bytes:
    n = RATE * ms // 1000
    return array("h", (int(amp * math.sin(2 * math.pi * 220 * i / RATE)) or 1 for i in range(n))).tobytes()


def quiet(ms: int) -> bytes:
    return silence(ms, RATE)


def ms_of(pcm: bytes) -> float:
    return len(pcm) / 2 / RATE * 1000


def longest_silence_ms(pcm: bytes) -> float:
    samples = array("h")
    samples.frombytes(pcm)
    best = run = 0
    for s in samples:
        run = run + 1 if abs(s) < 5 else 0
        best = max(best, run)
    return best / RATE * 1000


def trim(pcm: bytes, step: int = 1234) -> bytes:
    t = SegmentTrimmer(RATE)
    out = b"".join(t.feed(pcm[i : i + step]) for i in range(0, len(pcm), step))
    return out + t.finish()


def test_pause_depends_on_how_the_text_ends():
    assert pause_after_ms("Con gusto le ayudo.") == SENTENCE_PAUSE_MS
    assert pause_after_ms("¿Qué carro es?") == SENTENCE_PAUSE_MS
    assert pause_after_ms("Dijo “claro que sí.” ") == SENTENCE_PAUSE_MS
    assert pause_after_ms("Mire, le cuento,") == CLAUSE_PAUSE_MS
    assert pause_after_ms("") == CLAUSE_PAUSE_MS


def test_silence_around_a_segment_is_removed():
    out = trim(quiet(100) + tone(300) + quiet(900))
    assert 300 <= ms_of(out) <= 300 + 20 + 40 + 10


def test_pauses_inside_a_segment_are_kept():
    out = trim(quiet(50) + tone(300) + quiet(270) + tone(200) + quiet(900))
    assert 265 <= longest_silence_ms(out) <= 275
    assert 770 <= ms_of(out) <= 770 + 20 + 40 + 10


def test_a_pause_that_runs_too_long_is_shortened():
    out = trim(tone(300) + quiet(650) + tone(200) + quiet(900))
    assert MAX_INNER_PAUSE_MS - 5 <= longest_silence_ms(out) <= MAX_INNER_PAUSE_MS + 5
    assert 300 + MAX_INNER_PAUSE_MS + 200 <= ms_of(out) <= 300 + MAX_INNER_PAUSE_MS + 200 + 50


def test_chunking_does_not_change_the_result():
    pcm = quiet(137) + tone(333) + quiet(211) + tone(123) + quiet(680) + tone(90) + quiet(777)
    assert trim(pcm, 100) == trim(pcm, 4096) == trim(pcm, len(pcm))


def test_only_silence_gives_nothing():
    assert trim(quiet(500)) == b""


def test_audio_that_ends_on_sound_is_kept_whole():
    pcm = tone(205)
    assert trim(pcm, 300) == pcm


class _LLM:
    last_finish_reason = "stop"

    def __init__(self, text: str) -> None:
        self.text = text

    async def stream_reply(self, user_text, history=None):
        for i in range(0, len(self.text), 5):
            yield self.text[i : i + 5]


class _TTS:
    sample_rate = RATE

    def __init__(self) -> None:
        self.requests: list[str] = []

    async def synthesize(self, text):
        self.requests.append(text)
        # Like the real voice: a little silence first and close to a second after the last word
        audio = quiet(50) + tone(400) + quiet(900)
        for i in range(0, len(audio), 3000):
            yield audio[i : i + 3000]

    async def close(self):
        pass


async def _say(text: str):
    tts = _TTS()
    session = AgentSession(None, _LLM(text), tts)
    heard: list[bytes] = []

    async def on_audio(pcm: bytes) -> None:
        heard.append(pcm)

    session._on_audio_chunk = on_audio
    await session._generate_and_stream("hola")
    return tts.requests, b"".join(heard)


@pytest.mark.asyncio
async def test_reply_is_spoken_as_opening_sentence_plus_the_rest():
    text = "Buenas, le habla Enrique de Gonher. Con gusto le ayudo con las dos cosas. Para empezar, ¿qué carro es?"
    requests, _ = await _say(text)
    assert requests == [
        "Buenas, le habla Enrique de Gonher.",
        "Con gusto le ayudo con las dos cosas. Para empezar, ¿qué carro es?",
    ]


@pytest.mark.asyncio
async def test_join_between_segments_is_a_natural_pause():
    text = "Buenas, le habla Enrique de Gonher. Con gusto le ayudo con las dos cosas. Para empezar, ¿qué carro es?"
    _, audio = await _say(text)
    # 40 ms kept after the last word + the pause + 20 ms kept before the next word (was about 950 ms)
    assert SENTENCE_PAUSE_MS + 50 <= longest_silence_ms(audio) <= SENTENCE_PAUSE_MS + 75
    assert ms_of(audio) < 2 * 400 + SENTENCE_PAUSE_MS + 150


@pytest.mark.asyncio
async def test_a_single_sentence_is_one_request_without_padding():
    requests, audio = await _say("Con mucho gusto, don Carlos.")
    assert requests == ["Con mucho gusto, don Carlos."]
    assert 400 <= ms_of(audio) <= 470


@pytest.mark.asyncio
async def test_a_very_long_reply_is_split_at_a_sentence_end():
    sentence = "Este aceite protege el motor contra el desgaste y lo mantiene limpio por más tiempo. "
    requests, _ = await _say("Claro que sí, con mucho gusto. " + sentence * 6)
    assert requests[0] == "Claro que sí, con mucho gusto."
    assert len(requests) >= 3
    assert all(len(r) <= AgentSession._MAX_SEG and r.endswith(".") for r in requests)
    assert " ".join(requests) == ("Claro que sí, con mucho gusto. " + sentence * 6).strip()
