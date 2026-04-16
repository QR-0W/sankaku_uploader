from __future__ import annotations

from pathlib import Path

from sankaku_uploader.domain import ItemStatus, TaskStatus, TaskType, UploadItem, UploadTask, can_transition_task
from sankaku_uploader.infrastructure.storage import JsonRepository


class TaskService:
    def __init__(self, repository: JsonRepository) -> None:
        self.repository = repository
        self.tasks: list[UploadTask] = self.repository.load_tasks()

    def list_tasks(self) -> list[UploadTask]:
        return list(self.tasks)

    def get_task(self, task_id: str) -> UploadTask:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"task not found: {task_id}")

    def create_task(self, name: str, task_type: TaskType) -> UploadTask:
        task = UploadTask(task_name=name.strip() or "Untitled Task", task_type=task_type)
        self.tasks.insert(0, task)
        self._save()
        return task

    def delete_task(self, task_id: str) -> None:
        self.tasks = [task for task in self.tasks if task.task_id != task_id]
        self._save()

    def add_files(self, task_id: str, paths: list[Path]) -> list[UploadItem]:
        task = self.get_task(task_id)
        valid_paths = [path for path in paths if path.exists() and path.is_file()]
        new_items = task.add_paths(valid_paths)
        self._save()
        return new_items

    def reorder_items(self, task_id: str, ordered_item_ids: list[str]) -> None:
        task = self.get_task(task_id)
        task.reorder(ordered_item_ids)
        self._save()

    def remove_item(self, task_id: str, item_id: str) -> None:
        task = self.get_task(task_id)
        task.remove_item(item_id)
        self._save()

    def clear_items(self, task_id: str) -> None:
        task = self.get_task(task_id)
        task.items.clear()
        task.root_post_id = ""
        task.touch()
        self._save()

    def retry_failed_items(self, task_id: str) -> int:
        task = self.get_task(task_id)
        retried = task.retry_failed_items()
        if retried > 0 and task.status in {TaskStatus.FAILED, TaskStatus.PARTIAL_FAILED}:
            task.set_status(TaskStatus.PENDING)
        self._save()
        return retried

    def set_task_status(self, task_id: str, target: TaskStatus, *, force: bool = False) -> None:
        task = self.get_task(task_id)
        if force or task.status == target:
            task.set_status(target)
            self._save()
            return
        if not can_transition_task(task.status, target):
            raise ValueError(f"invalid task transition: {task.status.value} -> {target.value}")
        task.set_status(target)
        self._save()

    def update_item_result(
        self,
        task_id: str,
        item_id: str,
        *,
        status: ItemStatus,
        detected_tags: list[str] | None = None,
        final_tags: list[str] | None = None,
        post_id: str = "",
        error: str = "",
    ) -> None:
        task = self.get_task(task_id)
        item = self._get_item(task, item_id)
        item.set_status(status, error=error)
        if detected_tags is not None:
            item.detected_tags = list(detected_tags)
        if final_tags is not None:
            item.final_tags = list(final_tags)
            item.final_tags_locked = False
        if post_id:
            item.created_post_id = post_id
            if task.task_type is TaskType.DIFF_GROUP and item.order_index == 0:
                task.set_root_post_id(post_id)
        task.touch()
        self._save()

    def update_item_tags(self, task_id: str, item_id: str, final_tags: list[str]) -> None:
        task = self.get_task(task_id)
        item = self._get_item(task, item_id)
        item.final_tags = list(final_tags)
        item.final_tags_locked = True
        item.touch()
        task.touch()
        self._save()

    @staticmethod
    def _get_item(task: UploadTask, item_id: str) -> UploadItem:
        for item in task.items:
            if item.item_id == item_id:
                return item
        raise KeyError(f"item not found: {item_id}")

    def _save(self) -> None:
        self.repository.save_tasks(self.tasks)

    def persist(self) -> None:
        self._save()
