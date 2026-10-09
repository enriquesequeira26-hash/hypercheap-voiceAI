import json

import httpx
import pytest

from app.agent.inworld_tts import InworldTTS
from app.agent.pronunciation import Pronouncer, parse_pronunciations


def test_diesel_is_respelled_by_default():
    p = Pronouncer()
    assert p.apply("¿Es gasolina o diésel?") == "¿Es gasolina o dísel?"
    assert p.apply("Motor diesel, aceite para DIESEL.") == "Motor dísel, aceite para Dísel."
    assert p.apply("Diésel pesado") == "Dísel pesado"


def test_only_whole_words_are_replaced():
    p = Pronouncer("con=kon")
    assert p.apply("Con gusto, le confirmo el contacto.") == "Kon gusto, le confirmo el contacto."
    assert Pronouncer().apply("biodiesel") == "biodiesel"


def test_extra_entries_and_phrases():
    p = Pronouncer("Gonher = Gonér; Heavy Duty=jevi diuti\nheavy=jevi")
    assert p.apply("Gonher Heavy  Duty para diésel") == "Gonér Jevi diuti para dísel"
    assert p.apply("gonher heavy") == "Gonér jevi"


def test_empty_sound_removes_a_builtin():
    p = Pronouncer("diesel=; diésel=")
    assert p.apply("diesel y diésel") == "diesel y diésel"


def test_parse_ignores_noise():
    assert parse_pronunciations(" ; sin igual ;a=b;;A = c ") == {"a": "c"}
    assert parse_pronunciations(None) == {}


def test_text_without_matches_is_untouched():
    text = "Claro que sí, ¿qué vehículo tiene?"
    assert Pronouncer().apply(text) == text


@pytest.mark.asyncio
async def test_tts_sends_respelled_text_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, text="")

    tts = InworldTTS("key", pronunciations="Gonher=Gonér")
    await tts._client.aclose()
    tts._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    chunks = [c async for c in tts.synthesize("Gonher para motor diésel.")]
    await tts.close()
    assert chunks == []
    assert seen["text"] == "Gonér para motor dísel."
