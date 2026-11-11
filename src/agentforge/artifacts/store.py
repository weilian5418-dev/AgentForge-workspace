"""Immutable content-addressed object store (D06)."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class ArtifactStoreError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ArtifactStore:
    """Stores blobs by sha256. Write is atomic (temp + replace). Never mutate in place."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, content_hash: str) -> Path:
        return self.root / content_hash[:2] / content_hash[2:4] / content_hash

    def has(self, content_hash: str) -> bool:
        return self._path_for(content_hash).is_file()

    def put_bytes(self, data: bytes) -> str:
        content_hash = hashlib.sha256(data).hexdigest()
        dest = self._path_for(content_hash)
        if dest.is_file():
            # Content-addressed reuse; permissions still gated by DB records.
            return content_hash
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".tmp_")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, dest)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise
        return content_hash

    def put_file(self, path: Path) -> str:
        if not path.is_file():
            raise ArtifactStoreError("not_a_file", f"Not a file: {path}")
        if path.is_symlink():
            raise ArtifactStoreError("symlink_refused", "Refusing symlink as store source")
        data = path.read_bytes()
        return self.put_bytes(data)

    def get_bytes(self, content_hash: str) -> bytes:
        path = self._path_for(content_hash)
        if not path.is_file():
            raise ArtifactStoreError("object_missing", f"Content object missing: {content_hash}")
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != content_hash:
            raise ArtifactStoreError("hash_mismatch", "Stored object hash mismatch")
        return data

    def delete(self, content_hash: str) -> bool:
        path = self._path_for(content_hash)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def verify(self, content_hash: str) -> bool:
        try:
            self.get_bytes(content_hash)
            return True
        except ArtifactStoreError:
            return False
