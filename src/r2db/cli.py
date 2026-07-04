"""r2db command-line interface.

A thin wrapper over the library for quick record CRUD, publishing and file
backup/restore. Targets are chosen from the environment: if R2 credentials are
set it talks to R2, otherwise it uses a local filesystem directory (``R2DB_DIR``,
default ``./.r2db``) so you can try everything with no cloud account.

    r2db put users alice --json '{"name":"Alice","plan":"pro"}'
    r2db get users alice
    r2db list users
    r2db delete users alice
    r2db backup ./app.sqlite state/app.sqlite      # uses R2_STATE_BUCKET if set
    r2db restore ./app.sqlite state/app.sqlite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .state import backup_file, restore_file
from .store import Store
from .targets import target_from_env


def _store(args) -> Store:
    return Store(target_from_env(), prefix=args.prefix or "")


def _load_value(args) -> dict:
    if args.file:
        raw = Path(args.file).read_text()
    elif args.json:
        raw = args.json
    else:
        raw = sys.stdin.read()
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise SystemExit("record must be a JSON object")
    return obj


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="r2db", description="Cloudflare R2 database")
    p.add_argument("--version", action="version", version=f"r2db {__version__}")
    p.add_argument("--prefix", default=None,
                   help="key prefix scoping all collections (e.g. an app name)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("put", help="create or replace a record")
    sp.add_argument("collection")
    sp.add_argument("id")
    sp.add_argument("--json", help="inline JSON object")
    sp.add_argument("--file", help="read the JSON object from a file")
    sp.add_argument("--merge", action="store_true",
                    help="shallow-merge into the existing record")

    sg = sub.add_parser("get", help="read a record")
    sg.add_argument("collection")
    sg.add_argument("id")

    sl = sub.add_parser("list", help="list ids (or --records for full records)")
    sl.add_argument("collection")
    sl.add_argument("--records", action="store_true")

    sd = sub.add_parser("delete", help="delete a record")
    sd.add_argument("collection")
    sd.add_argument("id")

    sub.add_parser("collections", help="list non-empty collections")

    sb = sub.add_parser("backup", help="upload a file to a key")
    sb.add_argument("local")
    sb.add_argument("key")

    sr = sub.add_parser("restore", help="download a key to a file")
    sr.add_argument("local")
    sr.add_argument("key")

    args = p.parse_args(argv)

    if args.cmd == "put":
        rec = _store(args).collection(args.collection).put(
            args.id, _load_value(args), merge=args.merge)
        _print(rec)
    elif args.cmd == "get":
        rec = _store(args).collection(args.collection).get(args.id)
        if rec is None:
            print(f"no record {args.collection}/{args.id}", file=sys.stderr)
            return 1
        _print(rec)
    elif args.cmd == "list":
        col = _store(args).collection(args.collection)
        _print(list(col.all()) if args.records else col.ids())
    elif args.cmd == "delete":
        existed = _store(args).collection(args.collection).delete(args.id)
        print("deleted" if existed else "no such record")
        return 0 if existed else 1
    elif args.cmd == "collections":
        _print(_store(args).collections())
    elif args.cmd == "backup":
        # state files belong in a private bucket; prefer R2_STATE_BUCKET.
        n = backup_file(target_from_env(prefer_state=True), args.local, args.key)
        print(f"backed up {args.local} -> {args.key} ({n} bytes)")
    elif args.cmd == "restore":
        ok = restore_file(target_from_env(prefer_state=True), args.local, args.key)
        print(f"restored {args.key} -> {args.local}" if ok
              else f"no backup at {args.key}; starting fresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
