"""Back up / restore a single file (e.g. a SQLite system-of-record) to a blob
target.

Lets a compute host be disposable: restore the file before a run, back it up
after. Keep these backups in a PRIVATE bucket, separate from any public serving
bucket — the ``cache_control`` here is deliberately ``private, no-store``.

Lifted, app-agnostic, from the ``r2sync`` package.
"""

from __future__ import annotations

from pathlib import Path

from .targets import BlobTarget

_OCTET = "application/octet-stream"


def backup_file(target: BlobTarget, local_path: str | Path, key: str) -> int:
    """Upload ``local_path`` to ``key``. Returns bytes written."""
    body = Path(local_path).read_bytes()
    target.put(key, body, content_type=_OCTET, cache_control="private, no-store")
    return len(body)


def restore_file(target: BlobTarget, local_path: str | Path, key: str) -> bool:
    """Download ``key`` to ``local_path`` if it exists. Returns whether a file
    was restored (False = no prior backup, start fresh)."""
    body = target.get(key)
    if body is None:
        return False
    p = Path(local_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return True
