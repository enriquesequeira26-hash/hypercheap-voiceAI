"""
Small document store for the call campaign: JSON documents and files kept in a private Vercel Blob store.

Set BLOB_READ_WRITE_TOKEN (Vercel adds it when a Blob store is connected to the project). For local development
set CAMPAIGN_LOCAL_DIR instead and everything is kept in that folder.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import settings

log = logging.getLogger("hypercheap.store")

API_URL = "https://vercel.com/api/blob"
API_VERSION = "11"
_TIMEOUT = httpx.Timeout(20, read=40)


class StoreError(RuntimeError):
    pass


def ready() -> bool:
    return bool(settings.campaign_local_dir or settings.blob_read_write_token)


def _token() -> str:
    token = settings.blob_read_write_token
    if not token:
        raise StoreError("El almacenamiento no está configurado (falta BLOB_READ_WRITE_TOKEN).")
    return token


def _store_id(token: str) -> str:
    # Token shape: vercel_blob_rw_<storeId>_<secret>
    parts = token.split("_")
    if len(parts) < 5 or not parts[3]:
        raise StoreError("BLOB_READ_WRITE_TOKEN no tiene el formato esperado.")
    return parts[3].lower()


def _local(path: str) -> Path:
    root = Path(settings.campaign_local_dir).resolve()
    target = (root / path).resolve()
    if root != target and root not in target.parents:
        raise StoreError("Ruta inválida.")
    return target


async def put_bytes(path: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    if settings.campaign_local_dir:
        target = _local(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        return
    token = _token()
    headers = {
        "authorization": f"Bearer {token}",
        "x-api-version": API_VERSION,
        "x-vercel-blob-access": "private",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
        "x-content-type": content_type,
        "x-cache-control-max-age": "60",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(3):
            resp = await client.put(API_URL, params={"pathname": path}, headers=headers, content=data)
            if resp.status_code < 500 and resp.status_code != 429:
                break
            await asyncio.sleep(0.4 * (attempt + 1))
    if resp.status_code >= 400:
        raise StoreError(f"No se pudo guardar {path}: HTTP {resp.status_code} {resp.text[:200]}")


async def get_bytes(path: str) -> Optional[bytes]:
    """Returns None when the document does not exist."""
    if settings.campaign_local_dir:
        target = _local(path)
        return target.read_bytes() if target.is_file() else None
    token = _token()
    url = f"https://{_store_id(token)}.private.blob.vercel-storage.com/{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        # cache=0 reads straight from origin, so a document that was just overwritten is never stale
        resp = await client.get(url, params={"cache": "0"}, headers={"authorization": f"Bearer {token}"})
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise StoreError(f"No se pudo leer {path}: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.content


async def list_paths(prefix: str) -> list[str]:
    if settings.campaign_local_dir:
        root = Path(settings.campaign_local_dir).resolve()
        found = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.suffix != ".tmp"]
        return sorted(p for p in found if p.startswith(prefix))
    token = _token()
    headers = {"authorization": f"Bearer {token}", "x-api-version": API_VERSION}
    paths: list[str] = []
    cursor: Optional[str] = None
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for _ in range(50):
            params: dict[str, Any] = {"prefix": prefix, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            resp = await client.get(API_URL, params=params, headers=headers)
            if resp.status_code >= 400:
                raise StoreError(f"No se pudo listar {prefix}: HTTP {resp.status_code} {resp.text[:200]}")
            page = resp.json()
            paths += [b["pathname"] for b in page.get("blobs", [])]
            cursor = page.get("cursor")
            if not page.get("hasMore") or not cursor:
                break
    return sorted(paths)


async def put_json(path: str, value: Any) -> None:
    await put_bytes(path, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json")


async def get_json(path: str, default: Any = None) -> Any:
    raw = await get_bytes(path)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        log.error("[store] %s is not valid JSON", path)
        return default
