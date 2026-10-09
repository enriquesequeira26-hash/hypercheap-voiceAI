"""
Pronunciation dictionary for the synthesized voice.

Some words are read wrong by the voice model (for example "diésel", which in Costa Rica is said "dísel").
The replacements here are applied only to the text sent to the TTS: the conversation history, the
transcripts and the Excel keep the normal spelling.

Extra entries come from TTS_PRONUNCIATIONS, written as "word=how it sounds" pairs separated by ";" or
line breaks, for example:

    Gonher=Gonér; Heavy Duty=jevi diuti

An entry with an empty right side ("diesel=") removes a built-in one.
"""

import re
from typing import Optional

# Built-in entries, respelled the way a Spanish reader would say them.
DEFAULTS: dict[str, str] = {
    "diesel": "dísel",
    "diésel": "dísel",
    "diesels": "dísel",
    "diésels": "dísel",
}


def parse_pronunciations(spec: Optional[str]) -> dict[str, str]:
    """Parses "a=b; c=d" (also one pair per line). Keys are lowercased; a later pair wins."""
    entries: dict[str, str] = {}
    for chunk in re.split(r"[;\n]+", spec or ""):
        if "=" not in chunk:
            continue
        word, _, sound = chunk.partition("=")
        word = " ".join(word.split()).lower()
        if word:
            entries[word] = " ".join(sound.split())
    return entries


class Pronouncer:
    def __init__(self, spec: Optional[str] = None) -> None:
        entries = dict(DEFAULTS)
        entries.update(parse_pronunciations(spec))
        self._entries = {word: sound for word, sound in entries.items() if sound}
        self._pattern: Optional[re.Pattern[str]] = None
        if self._entries:
            # Longest first, so "heavy duty" wins over "heavy". Any run of spaces matches a space in the entry.
            words = sorted(self._entries, key=len, reverse=True)
            alternatives = "|".join(r"\s+".join(re.escape(part) for part in word.split(" ")) for word in words)
            self._pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)

    @property
    def entries(self) -> dict[str, str]:
        return dict(self._entries)

    def apply(self, text: str) -> str:
        if not text or self._pattern is None:
            return text

        def swap(match: re.Match[str]) -> str:
            found = match.group(0)
            sound = self._entries.get(" ".join(found.split()).lower())
            if sound is None:
                return found
            if found[:1].isupper():
                sound = sound[:1].upper() + sound[1:]
            return sound

        return self._pattern.sub(swap, text)
