"""r2db — a generic Cloudflare R2 database toolkit (a Supabase replacement).

Three composable layers over an S3-compatible object store, all sharing one tiny
:class:`BlobTarget` interface so you develop against the local filesystem and
ship against R2 unchanged:

* **store**   — a document/collection database: CRUD + list over JSON records
                (``Store`` / ``Collection``). Your app's data lives here.
* **publish** — bake a set of read-models to a public bucket as static JSON with
                a change-detection ``manifest.json`` (``Document`` / ``publish``).
                The read-only "API"/serving layer, with no server to run.
* **state**   — back up / restore a single file, e.g. a SQLite system-of-record,
                to a private bucket (``backup_file`` / ``restore_file``).

The core (targets + publish + state) is a reusable object-storage publishing
toolkit; the document ``store`` generalises it into a standalone database.
"""

from .publish import Document, PublishResult, publish
from .state import backup_file, restore_file
from .store import Collection, Store
from .targets import BlobTarget, FilesystemTarget, R2Target, target_from_env

__all__ = [
    "BlobTarget", "FilesystemTarget", "R2Target", "target_from_env",
    "Store", "Collection",
    "Document", "PublishResult", "publish",
    "backup_file", "restore_file",
]

__version__ = "0.1.0"
