from pathlib import Path
from pathlib import Path

from sankaku_uploader.application import TaskService
from sankaku_uploader.domain import ItemStatus, TaskStatus, TaskType
from sankaku_uploader.infrastructure.storage import JsonRepository


def test_task_service_add_reorder_and_update(tmp_path: Path) -> None:
    repo = JsonRepository(base_dir=tmp_path)
    service = TaskService(repo)

    task = next(t for t in service.list_tasks() if t.task_type == TaskType.DIFF_GROUP)
    files = [tmp_path / "a.png", tmp_path / "b.png"]
    for file in files:
        file.write_text("x", encoding="utf-8")

    added = service.add_files(task.task_id, files)
    assert len(added) == 2

    service.reorder_items(task.task_id, [added[1].item_id, added[0].item_id])
    reordered = service.get_task(task.task_id)
    assert reordered.items[0].item_id == added[1].item_id

    service.update_item_result(task.task_id, reordered.items[0].item_id, status=ItemStatus.SUCCESS, post_id="abc")
    updated = service.get_task(task.task_id)
    assert updated.root_post_id == "abc"
    assert updated.items[1].parent_post_id == "abc"


def test_retry_failed_items_switches_task_to_pending(tmp_path: Path) -> None:
    repo = JsonRepository(base_dir=tmp_path)
    service = TaskService(repo)

    task = next(t for t in service.list_tasks() if t.task_type == TaskType.NORMAL_BATCH)
    file = tmp_path / "a.png"
    file.write_text("x", encoding="utf-8")
    service.add_files(task.task_id, [file])
    service.update_item_result(task.task_id, service.get_task(task.task_id).items[0].item_id, status=ItemStatus.FAILED, error="x")
    service.set_task_status(task.task_id, TaskStatus.FAILED, force=True)

    retried = service.retry_failed_items(task.task_id)
    assert retried == 1
    assert service.get_task(task.task_id).status is TaskStatus.PENDING


def test_update_item_tags(tmp_path: Path) -> None:
    repo = JsonRepository(base_dir=tmp_path)
    service = TaskService(repo)
    task = next(t for t in service.list_tasks() if t.task_type == TaskType.NORMAL_BATCH)
    file = tmp_path / "a.png"
    file.write_text("x", encoding="utf-8")
    added = service.add_files(task.task_id, [file])

    service.update_item_tags(task.task_id, added[0].item_id, ["1girl", "smile"])
    loaded = service.get_task(task.task_id)
    assert loaded.items[0].final_tags == ["1girl", "smile"]
    assert loaded.items[0].final_tags_locked is True


def test_update_item_tags_allows_manual_clear(tmp_path: Path) -> None:
    repo = JsonRepository(base_dir=tmp_path)
    service = TaskService(repo)
    task = next(t for t in service.list_tasks() if t.task_type == TaskType.NORMAL_BATCH)
    file = tmp_path / "a.png"
    file.write_text("x", encoding="utf-8")
    added = service.add_files(task.task_id, [file])

    service.update_item_tags(task.task_id, added[0].item_id, [])
    loaded = service.get_task(task.task_id)
    assert loaded.items[0].final_tags == []
    assert loaded.items[0].final_tags_locked is True


def test_set_author_tags_persists_on_diff_task(tmp_path: Path) -> None:
    repo = JsonRepository(base_dir=tmp_path)
    service = TaskService(repo)
    task = next(t for t in service.list_tasks() if t.task_type == TaskType.DIFF_GROUP)

    service.set_author_tags(task.task_id, ["artist_id_123", "source_x"])

    loaded = service.get_task(task.task_id)
    assert loaded.author_tags == ["artist_id_123", "source_x"]
