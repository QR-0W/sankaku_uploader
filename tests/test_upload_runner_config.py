from multiprocessing import Queue
from pathlib import Path
from types import SimpleNamespace

from sankaku_uploader.application import upload_runner
from sankaku_uploader.domain import ReviewMode, Settings, TaskType, UploadTask


def _build_task(tmp_path: Path) -> UploadTask:
    task = UploadTask(task_name="demo", task_type=TaskType.NORMAL_BATCH)
    media = tmp_path / "a.png"
    media.write_text("x", encoding="utf-8")
    task.add_paths([media])
    return task


def test_run_upload_task_uses_auto_submit_and_headless(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class DummyClient:
        def __init__(self, config, review_decision_provider=None):
            captured["config"] = config
            captured["provider"] = review_decision_provider

        def upload_items(self, items, *, diff_mode=False):
            return [
                SimpleNamespace(
                    item_id=item.item_id,
                    success=True,
                    tag_state="ok",
                    ai_tags=["tag-a"],
                    post_id="abc",
                    uploaded_url="https://www.sankakucomplex.com/posts/abc",
                    error="",
                )
                for item in items
            ]

    monkeypatch.setattr(upload_runner, "SankakuAutomationClient", DummyClient)

    task = _build_task(tmp_path)
    settings = Settings(
        upload_page_url="https://example.com/upload",
        profile_dir=str(tmp_path / "profile"),
        review_mode=ReviewMode.QUICK_PASS,
        headless=True,
    )
    out_queue: Queue[str] = Queue()
    cmd_queue: Queue[str] = Queue()

    upload_runner._run_upload_task(task.to_dict(), settings.to_dict(), out_queue, cmd_queue)

    cfg = captured["config"]
    assert cfg.run_mode == "auto_submit"
    assert cfg.headless is True
    assert captured["provider"] is None


def test_run_upload_task_manual_review_keeps_provider(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class DummyClient:
        def __init__(self, config, review_decision_provider=None):
            captured["config"] = config
            captured["provider"] = review_decision_provider

        def upload_items(self, items, *, diff_mode=False):
            return [
                SimpleNamespace(
                    item_id=item.item_id,
                    success=True,
                    tag_state="ok",
                    ai_tags=["tag-a"],
                    post_id="abc",
                    uploaded_url="https://www.sankakucomplex.com/posts/abc",
                    error="",
                )
                for item in items
            ]

    monkeypatch.setattr(upload_runner, "SankakuAutomationClient", DummyClient)

    task = _build_task(tmp_path)
    settings = Settings(
        upload_page_url="https://example.com/upload",
        profile_dir=str(tmp_path / "profile"),
        review_mode=ReviewMode.MANUAL_REVIEW,
        headless=True,
    )
    out_queue: Queue[str] = Queue()
    cmd_queue: Queue[str] = Queue()

    upload_runner._run_upload_task(task.to_dict(), settings.to_dict(), out_queue, cmd_queue)

    cfg = captured["config"]
    assert cfg.run_mode == "auto_submit"
    assert cfg.headless is True
    assert callable(captured["provider"])
