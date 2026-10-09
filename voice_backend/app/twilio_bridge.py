"""
Phone calls through Twilio Media Streams.

  POST /twilio/voice  TwiML webhook (inbound calls, and the answer URL of outbound calls). Signed by Twilio.
  WS   /ws/twilio     Bidirectional audio stream of one call (mu-law 8 kHz <-> the agent).
  POST /twilio/call   Places an outbound call. Requires CALL_API_KEY.
  GET  /llamar        Minimal page to place a test call.

Everything is disabled until TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER are set.
"""

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import sys
import time
from array import array
from typing import Optional
from urllib.parse import parse_qsl
from xml.sax.saxutils import quoteattr

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from .agent.fennec_ws import DEFAULT_VAD, FennecWSClient
from .agent.inworld_stt import InworldSTTClient
from .agent.inworld_tts import InworldTTS
from .agent.llm_client import BasetenChat
from .agent.session import AgentSession
from .config import settings

log = logging.getLogger("hypercheap.twilio")
router = APIRouter()

PHONE_RATE = 8000  # Twilio Media Streams: mu-law, 8 kHz, mono
ASR_RATE = 16000  # the phone audio is upsampled x2 for speech-to-text
TOKEN_TTL_S = 120
E164 = re.compile(r"\+[1-9]\d{7,14}")

# Hidden stage directions that make the agent speak first. The caller never hears these, only the reply.
INBOUND_CUE = (
    "(Indicación interna: un cliente acaba de llamar por teléfono a Gonher. Conteste con un saludo breve y alegre, "
    "preséntese como Enrique, el asistente virtual de ventas de Gonher, y pregunte en qué le puede ayudar.)"
)
OUTBOUND_CUE = (
    "(Indicación interna: usted acaba de llamar por teléfono a esta persona y ya contestó. Salude con calidez, "
    "preséntese como Enrique, el asistente virtual de ventas de Gonher, y pregunte si tiene un minuto para conversar.)"
)


# --- G.711 mu-law <-> 16-bit PCM (the stdlib audioop module was removed in Python 3.13) -------------------------


def _ulaw_to_linear(u: int) -> int:
    u = ~u & 0xFF
    t = (((u & 0x0F) << 3) + 0x84) << ((u & 0x70) >> 4)
    return (0x84 - t) if (u & 0x80) else (t - 0x84)


_SEG_END = (0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF, 0x1FFF)


def _linear_to_ulaw(sample: int) -> int:
    val, mask = sample >> 2, 0xFF  # the reference G.711 encoder works on 14-bit samples
    if val < 0:
        val, mask = -val, 0x7F
    val = min(val, 8159) + 0x21
    for seg, end in enumerate(_SEG_END):
        if val <= end:
            return ((seg << 4) | ((val >> (seg + 1)) & 0x0F)) ^ mask
    return 0x7F ^ mask


_ULAW2LIN = [_ulaw_to_linear(i) for i in range(256)]
# Indexed by the sample read as an unsigned 16-bit value.
_LIN2ULAW = bytes(_linear_to_ulaw(i - 65536 if i >= 32768 else i) for i in range(65536))


def pcm16_to_ulaw(pcm_le16: bytes) -> bytes:
    """16-bit little-endian PCM -> mu-law, one byte per sample."""
    samples = array("H")
    samples.frombytes(pcm_le16[: len(pcm_le16) & ~1])
    if sys.byteorder == "big":
        samples.byteswap()
    return bytes(map(_LIN2ULAW.__getitem__, samples))


def ulaw_to_pcm16_x2(ulaw: bytes, prev: int = 0) -> tuple[bytes, int]:
    """mu-law 8 kHz -> 16-bit little-endian PCM at 16 kHz (linear interpolation). Returns (pcm, last sample)."""
    out = array("h")
    for b in ulaw:
        cur = _ULAW2LIN[b]
        out.append((prev + cur) >> 1)
        out.append(cur)
        prev = cur
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes(), prev


# --- Helpers ------------------------------------------------------------------------------------------------------


def twilio_ready() -> bool:
    return bool(settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_phone_number)


def _base_url(request: Request) -> str:
    """Public https origin of this deployment, as Twilio reaches it."""
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"https://{host}"


def twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    """https://www.twilio.com/docs/usage/security#validating-requests"""
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def make_stream_token(now: Optional[float] = None) -> str:
    """Short-lived proof that a /ws/twilio connection comes from a call we just answered."""
    ts = str(int(now if now is not None else time.time()))
    mac = hmac.new(settings.twilio_auth_token.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{mac}"


def stream_token_ok(token: str, now: Optional[float] = None) -> bool:
    ts, _, mac = (token or "").partition(".")
    if not ts.isdigit() or not mac:
        return False
    expected = hmac.new(settings.twilio_auth_token.encode(), ts.encode(), hashlib.sha256).hexdigest()
    age = (now if now is not None else time.time()) - int(ts)
    return hmac.compare_digest(mac, expected) and -5 <= age <= TOKEN_TTL_S


def build_phone_agent() -> AgentSession:
    asr: FennecWSClient | InworldSTTClient
    if settings.asr_provider == "fennec":
        asr = FennecWSClient(api_key=settings.fennec_api_key, sample_rate=ASR_RATE, channels=1, vad=DEFAULT_VAD)
    else:
        asr = InworldSTTClient(
            api_key_basic_b64=settings.inworld_api_key,
            sample_rate=ASR_RATE,
            channels=1,
            language=settings.inworld_language or None,
            model_id=settings.inworld_stt_model_id,
            end_of_turn_confidence=settings.inworld_stt_eot_confidence,
            min_end_of_turn_silence_ms=settings.inworld_stt_min_silence_ms,
            max_turn_silence_ms=settings.inworld_stt_max_silence_ms,
        )
    llm = BasetenChat(
        api_key=settings.baseten_api_key,
        base_url=settings.baseten_base_url,
        model=settings.baseten_model,
        system_prompt=settings.agent_system_prompt or None,
    )
    tts = InworldTTS(
        api_key_basic_b64=settings.inworld_api_key,
        model_id=settings.inworld_model_id,
        voice_id=settings.inworld_voice_id,
        sample_rate_hz=PHONE_RATE,
        language=settings.inworld_language or None,
        instruction=settings.inworld_instruction or None,
    )
    return AgentSession(asr, llm, tts)


# --- TwiML webhook ------------------------------------------------------------------------------------------------


@router.post("/twilio/voice")
async def twilio_voice(request: Request):
    if not twilio_ready():
        return PlainTextResponse("Twilio no está configurado", status_code=503)

    form = dict(parse_qsl((await request.body()).decode("utf-8", "replace"), keep_blank_values=True))
    base = _base_url(request)
    url = base + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    expected = twilio_signature(url, form, settings.twilio_auth_token)
    if not hmac.compare_digest(expected, request.headers.get("x-twilio-signature", "")):
        log.warning("[twilio] rejected webhook with a bad signature (url=%s)", url)
        return PlainTextResponse("Firma inválida", status_code=403)

    direction = "outbound" if request.query_params.get("direction") == "outbound" else "inbound"
    ws_url = base.replace("https://", "wss://", 1) + "/ws/twilio"
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response><Connect>'
        f"<Stream url={quoteattr(ws_url)}>"
        f'<Parameter name="token" value={quoteattr(make_stream_token())}/>'
        f'<Parameter name="direction" value="{direction}"/>'
        "</Stream></Connect></Response>"
    )
    return Response(content=twiml, media_type="text/xml")


# --- Audio stream of one call -------------------------------------------------------------------------------------


@router.websocket("/ws/twilio")
async def ws_twilio(ws: WebSocket):
    await ws.accept()
    agent: Optional[AgentSession] = None
    stream_sid = ""
    send_lock = asyncio.Lock()
    pending_ulaw = bytearray()  # caller audio waiting to be batched
    prev_sample = 0
    odd_byte = b""

    async def send_json(obj: dict) -> None:
        async with send_lock:
            await ws.send_text(json.dumps(obj))

    async def on_tts_chunk(pcm: bytes) -> None:
        nonlocal odd_byte
        data = odd_byte + pcm
        odd_byte = data[-1:] if len(data) % 2 else b""
        ulaw = pcm16_to_ulaw(data)
        if ulaw and stream_sid:
            payload = base64.b64encode(ulaw).decode("ascii")
            await send_json({"event": "media", "streamSid": stream_sid, "media": {"payload": payload}})

    async def on_vad(evt: dict) -> None:
        # The caller started talking: drop whatever agent audio Twilio still has buffered (barge-in).
        if evt.get("type") == "utterance" and evt.get("phase") == "begin" and stream_sid:
            await send_json({"event": "clear", "streamSid": stream_sid})

    try:
        if not twilio_ready() or settings.missing_keys():
            log.error("[twilio] stream refused: Twilio or agent keys are not configured")
            return

        while True:
            msg = json.loads(await ws.receive_text())
            event = msg.get("event")

            if event == "start" and agent is None:
                start = msg.get("start") or {}
                params = start.get("customParameters") or {}
                if not stream_token_ok(params.get("token", "")):
                    log.warning("[twilio] stream refused: missing or expired token")
                    return
                stream_sid = start.get("streamSid") or msg.get("streamSid") or ""
                outbound = params.get("direction") == "outbound"
                log.info("[twilio] call connected (%s)", "outbound" if outbound else "inbound")
                agent = build_phone_agent()
                await agent.start(on_audio_chunk=on_tts_chunk, on_vad=on_vad)
                await agent.speak_first(OUTBOUND_CUE if outbound else INBOUND_CUE)

            elif event == "media" and agent is not None:
                payload = (msg.get("media") or {}).get("payload")
                if payload:
                    pending_ulaw += base64.b64decode(payload)
                    if len(pending_ulaw) >= 480:  # 60 ms, so speech-to-text gets fewer, larger frames
                        pcm, prev_sample = ulaw_to_pcm16_x2(bytes(pending_ulaw), prev_sample)
                        pending_ulaw.clear()
                        await agent.feed_pcm(pcm)

            elif event == "stop":
                log.info("[twilio] call ended")
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("[twilio] stream failed")
    finally:
        if agent is not None:
            with contextlib.suppress(Exception):
                await agent.close()
        with contextlib.suppress(Exception):
            await ws.close()


# --- Outbound calls -----------------------------------------------------------------------------------------------


class CallRequest(BaseModel):
    to: str
    key: str


@router.post("/twilio/call")
async def twilio_call(req: CallRequest, request: Request):
    if not twilio_ready() or not settings.call_api_key:
        return JSONResponse({"ok": False, "error": "Las llamadas no están configuradas."}, status_code=503)
    if not hmac.compare_digest(req.key.encode(), settings.call_api_key.encode()):
        return JSONResponse({"ok": False, "error": "Clave incorrecta."}, status_code=401)

    to = re.sub(r"[\s\-().]", "", req.to)
    if not E164.fullmatch(to):
        error = "Número inválido. Use formato internacional, por ejemplo +50688887777."
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    allowed = {n.strip() for n in settings.twilio_allowed_numbers.split(",") if n.strip()}
    if allowed and to not in allowed:
        return JSONResponse({"ok": False, "error": "Ese número no está en la lista permitida."}, status_code=403)

    sid = settings.twilio_account_sid
    data = {
        "To": to,
        "From": settings.twilio_phone_number,
        "Url": f"{_base_url(request)}/twilio/voice?direction=outbound",
        "Method": "POST",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                auth=(sid, settings.twilio_auth_token),
                data=data,
            )
    except httpx.HTTPError as e:
        log.error("[twilio] could not reach the Twilio API: %s", e)
        return JSONResponse({"ok": False, "error": "No se pudo contactar a Twilio."}, status_code=502)

    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400:
        log.error("[twilio] call rejected: HTTP %s %s", resp.status_code, body.get("message"))
        error = body.get("message") or f"Twilio respondió {resp.status_code}."
        return JSONResponse({"ok": False, "error": error}, status_code=502)
    return {"ok": True, "sid": body.get("sid"), "status": body.get("status")}


CALL_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Llamar con el agente</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f1115;color:#e8eaed;display:grid;place-items:center;min-height:100vh;margin:0}
form{background:#181b22;padding:28px;border-radius:14px;width:min(92vw,380px);display:grid;gap:14px}
h1{font-size:20px;margin:0}label{font-size:13px;color:#aab;display:grid;gap:6px}
input{padding:11px;border-radius:8px;border:1px solid #333a47;background:#0f1115;color:inherit;font-size:16px}
button{padding:12px;border:0;border-radius:8px;background:#d9232e;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
button:disabled{opacity:.6}#out{font-size:14px;min-height:20px}
</style></head><body>
<form id="f"><h1>Llamar con el agente</h1>
<label>Número (formato internacional)<input id="to" placeholder="+50688887777" required></label>
<label>Clave de llamadas<input id="key" type="password" required autocomplete="off"></label>
<button id="b">Llamar</button><div id="out"></div></form>
<script>
const f=document.getElementById('f'),out=document.getElementById('out'),b=document.getElementById('b');
f.addEventListener('submit',async e=>{e.preventDefault();b.disabled=true;out.textContent='Llamando…';
try{const r=await fetch('/twilio/call',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({to:document.getElementById('to').value,key:document.getElementById('key').value})});
const j=await r.json();out.textContent=j.ok?'Llamada en curso. Conteste su teléfono.':'Error: '+(j.error||r.status);}
catch(err){out.textContent='Error de red.';}b.disabled=false;});
</script></body></html>"""


@router.get("/llamar")
async def call_page():
    return HTMLResponse(CALL_PAGE)
