from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import uuid

from .enums import FileType, ItemStatus, ReviewMode, TaskStatus, TaskType

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def infer_file_type(path: Path) -> FileType:
    ext = path.suffix.lower()
    if ext in _VIDEO_EXTENSIONS:
        return FileType.VIDEO
    return FileType.IMAGE


@dataclass(slots=True)
class UploadItem:
    task_id: str
    file_path: str
    file_name: str
    file_type: FileType
    order_index: int
    status: ItemStatus = ItemStatus.PENDING
    detected_tags: list[str] = field(default_factory=list)
    final_tags: list[str] = field(default_factory=list)
    final_tags_locked: bool = False
    parent_post_id: str = ""
    created_post_id: str = ""
    error_message: str = ""
    item_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def from_path(cls, task_id: str, path: Path, order_index: int) -> "UploadItem":
        return cls(
            task_id=task_id,
            file_path=str(path),
            file_name=path.name,
            file_type=infer_file_type(path),
            order_index=order_index,
        )

    @property
    def path(self) -> Path:
        return Path(self.file_path)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def set_status(self, status: ItemStatus, error: str = "") -> None:
        self.status = status
        self.error_message = error
        self.touch()

    def validate(self) -> None:
        if not self.file_path:
            raise ValueError("file_path is required")
        if not self.file_name:
            raise ValueError("file_name is required")
        if self.order_index < 0:
            raise ValueError("order_index must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "task_id": self.task_id,
            "file_path": self.file_path,
            "file_name": self.file_name,
            "file_type": self.file_type.value,
            "order_index": self.order_index,
            "status": self.status.value,
            "detected_tags": list(self.detected_tags),
            "final_tags": list(self.final_tags),
            "final_tags_locked": self.final_tags_locked,
            "parent_post_id": self.parent_post_id,
            "created_post_id": self.created_post_id,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UploadItem":
        return cls(
            item_id=str(data.get("item_id") or uuid.uuid4().hex),
            task_id=str(data.get("task_id") or ""),
            file_path=str(data.get("file_path") or ""),
            file_name=str(data.get("file_name") or ""),
            file_type=FileType(str(data.get("file_type") or FileType.IMAGE.value)),
            order_index=int(data.get("order_index", 0)),
            status=ItemStatus(str(data.get("status") or ItemStatus.PENDING.value)),
            detected_tags=list(data.get("detected_tags") or []),
            final_tags=list(data.get("final_tags") or []),
            final_tags_locked=bool(data.get("final_tags_locked", False)),
            parent_post_id=str(data.get("parent_post_id") or ""),
            created_post_id=str(data.get("created_post_id") or ""),
            error_message=str(data.get("error_message") or ""),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )


@dataclass(slots=True)
class TaskRuntimeState:
    task_id: str
    current_item_id: str = ""
    current_step: str = "idle"
    last_error: str = ""
    is_paused: bool = False
    last_saved_at: str = field(default_factory=utc_now_iso)

    def touch(self) -> None:
        self.last_saved_at = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "current_item_id": self.current_item_id,
            "current_step": self.current_step,
            "last_error": self.last_error,
            "is_paused": self.is_paused,
            "last_saved_at": self.last_saved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRuntimeState":
        return cls(
            task_id=str(data.get("task_id") or ""),
            current_item_id=str(data.get("current_item_id") or ""),
            current_step=str(data.get("current_step") or "idle"),
            last_error=str(data.get("last_error") or ""),
            is_paused=bool(data.get("is_paused", False)),
            last_saved_at=str(data.get("last_saved_at") or utc_now_iso()),
        )


@dataclass(slots=True)
class UploadTask:
    task_name: str
    task_type: TaskType
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: TaskStatus = TaskStatus.PENDING
    root_post_id: str = ""
    manual_root_post_id: str = ""  # user-supplied parent post ID for diff mode
    author_tags: list[str] = field(default_factory=list)
    items: list[UploadItem] = field(default_factory=list)
    runtime: TaskRuntimeState | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if self.runtime is None:
            self.runtime = TaskRuntimeState(task_id=self.task_id)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()
        if self.runtime is not None:
            self.runtime.touch()

    def set_status(self, status: TaskStatus) -> None:
        self.status = status
        self.touch()

    def add_paths(self, paths: list[Path]) -> list[UploadItem]:
        next_index = len(self.items)
        new_items: list[UploadItem] = []
        for path in paths:
            item = UploadItem.from_path(self.task_id, path, next_index)
            next_index += 1
            new_items.append(item)
        self.items.extend(new_items)
        self._normalize_indexes()
        self._sync_diff_relationships()
        self.touch()
        return new_items

    def remove_item(self, item_id: str) -> None:
        self.items = [item for item in self.items if item.item_id != item_id]
        self._normalize_indexes()
        self._sync_diff_relationships()
        self.touch()

    def reorder(self, ordered_item_ids: list[str]) -> None:
        lookup = {item.item_id: item for item in self.items}
        reordered: list[UploadItem] = []
        for item_id in ordered_item_ids:
            item = lookup.pop(item_id, None)
            if item is not None:
                reordered.append(item)
        reordered.extend(lookup.values())
        self.items = reordered
        self._normalize_indexes()
        self._sync_diff_relationships()
        self.touch()

    def retry_failed_items(self) -> int:
        retried = 0
        for item in self.items:
            if item.status is ItemStatus.FAILED:
                item.set_status(ItemStatus.PENDING)
                item.error_message = ""
                retried += 1
        if retried:
            self.touch()
        return retried

    def pending_items(self) -> list[UploadItem]:
        return [item for item in sorted(self.items, key=lambda x: x.order_index) if item.status in {ItemStatus.PENDING, ItemStatus.FAILED}]

    def validate(self) -> None:
        if not self.task_name.strip():
            raise ValueError("task_name is required")
        if self.task_type is TaskType.DIFF_GROUP and len(self.items) < 2:
            raise ValueError("diff_group task requires at least 2 items")
        for item in self.items:
            item.validate()
        if self.task_type is TaskType.DIFF_GROUP:
            self._validate_diff_group()

    def _validate_diff_group(self) -> None:
        if not self.items:
            raise ValueError("diff_group task has no items")
        for item in self.items[1:]:
            if item.parent_post_id and self.root_post_id and item.parent_post_id != self.root_post_id:
                raise ValueError("diff child parent_post_id must equal root_post_id")

    def _normalize_indexes(self) -> None:
        for idx, item in enumerate(self.items):
            item.order_index = idx
            item.touch()

    def _sync_diff_relationships(self) -> None:
        if self.task_type is not TaskType.DIFF_GROUP:
            for item in self.items:
                item.parent_post_id = ""
            return

        if not self.items:
            self.root_post_id = ""
            return

        # 主贴是 order_index=0，不应有 parent_post_id
        self.items[0].parent_post_id = ""
        root_id = self.root_post_id.strip()
        for item in self.items[1:]:
            item.parent_post_id = root_id

    def set_root_post_id(self, post_id: str) -> None:
        self.root_post_id = post_id.strip()
        self._sync_diff_relationships()
        self.touch()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "task_type": self.task_type.value,
            "status": self.status.value,
            "root_post_id": self.root_post_id,
            "manual_root_post_id": self.manual_root_post_id,
            "author_tags": list(self.author_tags),
            "items": [item.to_dict() for item in self.items],
            "runtime": self.runtime.to_dict() if self.runtime else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UploadTask":
        task = cls(
            task_id=str(data.get("task_id") or uuid.uuid4().hex),
            task_name=str(data.get("task_name") or "Untitled task"),
            task_type=TaskType(str(data.get("task_type") or TaskType.NORMAL_BATCH.value)),
            status=TaskStatus(str(data.get("status") or TaskStatus.PENDING.value)),
            root_post_id=str(data.get("root_post_id") or ""),
            manual_root_post_id=str(data.get("manual_root_post_id") or ""),
            author_tags=list(data.get("author_tags") or []),
            items=[UploadItem.from_dict(item) for item in data.get("items") or []],
            runtime=TaskRuntimeState.from_dict(data["runtime"]) if data.get("runtime") else None,
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
        )
        task._normalize_indexes()
        task._sync_diff_relationships()
        return task


@dataclass(slots=True)
class Settings:
    site_url: str = "https://www.sankakucomplex.com"
    upload_page_url: str = "https://www.sankakucomplex.com/en/posts/upload"
    default_task_mode: TaskType = TaskType.NORMAL_BATCH
    retry_count: int = 1
    auto_save_interval: int = 10
    review_mode: ReviewMode = ReviewMode.MANUAL_REVIEW
    browser_channel: str = "msedge"
    profile_dir: str = str(Path.home() / ".sankaku-uploader" / "profile")
    headless: bool = True
    max_concurrent_pages: int = 8
    proxy_server: str = ""
    ui_preferences: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_url": self.site_url,
            "upload_page_url": self.upload_page_url,
            "default_task_mode": self.default_task_mode.value,
            "retry_count": self.retry_count,
            "auto_save_interval": self.auto_save_interval,
            "review_mode": self.review_mode.value,
            "browser_channel": self.browser_channel,
            "profile_dir": self.profile_dir,
            "headless": self.headless,
            "max_concurrent_pages": self.max_concurrent_pages,
            "proxy_server": self.proxy_server,
            "ui_preferences": self.ui_preferences,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        return cls(
            site_url=str(data.get("site_url") or "https://www.sankakucomplex.com"),
            upload_page_url=str(data.get("upload_page_url") or "https://www.sankakucomplex.com/zh-CN/posts/upload"),
            default_task_mode=TaskType(str(data.get("default_task_mode") or TaskType.NORMAL_BATCH.value)),
            retry_count=max(int(data.get("retry_count", 1)), 0),
            auto_save_interval=max(int(data.get("auto_save_interval", 10)), 1),
            review_mode=ReviewMode(str(data.get("review_mode") or ReviewMode.MANUAL_REVIEW.value)),
            browser_channel=str(data.get("browser_channel") or "msedge"),
            profile_dir=str(data.get("profile_dir") or str(Path.home() / ".sankaku-uploader" / "profile")),
            headless=bool(data.get("headless", True)),
            max_concurrent_pages=max(int(data.get("max_concurrent_pages", 8)), 1),
            proxy_server=str(data.get("proxy_server") or ""),
            ui_preferences=dict(data.get("ui_preferences") or {}),
        )
