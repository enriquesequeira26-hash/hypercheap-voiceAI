"""
Natural pauses where two synthesized segments meet.

A reply is spoken as a few TTS requests so the first words come out fast. Each request returns its audio
padded with silence (measured: close to a second after a sentence), so at every join the listener heard a
pause three or four times longer than the pause the same voice makes between two sentences of one request.

SegmentTrimmer removes the silence around a segment (pauses inside it are untouched) and the session puts a
pause of a natural length between segments instead.
"""

from array import array

# Pause the voice itself makes between two sentences inside one request is about a quarter of a second.
SENTENCE_PAUSE_MS = 260
CLAUSE_PAUSE_MS = 120  # after a comma, a colon or a cut at a space

_SENTENCE_END = ".!?…"
_CLOSERS = "\"'”’»)]"


def pause_after_ms(text: str) -> int:
    """Length of the pause to leave after a segment, from how its text ends."""
    stripped = (text or "").rstrip().rstrip(_CLOSERS)
    return SENTENCE_PAUSE_MS if stripped and stripped[-1] in _SENTENCE_END else CLAUSE_PAUSE_MS


def silence(ms: int, sample_rate: int) -> bytes:
    """PCM16 mono silence."""
    return bytes(2 * (sample_rate * max(0, ms) // 1000))


class SegmentTrimmer:
    """
    Streams one segment of PCM16 mono audio through, dropping the silence before its first sound and after
    its last one. A little is kept on both sides so soft consonants and the decay of the last word survive.
    """

    def __init__(self, sample_rate: int, threshold: int = 90, lead_ms: int = 20, tail_ms: int = 40) -> None:
        self._win = 2 * max(1, sample_rate // 100)  # bytes in 10 ms
        self._threshold = threshold  # peak of a window that counts as sound (full scale is 32768)
        self._lead = max(1, lead_ms // 10)
        self._tail = max(1, tail_ms // 10)
        self._rest = b""  # bytes that do not fill a window yet
        self._before: list[bytes] = []  # last silent windows before the first sound
        self._held: list[bytes] = []  # silent windows after the last sound so far
        self._started = False

    def _loud(self, window: bytes) -> bool:
        samples = array("h")
        samples.frombytes(window)
        return max(samples) > self._threshold or min(samples) < -self._threshold

    def feed(self, pcm: bytes) -> bytes:
        data = self._rest + pcm
        usable = len(data) - len(data) % self._win
        self._rest = data[usable:]
        out: list[bytes] = []
        for i in range(0, usable, self._win):
            window = data[i : i + self._win]
            if self._loud(window):
                if not self._started:
                    self._started = True
                    out.extend(self._before)
                    self._before = []
                else:
                    out.extend(self._held)  # a pause inside the segment: keep it whole
                self._held = []
                out.append(window)
            elif self._started:
                self._held.append(window)
            else:
                self._before = (self._before + [window])[-self._lead :]
        return b"".join(out)

    def finish(self) -> bytes:
        """End of the segment: what is left of its audio, without the trailing silence."""
        if not self._started:
            return b""
        tail = self._held[: self._tail]
        if not self._held and len(self._rest) >= 2:
            tail = [self._rest[: len(self._rest) & ~1]]
        self._held, self._rest = [], b""
        return b"".join(tail)
