from __future__ import annotations

from .enums import ItemStatus, TaskStatus

_TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.RUNNING: {TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.PARTIAL_FAILED},
    TaskStatus.PAUSED: {TaskStatus.RUNNING, TaskStatus.FAILED},
    TaskStatus.PARTIAL_FAILED: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.COMPLETED},
    TaskStatus.FAILED: {TaskStatus.RUNNING},
    TaskStatus.COMPLETED: set(),
}

_ITEM_TRANSITIONS: dict[ItemStatus, set[ItemStatus]] = {
    ItemStatus.PENDING: {ItemStatus.UPLOADING, ItemStatus.FAILED},
    ItemStatus.UPLOADING: {ItemStatus.WAITING_TAGS, ItemStatus.FAILED},
    ItemStatus.WAITING_TAGS: {ItemStatus.WAITING_USER_CONFIRM, ItemStatus.SUBMITTING, ItemStatus.FAILED},
    ItemStatus.WAITING_USER_CONFIRM: {ItemStatus.SUBMITTING, ItemStatus.FAILED},
    ItemStatus.SUBMITTING: {ItemStatus.SUCCESS, ItemStatus.FAILED},
    ItemStatus.SUCCESS: set(),
    ItemStatus.FAILED: {ItemStatus.PENDING, ItemStatus.UPLOADING},
}


def can_transition_task(current: TaskStatus, target: TaskStatus) -> bool:
    return target in _TASK_TRANSITIONS.get(current, set())


def can_transition_item(current: ItemStatus, target: ItemStatus) -> bool:
    return target in _ITEM_TRANSITIONS.get(current, set())
