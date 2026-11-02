"""Persistent platform-to-local identifier mappings."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class StableIDStore:
    """Allocate stable local IDs that survive restarts and list reordering."""

    _COLLECTIONS = {
        "questions": "nextQuestionId",
        "groups": "nextGroupId",
        "notices": "nextNoticeId",
    }

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._values: dict[str, dict[str, int]] = {
            name: {} for name in self._COLLECTIONS
        }
        self._next: dict[str, int] = {
            name: 1 for name in self._COLLECTIONS
        }
        self._load()

    def question_id(self, platform_id: str) -> int:
        return self._allocate("questions", platform_id)

    def group_id(self, platform_id: str) -> int:
        return self._allocate("groups", platform_id)

    def notice_id(self, platform_id: str) -> int:
        return self._allocate("notices", platform_id)

    def _allocate(self, collection: str, platform_id: str) -> int:
        normalized = str(platform_id).strip()
        if not normalized:
            raise ValueError("platform identifier must not be empty")
        with self._lock:
            existing = self._values[collection].get(normalized)
            if existing is not None:
                return existing
            local_id = max(1, self._next[collection])
            while any(value == local_id for value in self._values[collection].values()):
                local_id += 1
            self._values[collection][normalized] = local_id
            self._next[collection] = local_id + 1
            self._persist_locked()
            return local_id

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, Mapping):
            return
        with self._lock:
            for collection, next_key in self._COLLECTIONS.items():
                values = raw.get(collection)
                normalized: dict[str, int] = {}
                if isinstance(values, Mapping):
                    for platform_id, local_id in values.items():
                        parsed = _positive_int(local_id)
                        if platform_id and parsed is not None:
                            normalized[str(platform_id)] = parsed
                self._values[collection] = normalized
                raw_next = _positive_int(raw.get(next_key))
                minimum = max(normalized.values(), default=0) + 1
                self._next[collection] = max(minimum, raw_next or 1)

    def _persist_locked(self) -> None:
        if self._path is None:
            return
        payload: dict[str, Any] = {"version": 1}
        for collection, next_key in self._COLLECTIONS.items():
            payload[collection] = dict(sorted(self._values[collection].items()))
            payload[next_key] = self._next[collection]
        temporary_name = ""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                handle.write("\n")
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, self._path)
        except OSError as exc:
            raise OSError(f"save platform identity mapping: {exc}") from exc
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
