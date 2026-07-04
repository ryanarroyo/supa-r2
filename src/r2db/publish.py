"""Publish a set of read-model documents to a blob target.

Serialises each document to compact JSON, writes a manifest (keys + content
hashes + sizes + generation time), and optionally prunes stale objects. The
manifest lets a frontend cheaply detect "did anything change" (poll one small
file, cache-bust by hash) and lets the next run skip unchanged uploads.

This is the "serving layer" half of the Supabase replacement: render your data
to a tree of static JSON and publish it to a PUBLIC R2 bucket. There is no API
server and no row-level security to operate — read-only-public is just a bucket.

Lifted, app-agnostic, from the ``r2sync`` package.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from .targets import BlobTarget

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
DEFAULT_CACHE_CONTROL = "public, max-age=300"


@dataclass(frozen=True)
class Document:
    key: str          # object key relative to prefix, e.g. "users/active.json"
    obj: object       # any json-serialisable value


@dataclass
class PublishResult:
    uploaded: list[str]
    skipped: list[str]
    pruned: list[str]
    manifest_key: str
    total_bytes: int


def _dump(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def publish(target: BlobTarget, documents: Iterable[Document], *,
            prefix: str = "", cache_control: str = DEFAULT_CACHE_CONTROL,
            prune: bool = False, generated_at: Optional[datetime] = None,
            manifest_extra: Optional[dict] = None) -> PublishResult:
    """Write ``documents`` (plus a ``manifest.json``) under ``prefix``.

    ``prune=True`` deletes any pre-existing objects under ``prefix`` that this
    run did not write (so a removed record doesn't linger). Documents whose
    bytes match the previously published manifest are skipped (not re-uploaded)
    when the target can return the old manifest, else everything is uploaded.
    """
    generated_at = generated_at or datetime.now(timezone.utc)
    prefix = prefix.rstrip("/")

    def full(key: str) -> str:
        return f"{prefix}/{key}" if prefix else key

    # Previously-published hashes (best-effort; only for the skip optimisation).
    prev_hashes: dict[str, str] = {}
    try:
        prev_manifest = _read_json(target, full("manifest.json"))
        if prev_manifest:
            prev_hashes = {d["key"]: d["hash"]
                           for d in prev_manifest.get("documents", [])}
    except Exception:  # noqa: BLE001 — skip optimisation is optional
        prev_hashes = {}

    entries = []
    uploaded, skipped = [], []
    written_keys = set()
    total = 0
    for doc in documents:
        body = _dump(doc.obj)
        h = hashlib.sha256(body).hexdigest()
        total += len(body)
        entries.append({"key": doc.key, "hash": h, "bytes": len(body)})
        written_keys.add(full(doc.key))
        if prev_hashes.get(doc.key) == h:
            skipped.append(doc.key)
            continue
        target.put(full(doc.key), body, content_type=JSON_CONTENT_TYPE,
                   cache_control=cache_control)
        uploaded.append(doc.key)

    manifest = {
        "generated_at": generated_at.astimezone(timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(entries),
        "documents": sorted(entries, key=lambda e: e["key"]),
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    manifest_body = _dump(manifest)
    manifest_key = full("manifest.json")
    target.put(manifest_key, manifest_body, content_type=JSON_CONTENT_TYPE,
               cache_control="public, max-age=60")
    written_keys.add(manifest_key)
    total += len(manifest_body)

    pruned = []
    if prune and prefix:
        for existing in target.list_keys(prefix + "/"):
            if existing not in written_keys:
                target.delete(existing)
                pruned.append(existing)

    return PublishResult(uploaded=uploaded, skipped=skipped, pruned=pruned,
                         manifest_key=manifest_key, total_bytes=total)


def _read_json(target: BlobTarget, key: str):
    """Best-effort read of an existing object (any target that implements get)."""
    body = target.get(key)
    return json.loads(body) if body else None
