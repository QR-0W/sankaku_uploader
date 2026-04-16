from pathlib import Path

from sankaku_uploader.domain import Settings, TaskType, UploadTask
from sankaku_uploader.infrastructure.storage import JsonRepository


def test_repository_round_trip_tasks_and_settings(tmp_path: Path) -> None:
    repo = JsonRepository(base_dir=tmp_path)

    task = UploadTask(task_name="task", task_type=TaskType.NORMAL_BATCH)
    file_path = tmp_path / "a.png"
    file_path.write_text("x", encoding="utf-8")
    task.add_paths([file_path])
    repo.save_tasks([task])

    loaded = repo.load_tasks()
    assert len(loaded) == 1
    assert loaded[0].task_name == "task"
    assert loaded[0].items[0].file_name == "a.png"

    settings = Settings(upload_page_url="https://example.com/upload", headless=False, max_concurrent_pages=16)
    repo.save_settings(settings)
    loaded_settings = repo.load_settings()
    assert loaded_settings.upload_page_url == "https://example.com/upload"
    assert loaded_settings.headless is False
    assert loaded_settings.max_concurrent_pages == 16
