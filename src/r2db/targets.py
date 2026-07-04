"""Blob targets: where records and baked documents are read and written.

``FilesystemTarget`` writes a local directory tree (dev previews + tests).
``R2Target`` writes to a Cloudflare R2 bucket over the S3-compatible API (boto3).

Both implement the same tiny interface so every layer above them (the document
store, the publisher, backup/restore) is target-agnostic — you develop and test
against the filesystem and ship against R2 without changing a line.

App-agnostic — usable in any project.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class BlobTarget(ABC):
    """The minimal object-storage interface the rest of r2db is built on."""

    @abstractmethod
    def put(self, key: str, body: bytes, *, content_type: str,
            cache_control: Optional[str] = None) -> None:
        ...

    @abstractmethod
    def get(self, key: str) -> Optional[bytes]:
        """Object bytes, or None if the key doesn't exist."""
        ...

    @abstractmethod
    def list_keys(self, prefix: str) -> set[str]:
        """Existing object keys under ``prefix`` (used to enumerate + prune)."""
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...


class FilesystemTarget(BlobTarget):
    """Writes objects as files under ``root`` (keys become relative paths).

    Perfect for local development and the test suite — no cloud, no creds. The
    on-disk tree is byte-for-byte what R2 would serve.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def put(self, key: str, body: bytes, *, content_type: str,
            cache_control: Optional[str] = None) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def get(self, key: str) -> Optional[bytes]:
        path = self.root / key
        return path.read_bytes() if path.exists() else None

    def list_keys(self, prefix: str) -> set[str]:
        base = self.root / prefix
        if not base.exists():
            return set()
        return {str(p.relative_to(self.root)) for p in base.rglob("*")
                if p.is_file()}

    def delete(self, key: str) -> None:
        path = self.root / key
        if path.exists():
            path.unlink()


class R2Target(BlobTarget):
    """Cloudflare R2 bucket over the S3-compatible API.

    Credentials come from the environment (or explicit args):
      R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET.
    Create an R2 API token with Object Read & Write for the bucket
    (Cloudflare dashboard → R2 → Manage API Tokens).
    """

    def __init__(self, *, account_id: Optional[str] = None,
                 access_key_id: Optional[str] = None,
                 secret_access_key: Optional[str] = None,
                 bucket: Optional[str] = None) -> None:
        account_id = account_id or os.getenv("R2_ACCOUNT_ID")
        access_key_id = access_key_id or os.getenv("R2_ACCESS_KEY_ID")
        secret_access_key = secret_access_key or os.getenv("R2_SECRET_ACCESS_KEY")
        self.bucket = bucket or os.getenv("R2_BUCKET")
        missing = [n for n, v in [
            ("R2_ACCOUNT_ID", account_id), ("R2_ACCESS_KEY_ID", access_key_id),
            ("R2_SECRET_ACCESS_KEY", secret_access_key), ("R2_BUCKET", self.bucket),
        ] if not v]
        if missing:
            raise ValueError(f"missing R2 config: {', '.join(missing)}")

        import boto3
        from botocore.config import Config

        # R2 rejects the default flexible checksums newer botocore adds to every
        # request, so pin them to when-required; also force path-style + sigv4.
        cfg = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        )
        self._s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=cfg,
        )

    def put(self, key: str, body: bytes, *, content_type: str,
            cache_control: Optional[str] = None) -> None:
        extra = {"ContentType": content_type}
        if cache_control:
            extra["CacheControl"] = cache_control
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=body, **extra)

    def get(self, key: str) -> Optional[bytes]:
        from botocore.exceptions import ClientError
        try:
            resp = self._s3.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise

    def list_keys(self, prefix: str) -> set[str]:
        keys: set[str] = set()
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._s3.list_objects_v2(**kw)
            keys.update(o["Key"] for o in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return keys

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self.bucket, Key=key)


def target_from_env(*, prefer_state: bool = False) -> BlobTarget:
    """Build the natural target from the environment.

    Returns an :class:`R2Target` when R2 credentials are present (using
    ``R2_STATE_BUCKET`` when ``prefer_state`` is set, else ``R2_BUCKET``), or a
    :class:`FilesystemTarget` rooted at ``R2DB_DIR`` (default ``./.r2db``) for
    local development when no cloud creds are configured.
    """
    if os.getenv("R2_ACCOUNT_ID") and os.getenv("R2_ACCESS_KEY_ID"):
        bucket = None
        if prefer_state:
            bucket = os.getenv("R2_STATE_BUCKET") or os.getenv("R2_BUCKET")
        return R2Target(bucket=bucket)
    return FilesystemTarget(os.getenv("R2DB_DIR", ".r2db"))
