from __future__ import annotations

from pathlib import Path

from sankaku_uploader.domain import ItemStatus, TaskStatus, TaskType, UploadItem, UploadTask, can_transition_task
from sankaku_uploader.infrastructure.storage import JsonRepository


class TaskService:
    def __init__(self, repository: JsonRepository) -> None:
        self.repository = repository
        loaded_tasks = self.repository.load_tasks()
        
        normal_task = next((t for t in loaded_tasks if t.task_type == TaskType.NORMAL_BATCH), None)
        diff_task = next((t for t in loaded_tasks if t.task_type == TaskType.DIFF_GROUP), None)
        
        if normal_task is None:
            normal_task = UploadTask(task_name="普通模式队列", task_type=TaskType.NORMAL_BATCH)
        if diff_task is None:
            diff_task = UploadTask(task_name="差分模式队列", task_type=TaskType.DIFF_GROUP)
            
        for t in loaded_tasks:
            if t is not normal_task and t.task_type == TaskType.NORMAL_BATCH:
                normal_task.items.extend(t.items)
            elif t is not diff_task and t.task_type == TaskType.DIFF_GROUP:
                diff_task.items.extend(t.items)
                
        normal_task._normalize_indexes()
        diff_task._normalize_indexes()

        self.tasks: list[UploadTask] = [normal_task, diff_task]
        self._save()

    def list_tasks(self) -> list[UploadTask]:
        return list(self.tasks)

    def get_task(self, task_id: str) -> UploadTask:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise KeyError(f"task not found: {task_id}")

    def create_task(self, name: str, task_type: TaskType) -> UploadTask:
        raise NotImplementedError("Single queue per mode enfored, creating dynamic tasks is disabled.")

    def delete_task(self, task_id: str) -> None:
        raise NotImplementedError("Single queue per mode enforced, deleting core tasks is disabled.")

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

    def persist(self) -> None:
        self._save()

    def _save(self) -> None:
        self.repository.save_tasks(self.tasks)
