"""r2db.store — a small document/collection database on a blob target.

This is the "use R2 as your database instead of Supabase" layer. Each record is
one JSON object stored at ``{prefix}/{collection}/{id}.json``. You create, read,
update, delete and list records with plain method calls; the backend is an
S3-compatible bucket (:class:`~r2db.targets.R2Target`) in production or the local
filesystem (:class:`~r2db.targets.FilesystemTarget`) in dev and tests.

    from r2db import Store, target_from_env

    db = Store(target_from_env())
    users = db.collection("users")
    users.put("alice", {"name": "Alice", "plan": "pro"})
    users.get("alice")                       # -> {"id": "alice", "name": ..., ...}
    [u for u in users.all(where=lambda u: u["plan"] == "pro")]
    users.delete("alice")

What R2 is and isn't: object storage, not a query engine. Lookups by id are a
single O(1) GET; filtering (``all`` / ``query`` / ``find``) is a client-side
scan over the collection. That's a great fit for config, KV data, small-to-
medium collections and read-mostly workloads. For heavy relational querying,
keep a SQLite system-of-record (see the README recipe) and use :mod:`r2db.publish`
to serve rendered views from it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

from .targets import BlobTarget

_JSON = "application/json; charset=utf-8"
# Ids and collection names must be safe, unambiguous object-key segments (no
# slashes, no traversal). Keep it to url/path-friendly characters.
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*$")

Record = dict
Predicate = Callable[[Record], bool]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check(kind: str, value: str) -> str:
    s = str(value)
    if not _SAFE.match(s):
        raise ValueError(
            f"invalid {kind} {value!r}: use letters, digits, '.', '_' or '-' "
            "(no slashes or leading punctuation)")
    return s


class Store:
    """A namespace of collections on a single blob target.

    ``prefix`` scopes every key (e.g. an app or tenant name, or an API version).
    ``stamp_times`` injects ``id``/``created_at``/``updated_at`` on writes.
    """

    def __init__(self, target: BlobTarget, *, prefix: str = "",
                 stamp_times: bool = True,
                 cache_control: str = "private, no-store") -> None:
        self.target = target
        self.prefix = prefix.strip("/")
        self.stamp_times = stamp_times
        self.cache_control = cache_control

    def collection(self, name: str) -> "Collection":
        return Collection(self, name)

    def collections(self) -> list[str]:
        """Names of collections that currently hold at least one record."""
        base = f"{self.prefix}/" if self.prefix else ""
        names: set[str] = set()
        for key in self.target.list_keys(base):
            rest = key[len(base):] if base else key
            head, sep, tail = rest.partition("/")
            if sep and tail.endswith(".json"):
                names.add(head)
        return sorted(names)

    # -- convenience passthroughs to a collection ----------------------------
    def put(self, collection: str, id: str, record: Record, **kw) -> Record:
        return self.collection(collection).put(id, record, **kw)

    def get(self, collection: str, id: str) -> Optional[Record]:
        return self.collection(collection).get(id)

    def delete(self, collection: str, id: str) -> bool:
        return self.collection(collection).delete(id)

    def all(self, collection: str, *, where: Optional[Predicate] = None):
        return self.collection(collection).all(where=where)


class Collection:
    """A set of JSON records addressed by id under one prefix."""

    def __init__(self, store: Store, name: str) -> None:
        self.store = store
        self.name = _check("collection name", name)

    # -- key math ------------------------------------------------------------
    def _base(self) -> str:
        parts = [self.store.prefix, self.name]
        return "/".join(p for p in parts if p) + "/"

    def _key(self, id: str) -> str:
        return f"{self._base()}{_check('id', id)}.json"

    # -- writes --------------------------------------------------------------
    def put(self, id: str, record: Record, *, merge: bool = False) -> Record:
        """Create or replace record ``id``. Returns the stored record.

        ``merge=True`` reads the existing record (if any) and shallow-merges the
        new fields over it, preserving ``created_at`` and untouched keys.
        """
        if not isinstance(record, dict):
            raise TypeError("record must be a dict (JSON object)")
        out = dict(record)
        if merge:
            existing = self.get(id) or {}
            existing.update(out)
            out = existing
        if self.store.stamp_times:
            out.setdefault("id", str(id))
            out.setdefault("created_at", out.get("created_at") or _now_iso())
            out["updated_at"] = _now_iso()
        body = json.dumps(out, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        self.store.target.put(self._key(id), body, content_type=_JSON,
                              cache_control=self.store.cache_control)
        return out

    def update(self, id: str, fields: Record) -> Record:
        """Shallow-merge ``fields`` into an existing record (alias for
        ``put(id, fields, merge=True)``)."""
        return self.put(id, fields, merge=True)

    def delete(self, id: str) -> bool:
        """Delete record ``id``. Returns whether it existed."""
        existed = self.exists(id)
        self.store.target.delete(self._key(id))
        return existed

    # -- reads ---------------------------------------------------------------
    def get(self, id: str) -> Optional[Record]:
        body = self.store.target.get(self._key(id))
        return json.loads(body) if body is not None else None

    def exists(self, id: str) -> bool:
        return self.store.target.get(self._key(id)) is not None

    def ids(self) -> list[str]:
        base = self._base()
        out = []
        for key in self.store.target.list_keys(base):
            tail = key[len(base):]
            if tail.endswith(".json") and "/" not in tail:
                out.append(tail[:-len(".json")])
        return sorted(out)

    def count(self) -> int:
        return len(self.ids())

    def all(self, *, where: Optional[Predicate] = None) -> Iterator[Record]:
        """Iterate every record, optionally filtered by a ``where`` predicate.

        This is a full client-side scan (one GET per record). Fine for small
        collections; for large ones keep an index or a SQLite system-of-record.
        """
        for id in self.ids():
            rec = self.get(id)
            if rec is None:
                continue
            if where is None or where(rec):
                yield rec

    # ``query`` reads as an alias of ``all`` for callers who prefer that verb.
    query = all

    def find(self, where: Predicate) -> Optional[Record]:
        """First record matching ``where``, or None."""
        return next(self.all(where=where), None)
