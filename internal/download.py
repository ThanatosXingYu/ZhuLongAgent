"""Trusted local attachment storage and batch management."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import os
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener, getproxies

from .agent import ExerciseDetail, ExerciseGroup, File

DEFAULT_MAX_BYTES = 512 << 20
INSPECTION_PREVIEW_BYTES = 64 << 10
METADATA_TIMEOUT_SECONDS = 15.0
DownloadProgress = Callable[[int, int], None]
DownloadControl = Callable[[], None]


class SourceProtocol(Protocol):
    def exercises(self, refresh: bool = False) -> list[ExerciseGroup]: ...

    def exercise(self, exercise_id: int, refresh: bool = False) -> ExerciseDetail: ...


class DownloadError(RuntimeError):
    """Base class for local attachment errors."""


class ExerciseNotFound(DownloadError):
    pass


class AttachmentNotFound(DownloadError):
    pass


class InvalidURL(DownloadError):
    pass


class TooLarge(DownloadError):
    pass


class DownloadFailed(DownloadError):
    pass


class StorageError(DownloadError):
    pass


class AttachmentNotDownloaded(DownloadError):
    pass


# Export names matching the Go package's error identifiers for callers that
# use the migration as a drop-in service.
ErrExerciseNotFound = ExerciseNotFound
ErrAttachmentNotFound = AttachmentNotFound
ErrInvalidURL = InvalidURL
ErrTooLarge = TooLarge
ErrDownloadFailed = DownloadFailed
ErrStorage = StorageError
ErrAttachmentNotDownloaded = AttachmentNotDownloaded


@dataclass(frozen=True)
class Result:
    path: str
    size: int
    existed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "size": self.size, "existed": self.existed}


@dataclass(frozen=True)
class AttachmentInfo:
    index: int
    name: str
    path: str
    exists: bool = False
    size: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "index": self.index,
            "name": self.name,
            "path": self.path,
            "exists": self.exists,
            "size": self.size,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class Inspection(AttachmentInfo):
    sha256: str = ""
    content_type: str = ""
    encoding: str = "utf-8"
    preview: str = ""
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result.update(
            {
                "sha256": self.sha256,
                "contentType": self.content_type,
                "encoding": self.encoding,
                "preview": self.preview,
                "truncated": self.truncated,
            }
        )
        return result


@dataclass(frozen=True)
class ExerciseFiles:
    category: str
    id: int
    name: str
    attachments: tuple[AttachmentInfo, ...] = ()
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "category": self.category,
            "id": self.id,
            "name": self.name,
            "attachments": [item.to_dict() for item in self.attachments],
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class CatalogResult:
    exercises: tuple[ExerciseFiles, ...] = ()
    total_exercises: int = 0
    total_attachments: int = 0
    existing_attachments: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercises": [item.to_dict() for item in self.exercises],
            "totalExercises": self.total_exercises,
            "totalAttachments": self.total_attachments,
            "existingAttachments": self.existing_attachments,
        }


@dataclass(frozen=True)
class BatchItem:
    exercise_id: int
    index: int
    name: str
    path: str
    status: str
    size: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "exerciseId": self.exercise_id,
            "index": self.index,
            "name": self.name,
            "path": self.path,
            "status": self.status,
            "size": self.size,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class BatchIssue:
    exercise_id: int
    name: str
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {"exerciseId": self.exercise_id, "name": self.name, "error": self.error}


@dataclass(frozen=True)
class BatchResult:
    total: int = 0
    downloaded: int = 0
    existing: int = 0
    failed: int = 0
    exercise_failures: int = 0
    items: tuple[BatchItem, ...] = ()
    issues: tuple[BatchIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "total": self.total,
            "downloaded": self.downloaded,
            "existing": self.existing,
            "failed": self.failed,
            "exerciseFailures": self.exercise_failures,
            "items": [item.to_dict() for item in self.items],
        }
        if self.issues:
            result["issues"] = [item.to_dict() for item in self.issues]
        return result


@dataclass(frozen=True)
class ClearResult:
    exercise_id: int
    directory: str
    removed_files: int = 0
    removed_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "exerciseId": self.exercise_id,
            "directory": self.directory,
            "removedFiles": self.removed_files,
            "removedBytes": self.removed_bytes,
        }


@dataclass(frozen=True)
class RedownloadResult:
    clear: ClearResult
    download: BatchResult

    def to_dict(self) -> dict[str, Any]:
        return {"clear": self.clear.to_dict(), "download": self.download.to_dict()}


@dataclass(frozen=True)
class _ResolvedAttachment:
    file: File
    info: AttachmentInfo
    target: Path


@dataclass(frozen=True)
class _ResolvedExercise:
    category: str
    id: int
    name: str
    directory: Path
    display_directory: str
    attachments: tuple[_ResolvedAttachment, ...]


class DownloadService:
    """Store attachments under a path derived solely from trusted metadata."""

    def __init__(
        self,
        root: str | Path = "download",
        source: SourceProtocol | None = None,
        http_client: object | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = 300.0,
    ) -> None:
        root_value = str(root).strip() or "download"
        self.root = Path(root_value)
        self.display_root = (
            self.root if not self.root.is_absolute() else Path(self.root.name)
        )
        self.source = source
        self.http_client = http_client
        self.max_bytes = int(max_bytes)
        self.timeout = timeout
        self._size_lock = threading.Lock()
        self._remote_sizes: dict[str, int] = {}

    def download(
        self,
        exercise_id: int,
        attachment_index: int,
        progress: DownloadProgress | None = None,
        control: DownloadControl | None = None,
    ) -> Result:
        if control is not None:
            control()
        resolved = self._resolve(exercise_id)
        if attachment_index < 0 or attachment_index >= len(resolved.attachments):
            raise AttachmentNotFound("attachment not found")
        attachment = resolved.attachments[attachment_index]
        target = self._trusted_child(attachment.target)
        url = _validate_url(attachment.file.url)
        try:
            existing = target.stat()
        except FileNotFoundError:
            existing = None
        except OSError as exc:
            raise StorageError(
                "attachment storage failed: inspect destination"
            ) from exc
        if existing is not None:
            if not target.is_file():
                raise StorageError(
                    "attachment storage failed: destination is not a regular file"
                )
            self._remember_size(url, existing.st_size)
            if control is not None:
                control()
            if progress is not None:
                progress(existing.st_size, existing.st_size)
            return Result(attachment.info.path, existing.st_size, True)

        parent = target.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            self._trusted_child(parent)
        except OSError as exc:
            raise StorageError(
                "attachment storage failed: create destination directory"
            ) from exc

        response: Any | None = None
        partial_path = self._trusted_child(
            parent / f".{target.name}.part"
        )
        written = 0
        declared = -1
        try:
            if control is not None:
                control()
            try:
                part_stat = partial_path.stat()
                if not partial_path.is_file():
                    raise StorageError("attachment storage failed: partial path is not a file")
                written = part_stat.st_size
            except FileNotFoundError:
                written = 0
            except OSError as exc:
                raise StorageError("attachment storage failed: inspect partial file") from exc
            if written > self.max_bytes:
                raise TooLarge("attachment is too large")
            response, written, declared = self._open_download_response(
                url, written, partial_path
            )
            if declared > self.max_bytes:
                raise TooLarge("attachment is too large")
            if declared >= 0:
                self._remember_size(url, declared)
            if progress is not None:
                progress(written, declared)
            try:
                mode = "ab" if written else "wb"
                with partial_path.open(mode) as temporary:
                    while True:
                        if control is not None:
                            control()
                        try:
                            chunk = response.read(
                                min(1024 * 1024, self.max_bytes + 1 - written)
                            )
                        except (HTTPError, URLError, TimeoutError, OSError) as exc:
                            raise DownloadFailed(
                                f"attachment download failed: stream attachment: {exc}"
                            ) from exc
                        if not chunk:
                            break
                        if control is not None:
                            control()
                        written += len(chunk)
                        if written > self.max_bytes:
                            raise TooLarge("attachment is too large")
                        temporary.write(chunk)
                        if progress is not None:
                            progress(written, declared)
                    temporary.flush()
                    os.fchmod(temporary.fileno(), 0o644)
                os.replace(partial_path, target)
            except (TooLarge, DownloadFailed):
                raise
            except OSError as exc:
                raise StorageError(
                    "attachment storage failed: write attachment"
                ) from exc
            self._remember_size(url, written)
            return Result(attachment.info.path, written, False)
        except (TooLarge, DownloadFailed, StorageError):
            raise
        except (HTTPError, URLError, TimeoutError) as exc:
            raise DownloadFailed(
                f"attachment download failed: request attachment: {exc}"
            ) from exc
        finally:
            _close_quietly(locals().get("response"))

    def _open_download_response(
        self, url: str, part_size: int, partial_path: Path
    ) -> tuple[Any, int, int]:
        """Open a response, resuming a stable ``.part`` file when supported."""
        headers = {"Range": f"bytes={part_size}-"} if part_size else None
        response: Any | None = None
        try:
            response = self._open(url, headers=headers)
            status = _response_status(response)
            if status == 416 and part_size:
                total = _response_total_size(response)
                if total >= 0 and total == part_size:
                    return response, part_size, total
                partial_path.unlink(missing_ok=True)
                _close_quietly(response)
                response = self._open(url)
                status = _response_status(response)
                part_size = 0
            if status < 200 or status >= 300:
                raise DownloadFailed(
                    f"attachment download failed: upstream status {status}"
                )
            if part_size and status == 206:
                total = _response_total_size(response)
                if total < 0:
                    remaining = _content_length(response)
                    total = part_size + remaining if remaining >= 0 else -1
                return response, part_size, total
            if part_size and status == 200:
                # Server ignored Range.  Replacing the partial file is safer than
                # appending a second copy of the response.
                return response, 0, _content_length(response)
            return response, 0, _response_total_size(response)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            _close_quietly(response)
            raise DownloadFailed(
                f"attachment download failed: request attachment: {exc}"
            ) from exc

    def attachments(self, exercise_id: int) -> list[AttachmentInfo]:
        return self._attachment_infos(self._resolve(exercise_id))

    def probe_size(self, exercise_id: int, attachment_index: int) -> int:
        resolved = self._resolve(exercise_id)
        if attachment_index < 0 or attachment_index >= len(resolved.attachments):
            raise AttachmentNotFound("attachment not found")
        attachment = resolved.attachments[attachment_index]
        url = _validate_url(attachment.file.url)
        known = self._known_size(url)
        if known is not None:
            return known
        size = self._probe_url_size(url)
        if size >= 0:
            self._remember_size(url, size)
        return size

    def catalog(self) -> CatalogResult:
        groups = self._exercises()
        entries: list[ExerciseFiles] = []
        total_attachments = 0
        existing_attachments = 0
        for group in groups:
            for summary in group.corpus:
                try:
                    detail = self._exercise(summary.id)
                except Exception:
                    entries.append(
                        ExerciseFiles(
                            group.name,
                            summary.id,
                            summary.name,
                            error="题目详情读取失败",
                        )
                    )
                    continue
                try:
                    resolved = self._resolve_detail(
                        summary.id, group.name, summary.name, detail
                    )
                except DownloadError:
                    entries.append(
                        ExerciseFiles(
                            group.name,
                            summary.id,
                            summary.name,
                            error="附件目录检查失败",
                        )
                    )
                    continue
                infos = tuple(self._attachment_infos(resolved))
                total_attachments += len(infos)
                existing_attachments += sum(1 for item in infos if item.exists)
                entries.append(
                    ExerciseFiles(group.name, summary.id, resolved.name, infos)
                )
        return CatalogResult(
            tuple(entries),
            sum(len(group.corpus) for group in groups),
            total_attachments,
            existing_attachments,
        )

    def download_exercise(self, exercise_id: int) -> BatchResult:
        return self._download_resolved(self._resolve(exercise_id))

    def download_all(self) -> BatchResult:
        total = downloaded = existing = failed = exercise_failures = 0
        items: list[BatchItem] = []
        issues: list[BatchIssue] = []
        for group in self._exercises():
            for summary in group.corpus:
                try:
                    batch = self.download_exercise(summary.id)
                except Exception:
                    exercise_failures += 1
                    issues.append(
                        BatchIssue(summary.id, summary.name, "题目附件读取失败")
                    )
                    continue
                total += batch.total
                downloaded += batch.downloaded
                existing += batch.existing
                failed += batch.failed
                items.extend(batch.items)
        return BatchResult(
            total,
            downloaded,
            existing,
            failed,
            exercise_failures,
            tuple(items),
            tuple(issues),
        )

    def clear_exercise(self, exercise_id: int) -> ClearResult:
        return self._clear_resolved(self._resolve(exercise_id))

    def redownload_exercise(self, exercise_id: int) -> RedownloadResult:
        cleared = self.clear_exercise(exercise_id)
        return RedownloadResult(cleared, self.download_exercise(exercise_id))

    def inspect(self, exercise_id: int, attachment_index: int) -> Inspection:
        resolved = self._resolve(exercise_id)
        if attachment_index < 0 or attachment_index >= len(resolved.attachments):
            raise AttachmentNotFound("attachment not found")
        attachment = resolved.attachments[attachment_index]
        target = self._trusted_child(attachment.target)
        try:
            stat = target.stat()
        except FileNotFoundError as exc:
            raise AttachmentNotDownloaded("attachment is not downloaded") from exc
        except OSError as exc:
            raise StorageError("attachment storage failed: open attachment") from exc
        if not target.is_file():
            raise StorageError("attachment storage failed: inspect attachment file")
        try:
            with target.open("rb") as handle:
                captured = handle.read(INSPECTION_PREVIEW_BYTES + 1)
                digest = hashlib.sha256()
                digest.update(captured)
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise StorageError(
                "attachment storage failed: read attachment preview"
            ) from exc
        truncated = len(captured) > INSPECTION_PREVIEW_BYTES
        preview_bytes = captured[:INSPECTION_PREVIEW_BYTES] if truncated else captured
        if b"\x00" in preview_bytes:
            encoding = "base64"
            preview = base64.b64encode(preview_bytes).decode("ascii")
        else:
            try:
                preview = preview_bytes.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                encoding = "base64"
                preview = base64.b64encode(preview_bytes).decode("ascii")
        info = AttachmentInfo(
            attachment.info.index,
            attachment.info.name,
            attachment.info.path,
            True,
            stat.st_size,
        )
        return Inspection(
            **info.__dict__,
            sha256=digest.hexdigest(),
            content_type=_detect_content_type(preview_bytes, attachment.info.name),
            encoding=encoding,
            preview=preview,
            truncated=truncated,
        )

    def _download_resolved(self, resolved: _ResolvedExercise) -> BatchResult:
        items: list[BatchItem] = []
        downloaded = existing = failed = 0
        for attachment in resolved.attachments:
            item_base = (
                resolved.id,
                attachment.info.index,
                attachment.info.name,
                attachment.info.path,
            )
            try:
                result = self.download(resolved.id, attachment.info.index)
            except Exception:
                failed += 1
                items.append(
                    BatchItem(*item_base, status="failed", error="附件下载失败")
                )
                continue
            if result.existed:
                existing += 1
                status = "existing"
            else:
                downloaded += 1
                status = "downloaded"
            items.append(BatchItem(*item_base, status=status, size=result.size))
        return BatchResult(
            len(resolved.attachments), downloaded, existing, failed, 0, tuple(items)
        )

    def _clear_resolved(self, resolved: _ResolvedExercise) -> ClearResult:
        target = self._trusted_child(resolved.directory)
        result = ClearResult(resolved.id, resolved.display_directory)
        try:
            target.lstat()
        except FileNotFoundError:
            return result
        except OSError as exc:
            raise StorageError(
                "attachment storage failed: inspect exercise directory"
            ) from exc
        removed_files = removed_bytes = 0
        try:
            for path in target.rglob("*"):
                try:
                    stat = path.lstat()
                except OSError as exc:
                    raise StorageError(
                        "attachment storage failed: count exercise files"
                    ) from exc
                if path.is_symlink():
                    continue
                if stat.st_mode & 0o170000 == 0o100000:
                    removed_files += 1
                    removed_bytes += stat.st_size
            _remove_tree_without_following_symlinks(target)
        except StorageError:
            raise
        except OSError as exc:
            raise StorageError(
                "attachment storage failed: remove exercise directory"
            ) from exc
        return ClearResult(
            resolved.id, resolved.display_directory, removed_files, removed_bytes
        )

    def _resolve(self, exercise_id: int) -> _ResolvedExercise:
        groups = self._exercises()
        category = summary_name = ""
        found = False
        for group in groups:
            for summary in group.corpus:
                if summary.id == exercise_id:
                    category, summary_name, found = group.name, summary.name, True
                    break
            if found:
                break
        if not found:
            raise ExerciseNotFound("exercise not found")
        return self._resolve_detail(
            exercise_id, category, summary_name, self._exercise(exercise_id)
        )

    def _resolve_detail(
        self, exercise_id: int, category: str, summary_name: str, detail: ExerciseDetail
    ) -> _ResolvedExercise:
        exercise_name = detail.name.strip() or summary_name
        safe_category = safe_segment(category, "Uncategorized", 120)
        safe_name = safe_segment(exercise_name, "exercise", 120)
        root_absolute = self.root.absolute()
        directory = root_absolute / safe_category / f"{exercise_id}-{safe_name}"
        self._trusted_child(directory)
        used: set[str] = set()
        resolved: list[_ResolvedAttachment] = []
        for index, file in enumerate(detail.attachment.files):
            parsed = urlsplit(file.url.strip())
            filename = unique_attachment_filename(
                attachment_filename(file, parsed, index), index, used
            )
            target = directory / filename
            if not _within_root(root_absolute, target):
                raise StorageError(
                    "attachment storage failed: resolved path leaves download root"
                )
            resolved.append(
                _ResolvedAttachment(
                    file,
                    AttachmentInfo(
                        index,
                        filename,
                        _display_path(
                            self.display_root,
                            safe_category,
                            f"{exercise_id}-{safe_name}",
                            filename,
                        ),
                    ),
                    target,
                )
            )
        display_directory = _display_path(
            self.display_root, safe_category, f"{exercise_id}-{safe_name}"
        )
        return _ResolvedExercise(
            category,
            exercise_id,
            exercise_name,
            directory,
            display_directory,
            tuple(resolved),
        )

    def _attachment_infos(self, resolved: _ResolvedExercise) -> list[AttachmentInfo]:
        infos: list[AttachmentInfo] = []
        for attachment in resolved.attachments:
            info = attachment.info
            try:
                target = self._trusted_child(attachment.target)
                stat = target.stat()
                if not target.is_file():
                    info = AttachmentInfo(
                        info.index, info.name, info.path, error="本地附件不是普通文件"
                    )
                else:
                    info = AttachmentInfo(
                        info.index, info.name, info.path, True, stat.st_size
                    )
            except FileNotFoundError:
                known = self._known_size(attachment.file.url.strip())
                if known is not None:
                    info = AttachmentInfo(info.index, info.name, info.path, size=known)
            except StorageError:
                info = AttachmentInfo(
                    info.index, info.name, info.path, error="本地附件路径无效"
                )
            except OSError:
                info = AttachmentInfo(
                    info.index, info.name, info.path, error="本地附件检查失败"
                )
            infos.append(info)
        return infos

    def _exercises(self) -> list[ExerciseGroup]:
        if self.source is None:
            return []
        result = self.source.exercises(False)
        return result if isinstance(result, list) else list(result)

    def _exercise(self, exercise_id: int) -> ExerciseDetail:
        if self.source is None:
            raise ExerciseNotFound("exercise not found")
        attachment_detail = getattr(self.source, "attachment_detail", None)
        if callable(attachment_detail):
            result = attachment_detail(exercise_id, False)
            if isinstance(result, ExerciseDetail):
                return result
        return self.source.exercise(exercise_id, False)

    def _open(
        self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None
    ) -> Any:
        request = Request(url, headers=headers or {}, method=method)
        if self.http_client is not None and hasattr(self.http_client, "open"):
            return self.http_client.open(request, timeout=self.timeout)
        opener = build_opener(ProxyHandler(getproxies()))
        return opener.open(request, timeout=self.timeout)  # noqa: S310 - URL validated above.

    def _probe_url_size(self, url: str) -> int:
        response: Any | None = None
        try:
            response = self._open_metadata(url, method="HEAD")
            size = _content_length(response)
            if size >= 0:
                return size
        except (HTTPError, URLError, TimeoutError, OSError):
            pass
        finally:
            _close_quietly(response)
        response = None
        try:
            response = self._open_metadata(
                url, method="GET", headers={"Range": "bytes=0-0"}
            )
            return _response_total_size(response)
        except (HTTPError, URLError, TimeoutError, OSError):
            return -1
        finally:
            _close_quietly(response)

    def _open_metadata(
        self, url: str, *, method: str, headers: dict[str, str] | None = None
    ) -> Any:
        request = Request(url, headers=headers or {}, method=method)
        timeout = min(self.timeout, METADATA_TIMEOUT_SECONDS)
        if self.http_client is not None and hasattr(self.http_client, "open"):
            return self.http_client.open(request, timeout=timeout)
        opener = build_opener(ProxyHandler(getproxies()))
        return opener.open(request, timeout=timeout)  # noqa: S310 - URL validated above.

    def _known_size(self, url: str) -> int | None:
        with self._size_lock:
            return self._remote_sizes.get(url)

    def _remember_size(self, url: str, size: int) -> None:
        if size < 0:
            return
        with self._size_lock:
            self._remote_sizes[url] = size

    def _trusted_child(self, target: Path) -> Path:
        root = self.root.absolute()
        absolute = target.absolute()
        if absolute == root or not _within_root(root, absolute):
            raise StorageError("attachment storage failed: target leaves download root")
        relative = absolute.relative_to(root)
        canonical_root = Path(os.path.realpath(root))
        canonical_target = Path(os.path.realpath(absolute))
        expected = canonical_root / relative
        if canonical_target != expected:
            raise StorageError(
                "attachment storage failed: symbolic link redirects download path"
            )
        return absolute


def _validate_url(raw: str) -> str:
    parsed = urlsplit(raw.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidURL("invalid attachment URL")
    return raw.strip()


def attachment_filename(file: File, parsed: Any, index: int) -> str:
    name = file.name.strip()
    if not name and parsed is not None:
        name = unquote(Path(parsed.path).name)
    name = safe_segment(name, f"attachment-{index + 1}", 180)
    extension = safe_segment(file.ext.strip().lstrip("."), "", 24)
    if extension and not Path(name).suffix:
        name = f"{name}.{extension}"
    return name


def unique_attachment_filename(filename: str, index: int, used: set[str]) -> str:
    key = filename.lower()
    if key not in used:
        used.add(key)
        return filename
    suffix_number = index + 1
    extension = Path(filename).suffix
    stem = filename[: -len(extension)] if extension else filename
    while True:
        suffix = f"-{suffix_number}"
        candidate = f"{truncate_utf8(stem, 180 - len(extension) - len(suffix))}{suffix}{extension}"
        key = candidate.lower()
        if key not in used:
            used.add(key)
            return candidate
        suffix_number += 1


def safe_segment(raw: str, fallback: str, max_bytes: int) -> str:
    chars: list[str] = []
    for char in raw.strip():
        if unicodedata.category(char) == "Cc" or char in '<>:"/\\|?*':
            chars.append("_")
        else:
            chars.append(char)
    value = "".join(chars).strip(" .")
    while ".." in value:
        value = value.replace("..", "_")
    if not value or value in {".", ".."}:
        value = fallback
    return truncate_utf8(value, max_bytes)


def truncate_utf8(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _within_root(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _display_path(root: Path, *parts: str) -> str:
    return Path(root, *parts).as_posix()


def _content_length(response: Any) -> int:
    headers = getattr(response, "headers", {})
    if hasattr(headers, "get"):
        value = headers.get("Content-Length", "")
    elif hasattr(response, "getheader"):
        value = response.getheader("Content-Length", "")
    else:
        value = ""
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _response_status(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        value = getattr(response, "code", None)
    if value is None and hasattr(response, "getcode"):
        value = response.getcode()
    try:
        return int(value if value is not None else 200)
    except (TypeError, ValueError):
        return 200


def _response_total_size(response: Any) -> int:
    headers = getattr(response, "headers", {})
    content_range = headers.get("Content-Range", "") if hasattr(headers, "get") else ""
    if isinstance(content_range, str) and "/" in content_range:
        total = content_range.rsplit("/", 1)[-1].strip()
        if total != "*":
            try:
                return int(total)
            except ValueError:
                pass
    return _content_length(response)


def _close_quietly(response: Any) -> None:
    if response is not None and hasattr(response, "close"):
        try:
            response.close()
        except OSError:
            pass


def _detect_content_type(data: bytes, filename: str) -> str:
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
    if data.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data and b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return "text/plain; charset=utf-8"
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _remove_tree_without_following_symlinks(target: Path) -> None:
    """Remove a resolved exercise directory without traversing child links."""

    for child in target.iterdir():
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            _remove_tree_without_following_symlinks(child)
    target.rmdir()


Service = DownloadService
New = DownloadService
