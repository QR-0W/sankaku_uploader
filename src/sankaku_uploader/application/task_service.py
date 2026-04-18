from __future__ import annotations

from pathlib import Path

from sankaku_uploader.domain import ItemStatus, TaskStatus, TaskType, UploadItem, UploadTask, can_transition_task
from sankaku_uploader.infrastructure.storage import JsonRepository


class TaskService:
    def __init__(self, repository: JsonRepository) -> None:
        self.repository = repository
        loaded_tasks = self.repository.load_tasks()

        # Migrate from legacy two-fixed-task format: accept any number of tasks now.
        if not loaded_tasks:
            # Bootstrap with one default task if there's nothing saved at all
            loaded_tasks = [UploadTask(task_name="普通队列 1", task_type=TaskType.NORMAL_BATCH)]

        self.tasks: list[UploadTask] = loaded_tasks
        self._save()

    def list_tasks(self) -> list[UploadTask]:
        return list(self.tasks)

    def get_task(self, task_id: str) -> UploadTask:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"task not found: {task_id}")

    def create_task(self, name: str, task_type: TaskType) -> UploadTask:
        task = UploadTask(task_name=name, task_type=task_type)
        self.tasks.append(task)
        self._save()
        return task

    def delete_task(self, task_id: str) -> None:
        if len(self.tasks) <= 1:
            raise ValueError("至少保留一个队列")
        self.tasks = [t for t in self.tasks if t.task_id != task_id]
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
        task.remove(item_id)
        self._save()

    def clear_items(self, task_id: str) -> None:
        task = self.get_task(task_id)
        task.items.clear()
        task.root_post_id = ""
        self._save()

    def set_task_status(self, task_id: str, status: TaskStatus, force: bool = False) -> None:
        task = self.get_task(task_id)
        if not force and not can_transition_task(task.status, status):
            raise ValueError(f"invalid task transition: {task.status.value} -> {status.value}")
        task.set_status(status)
        self._save()

    def update_item_result(
        self,
        task_id: str,
        item_id: str,
        status: ItemStatus | None = None,
        post_id: str | None = None,
        error: str | None = None,
        detected_tags: list[str] | None = None,
        final_tags: list[str] | None = None,
    ) -> None:
        task = self.get_task(task_id)
        for item in task.items:
            if item.item_id != item_id:
                continue
            if status is not None:
                item.set_status(status)
            if post_id is not None:
                item.created_post_id = post_id
                if task.task_type == TaskType.DIFF_GROUP and item.order_index == 0:
                    task.set_root_post_id(post_id)
            if error is not None:
                item.error_message = error
            if detected_tags is not None:
                item.detected_tags = list(detected_tags)
            if final_tags is not None:
                item.final_tags = list(final_tags)
            break
        self._save()

    def update_item_tags(self, task_id: str, item_id: str, tags: list[str]) -> None:
        task = self.get_task(task_id)
        for item in task.items:
            if item.item_id == item_id:
                item.final_tags = list(tags)
                item.final_tags_locked = True
                self._save()
                return

    def set_manual_root_post_id(self, task_id: str, post_id: str) -> None:
        task = self.get_task(task_id)
        task.manual_root_post_id = post_id.strip()
        self._save()

    def retry_failed_items(self, task_id: str) -> int:
        task = self.get_task(task_id)
        count = 0
        for item in task.items:
            if item.status in (ItemStatus.FAILED, ItemStatus.TAG_ERROR, ItemStatus.DUPLICATE):
                item.set_status(ItemStatus.PENDING)
                item.error_message = ""
                count += 1
        if count > 0:
            task.set_status(TaskStatus.PENDING)
            self._save()
        return count

    def rename_task(self, task_id: str, new_name: str) -> None:
        task = self.get_task(task_id)
        task.task_name = new_name.strip()
        self._save()

    def persist(self) -> None:
        self._save()

    def _save(self) -> None:
        self.repository.save_tasks(self.tasks)
