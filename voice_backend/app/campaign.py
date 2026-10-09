"""
Telesales campaign: a supervised dialer for the customers of the Excel workbook.

  GET  /campana                        Panel (asks for CALL_API_KEY in the page).
  GET  /campana/api/estado             What is configured, the customer base and the current batch.
  POST /campana/api/base               Upload the workbook (.xlsx body).
  POST /campana/api/lote               Choose the next customers to call.
  POST /campana/api/llamar             Call one customer of the batch.
  GET  /campana/api/llamada/{cid}      Progress and result of the last call to a customer.
  GET  /campana/api/excel              The workbook with the results filled in.
  GET  /campana/api/grabacion/{sid}    Audio of a recorded call.

Every /campana/api route requires the header x-call-key = CALL_API_KEY.
"""

import asyncio
import logging
import re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from . import campaign_data as cd
from . import store
from .campaign_page import PAGE
from .config import settings
from .twilio_bridge import E164, _base_url, key_ok, place_call, twilio_ready

log = logging.getLogger("hypercheap.campaign")
router = APIRouter()

MAX_BATCH = 50
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def require_key(x_call_key: str = Header(default="")) -> None:
    if not settings.call_api_key:
        raise HTTPException(503, "Falta definir CALL_API_KEY en Vercel.")
    if not key_ok(x_call_key):
        raise HTTPException(401, "Clave incorrecta.")


def require_store() -> None:
    if not store.ready():
        raise HTTPException(503, "Falta conectar el almacenamiento (Vercel Blob) al proyecto.")


api = APIRouter(prefix="/campana/api", dependencies=[Depends(require_key)])


@router.get("/campana")
async def panel():
    return HTMLResponse(PAGE)


def _row(client: dict, result: Optional[dict]) -> dict:
    result = result or {}
    return {
        "id": client["id"],
        "nombre": client["nombre"],
        "poblacion": client["poblacion"],
        "segmento": client["segmento"][:2],
        "telefono": client["telefono"],
        "fase": result.get("fase", "pendiente"),
        "estado_gestion": result.get("estado_gestion", ""),
        "resultado": result.get("resultado", ""),
        "resumen": result.get("resumen", ""),
        "duracion_s": result.get("duracion_s", 0),
    }


async def _state() -> dict:
    state: dict = {
        "almacenamiento": store.ready(),
        "twilio": twilio_ready(),
        "en_horario": cd.within_call_hours(),
        "horario": settings.call_hours,
        "grabacion": settings.call_recording,
        "base": None,
        "lote": [],
    }
    if not store.ready():
        return state
    clients = await cd.load_clients()
    if not clients:
        return state
    state["base"] = {
        "total": len(clients),
        "con_telefono": sum(1 for c in clients if c["telefono"]),
        "pendientes": len(cd.pick_batch(clients, "", len(clients), set())),
    }
    batch = await store.get_json("campaign/batch.json", default={}) or {}
    by_id = {c["id"]: c for c in clients}
    ids = [cid for cid in batch.get("ids", []) if cid in by_id]
    results = await asyncio.gather(*(store.get_json(f"campaign/results/{cid}.json") for cid in ids))
    state["lote"] = [_row(by_id[cid], res) for cid, res in zip(ids, results)]
    return state


@api.get("/estado")
async def estado():
    try:
        return await _state()
    except store.StoreError as e:
        raise HTTPException(502, str(e)) from e


@api.post("/base", dependencies=[Depends(require_store)])
async def subir_base(request: Request):
    data = await request.body()
    if not data:
        raise HTTPException(400, "No llegó ningún archivo.")
    try:
        clients = cd.parse_workbook(data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    try:
        await store.put_bytes("campaign/base.xlsx", data, XLSX)
        await store.put_json("campaign/clients.json", clients)
        return await _state()
    except store.StoreError as e:
        raise HTTPException(502, str(e)) from e


class BatchRequest(BaseModel):
    segmento: str = "P1"
    cantidad: int = 20


@api.post("/lote", dependencies=[Depends(require_store)])
async def crear_lote(req: BatchRequest):
    try:
        clients = await cd.load_clients()
        if not clients:
            raise HTTPException(400, "Primero suba el Excel con la base de clientes.")
        prefix = "campaign/results/"
        done = {p[len(prefix) : -len(".json")] for p in await store.list_paths(prefix) if p.endswith(".json")}
        size = max(1, min(int(req.cantidad), MAX_BATCH))
        ids = cd.pick_batch(clients, req.segmento.strip(), size, done)
        if not ids:
            raise HTTPException(400, "No quedan clientes pendientes con teléfono en ese segmento.")
        await store.put_json("campaign/batch.json", {"creado": cd.now_local().isoformat(), "ids": ids})
        return await _state()
    except store.StoreError as e:
        raise HTTPException(502, str(e)) from e


class DialRequest(BaseModel):
    id: str
    probar_con: str = ""  # call this number instead of the customer's (rehearsal)


@api.post("/llamar", dependencies=[Depends(require_store)])
async def llamar(req: DialRequest, request: Request):
    if not twilio_ready():
        raise HTTPException(503, "Twilio no está configurado en Vercel.")
    try:
        batch = await store.get_json("campaign/batch.json", default={}) or {}
        client = await cd.find_client(req.id)
        if not client or req.id not in batch.get("ids", []):
            raise HTTPException(404, "Ese cliente no está en el lote actual.")

        test_number = re.sub(r"[\s\-().]", "", req.probar_con)
        if test_number:
            if not E164.fullmatch(test_number):
                raise HTTPException(400, "Número de prueba inválido. Use formato internacional: +50688887777.")
            to = test_number
        else:
            if not cd.within_call_hours():
                raise HTTPException(409, f"Fuera del horario de llamadas ({settings.call_hours} h, lunes a sábado).")
            if not client["telefono"]:
                raise HTTPException(400, "Ese cliente no tiene teléfono.")
            to = cd.to_e164(client["telefono"])
        allowed = {n.strip() for n in settings.twilio_allowed_numbers.split(",") if n.strip()}
        if allowed and to not in allowed:
            raise HTTPException(403, "Ese número no está en la lista permitida (TWILIO_ALLOWED_NUMBERS).")

        ok, body = await place_call(to, _base_url(request), cid=client["id"])
        if not ok:
            raise HTTPException(502, body["message"])
        await cd.save_attempt(client["id"], body.get("sid", ""), to, rehearsal=bool(test_number))
        return {"ok": True, "callSid": body.get("sid"), "prueba": bool(test_number)}
    except store.StoreError as e:
        raise HTTPException(502, str(e)) from e


@api.get("/llamada/{cid}", dependencies=[Depends(require_store)])
async def llamada(cid: str, forzar: int = 0):
    try:
        return await cd.call_snapshot(cid, force=bool(forzar))
    except store.StoreError as e:
        raise HTTPException(502, str(e)) from e


@api.get("/excel", dependencies=[Depends(require_store)])
async def excel(request: Request):
    try:
        data = await store.get_bytes("campaign/base.xlsx")
        if not data:
            raise HTTPException(400, "Primero suba el Excel con la base de clientes.")
        prefix = "campaign/results/"
        paths = [p for p in await store.list_paths(prefix) if p.endswith(".json")]
        results = [r for r in await asyncio.gather(*(store.get_json(p) for p in paths)) if r]
    except store.StoreError as e:
        raise HTTPException(502, str(e)) from e
    filled = await asyncio.to_thread(cd.fill_workbook, data, results, f"{_base_url(request)}/campana")
    name = f"Plan Televentas {cd.now_local():%Y-%m-%d %H%M}.xlsx"
    return Response(filled, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@api.get("/grabacion/{sid}")
async def grabacion(sid: str):
    if not re.fullmatch(r"RE[0-9a-fA-F]{32}", sid):
        raise HTTPException(400, "Identificador de grabación inválido.")
    if not twilio_ready():
        raise HTTPException(503, "Twilio no está configurado en Vercel.")
    account = settings.twilio_account_sid
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account}/Recordings/{sid}.mp3"
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url, auth=(account, settings.twilio_auth_token))
    except httpx.HTTPError as e:
        raise HTTPException(502, "No se pudo contactar a Twilio.") from e
    if resp.status_code == 404:
        raise HTTPException(404, "La grabación todavía no está lista. Intente en un momento.")
    if resp.status_code >= 400:
        raise HTTPException(502, f"Twilio respondió {resp.status_code} al pedir la grabación.")
    return Response(resp.content, media_type="audio/mpeg", headers={"Cache-Control": "private, no-store"})


router.include_router(api)
