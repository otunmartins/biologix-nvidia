"""Content-addressed cache for model outputs.

Keys include the model identity *and version*, so upgrading a model does not
silently serve stale vectors under the new name. Cache hits are recorded in
the provenance manifest, because "cached" and "freshly computed" are
different claims about a number's origin.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from biologix.provenance import stable_hash
from biologix.settings import get_settings


@dataclass
class CacheEntry:
    key: str
    path: Path
    hit: bool
    stored_at: str | None = None


class ArrayCache:
    """Disk cache for numeric arrays plus a JSON metadata sidecar."""

    def __init__(self, namespace: str, directory: str | Path | None = None) -> None:
        settings = get_settings()
        base = Path(directory) if directory else settings.cache_dir
        self.directory = Path(base) / namespace
        self.directory.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace

    def key_for(self, *, model: str, version: str, payload: Any) -> str:
        """Cache key binding the content to the exact model version."""
        return stable_hash(
            {"ns": self.namespace, "model": model, "version": version, "payload": payload},
            length=32,
        )

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.directory / f"{key}.npy", self.directory / f"{key}.json"

    def get(self, key: str) -> tuple[np.ndarray, dict[str, Any]] | None:
        array_path, meta_path = self._paths(key)
        if not (array_path.exists() and meta_path.exists()):
            return None
        try:
            array = np.load(array_path, allow_pickle=False)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            # A corrupt entry is dropped rather than raised: the caller can
            # always recompute.
            array_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            return None
        return array, meta

    def put(self, key: str, array: np.ndarray, meta: dict[str, Any]) -> CacheEntry:
        array_path, meta_path = self._paths(key)
        np.save(array_path, array, allow_pickle=False)
        payload = dict(meta)
        payload["_cached_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload["_shape"] = list(array.shape)
        payload["_dtype"] = str(array.dtype)
        meta_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return CacheEntry(key=key, path=array_path, hit=False, stored_at=payload["_cached_at"])

    def stats(self) -> dict[str, Any]:
        arrays = list(self.directory.glob("*.npy"))
        return {
            "namespace": self.namespace,
            "directory": str(self.directory),
            "n_entries": len(arrays),
            "bytes": sum(p.stat().st_size for p in arrays),
        }

    def clear(self) -> int:
        n = 0
        for p in list(self.directory.glob("*.npy")) + list(self.directory.glob("*.json")):
            p.unlink(missing_ok=True)
            n += 1
        return n


class JsonCache:
    """Disk cache for JSON-serialisable model responses (e.g. GenMol)."""

    def __init__(self, namespace: str, directory: str | Path | None = None) -> None:
        settings = get_settings()
        base = Path(directory) if directory else settings.cache_dir
        self.directory = Path(base) / namespace
        self.directory.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace

    def key_for(self, *, model: str, version: str, payload: Any) -> str:
        return stable_hash(
            {"ns": self.namespace, "model": model, "version": version, "payload": payload},
            length=32,
        )

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self.directory / f"{key}.json"
        payload = dict(value)
        payload["_cached_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def stats(self) -> dict[str, Any]:
        files = list(self.directory.glob("*.json"))
        return {
            "namespace": self.namespace,
            "directory": str(self.directory),
            "n_entries": len(files),
            "bytes": sum(p.stat().st_size for p in files),
        }

    def clear(self) -> int:
        n = 0
        for p in self.directory.glob("*.json"):
            p.unlink(missing_ok=True)
            n += 1
        return n
