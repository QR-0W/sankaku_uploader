from pathlib import Path

import pytest

from sankaku_uploader.domain import ItemStatus, TaskType, UploadTask


def test_diff_group_reorder_keeps_first_item_as_root_candidate(tmp_path: Path) -> None:
    task = UploadTask(task_name="diff", task_type=TaskType.DIFF_GROUP)
    paths = [tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"]
    for path in paths:
        path.write_text("x", encoding="utf-8")
    task.add_paths(paths)

    original_second = task.items[1].item_id
    task.reorder([task.items[1].item_id, task.items[0].item_id, task.items[2].item_id])

    assert task.items[0].item_id == original_second
    assert task.items[0].parent_post_id == ""
    assert task.items[1].parent_post_id == ""


def test_diff_group_set_root_sets_children_parent(tmp_path: Path) -> None:
    task = UploadTask(task_name="diff", task_type=TaskType.DIFF_GROUP)
    paths = [tmp_path / "a.png", tmp_path / "b.png"]
    for path in paths:
        path.write_text("x", encoding="utf-8")
    task.add_paths(paths)

    task.set_root_post_id("12345")

    assert task.root_post_id == "12345"
    assert task.items[0].parent_post_id == ""
    assert task.items[1].parent_post_id == "12345"


def test_diff_group_requires_at_least_two_items(tmp_path: Path) -> None:
    task = UploadTask(task_name="diff", task_type=TaskType.DIFF_GROUP)
    only = tmp_path / "a.png"
    only.write_text("x", encoding="utf-8")
    task.add_paths([only])

    with pytest.raises(ValueError):
        task.validate()


def test_retry_failed_items_reset_to_pending(tmp_path: Path) -> None:
    task = UploadTask(task_name="normal", task_type=TaskType.NORMAL_BATCH)
    path = tmp_path / "a.png"
    path.write_text("x", encoding="utf-8")
    task.add_paths([path])
    task.items[0].set_status(ItemStatus.FAILED, error="boom")

    assert task.retry_failed_items() == 1
    assert task.items[0].status is ItemStatus.PENDING
    assert task.items[0].error_message == ""


def test_author_tags_round_trip_in_task_serialization() -> None:
    task = UploadTask(task_name="diff", task_type=TaskType.DIFF_GROUP, author_tags=["artist_id_1", "artist_id_2"])

    restored = UploadTask.from_dict(task.to_dict())

    assert restored.author_tags == ["artist_id_1", "artist_id_2"]
