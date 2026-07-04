# r2db — a generic Cloudflare R2 database (a Supabase replacement)

A small, dependency-light Python toolkit that turns a **Cloudflare R2** bucket
into a usable database + serving layer for any project — instead of standing up
Supabase (Postgres + PostgREST + RLS). No server to host, no egress fees, and
you develop against the local filesystem and ship against R2 without changing a
line of code.


```
your app ──► r2db.Store ──► BlobTarget ──► Cloudflare R2  (or local ./.r2db in dev)
             (documents)     ▲                 │
                             │                 ▼
      r2db.publish (read-models + manifest) ► public bucket ► frontend fetches JSON
      r2db.state   (backup/restore a file) ► private bucket ► durable SQLite, etc.
```

## Three layers, one interface

Everything sits on a tiny [`BlobTarget`](src/r2db/targets.py) interface with two
implementations — `FilesystemTarget` (dev/tests, no cloud) and `R2Target`
(production). Swap the target, keep the code.

| Layer | Module | What it gives you | Supabase equivalent |
|---|---|---|---|
| **Document store** | [`store.py`](src/r2db/store.py) | `Store`/`Collection`: CRUD + list over JSON records | tables + the client SDK |
| **Publish** | [`publish.py`](src/r2db/publish.py) | bake read-models to static JSON + a change-detection `manifest.json` | PostgREST / read API |
| **State** | [`state.py`](src/r2db/state.py) | back up / restore a file (e.g. SQLite) to a private bucket | managed Postgres durability |

## Install

```bash
uv sync                       # or: pip install -e .
cp .env.example .env          # optional — leave R2_* unset to use ./.r2db locally
```

With no `R2_*` env set, r2db writes to a local directory (`R2DB_DIR`, default
`./.r2db`) so you can try everything offline. Fill in `.env` to use a real bucket.

## Use it as a database

```python
from r2db import Store, target_from_env

db = Store(target_from_env())            # R2 if creds are set, else ./.r2db
users = db.collection("users")

users.put("alice", {"name": "Alice", "plan": "pro"})   # create/replace
users.get("alice")                        # {"id":"alice","name":"Alice",...,"created_at":...}
users.update("alice", {"plan": "enterprise"})          # shallow-merge, keeps created_at
users.exists("alice")                     # True
users.count()                             # 1
[u for u in users.all(where=lambda u: u["plan"] == "pro")]   # scan + filter
users.find(lambda u: u["name"] == "Alice")             # first match or None
users.delete("alice")                     # True (existed)
```

Each record is one object at `{prefix}/{collection}/{id}.json`. Writes stamp
`id`/`created_at`/`updated_at` (disable with `Store(..., stamp_times=False)`).
Pass `prefix=` to scope a whole namespace (an app name, tenant, or API version).

…or from the shell (same env-based target selection):

```bash
r2db put users alice --json '{"name":"Alice","plan":"pro"}'
r2db get users alice
r2db list users --records
r2db collections
r2db delete users alice
```

### What R2 is good at (and what it isn't)

R2 is strongly-consistent **object storage**, not a query engine. Lookups by id
are a single O(1) `GET`; filtering (`all`/`find`) is a **client-side scan** — one
`GET` per record. That's ideal for:

- config and feature flags, KV data, per-user/per-tenant documents
- small-to-medium collections, read-mostly workloads
- anything a static frontend fetches directly

For heavy relational querying, joins, or large scans, don't scan R2 — use the
recipe below.

## Use it as a Supabase-style serving layer (the scalable recipe)

The pattern the `tdf26` project uses in production, and the one to reach for when
a collection outgrows client-side scans:

1. Keep an append-only **SQLite** file as your system-of-record + compute engine
   (real SQL, joins, indexes — all local, all free).
2. Write plain functions that turn queries into JSON **read-models** (your "API").
3. `publish()` them to a **public** R2 bucket; the frontend fetches static JSON.
   The `manifest.json` lets it detect changes and cache-bust by content hash.
4. Back up the SQLite file to a **private** bucket with `backup_file` so the
   compute host stays disposable.

```python
from r2db import Document, R2Target, publish, backup_file, R2Target

target = R2Target()                       # public serving bucket, from R2_* env
docs = [
    Document("users/active.json", render_active_users(db)),
    Document("stats/summary.json", render_summary(db)),
]
publish(target, docs, prefix="v1", prune=True)         # writes docs + manifest.json

backup_file(R2Target(bucket="my-app-state"), "app.sqlite", "state/app.sqlite")
```

You get relational modelling, versioned read-models, change detection, near-zero
serving cost, and **no API server or row-level-security to operate** — read-only
public is just a bucket.

## One-time R2 setup

```bash
wrangler r2 bucket create my-app-db          # store + public serving
wrangler r2 bucket create my-app-state       # optional: private file backups
```

Create an R2 API token (Object Read & Write) and put the credentials in `.env`.
To serve publicly, enable the bucket's `r2.dev` URL or attach a custom domain and
set a CORS policy allowing `GET` from your frontend origin (Dashboard → R2 →
Settings). A frontend needs only the public bucket URL — no keys.

> Keep the **state/backup** bucket private and separate from any public serving
> bucket. `state.py` writes backups with `Cache-Control: private, no-store`, and
> the CLI's `backup`/`restore` prefer `R2_STATE_BUCKET`.

## Environment

| Var | Purpose |
|---|---|
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | R2 API credentials |
| `R2_BUCKET` | default bucket for the store + publisher |
| `R2_STATE_BUCKET` | optional private bucket for file backups |
| `R2DB_DIR` | filesystem-target root when no R2 creds are set (default `./.r2db`) |

## Tests

```bash
uv run pytest            # offline: filesystem target only, no cloud or creds
uv run ruff check .
```

## Layout

| Path | What |
|---|---|
| `src/r2db/targets.py` | `BlobTarget` + `FilesystemTarget` + `R2Target` + `target_from_env` |
| `src/r2db/store.py` | `Store` / `Collection` — the document database |
| `src/r2db/publish.py` | `Document` + `publish()` — read-models + manifest to a bucket |
| `src/r2db/state.py` | `backup_file` / `restore_file` — a file to/from a private bucket |
| `src/r2db/cli.py` | the `r2db` command-line interface |
| `tests/` | offline unit tests (filesystem target; no services) |
