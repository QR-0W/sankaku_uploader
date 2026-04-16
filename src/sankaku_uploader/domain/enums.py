from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    NORMAL_BATCH = "normal_batch"
    DIFF_GROUP = "diff_group"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL_FAILED = "partial_failed"


class ItemStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    WAITING_TAGS = "waiting_tags"
    WAITING_USER_CONFIRM = "waiting_user_confirm"
    SUBMITTING = "submitting"
    SUCCESS = "success"
    FAILED = "failed"
    TAG_ERROR = "tag_error"
    DUPLICATE = "duplicate"


class FileType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class ReviewMode(str, Enum):
    MANUAL_REVIEW = "manual_review"
    QUICK_PASS = "quick_pass"
