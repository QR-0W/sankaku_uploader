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
        def __init__(self, config, review_decision_provider=None, trace_hook=None):
            captured["config"] = config
            captured["provider"] = review_decision_provider
            captured["trace_hook"] = trace_hook

        def upload_items(self, items, *, diff_mode=False, manual_root_post_id="", item_result_callback=None):
            results = [
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
            if item_result_callback:
                for r in results:
                    item_result_callback(r)
            return results

    monkeypatch.setattr(upload_runner, "SankakuAutomationClient", DummyClient)

    task = _build_task(tmp_path)
    settings = Settings(
        upload_page_url="https://example.com/upload",
        profile_dir=str(tmp_path / "profile"),
        review_mode=ReviewMode.QUICK_PASS,
        headless=True,
        max_concurrent_pages=16,
    )
    out_queue: Queue[str] = Queue()
    cmd_queue: Queue[str] = Queue()

    upload_runner._run_upload_task(task.to_dict(), settings.to_dict(), out_queue, cmd_queue)

    cfg = captured["config"]
    assert cfg.run_mode == "auto_submit"
    assert cfg.headless is True
    assert cfg.max_concurrent_pages == 16
    assert captured["provider"] is None
    assert callable(captured["trace_hook"])


def test_run_upload_task_manual_review_keeps_provider(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class DummyClient:
        def __init__(self, config, review_decision_provider=None, trace_hook=None):
            captured["config"] = config
            captured["provider"] = review_decision_provider
            captured["trace_hook"] = trace_hook

        def upload_items(self, items, *, diff_mode=False, manual_root_post_id="", item_result_callback=None):
            results = [
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
            if item_result_callback:
                for r in results:
                    item_result_callback(r)
            return results

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
    assert callable(captured["trace_hook"])


def test_upload_runner_controller_can_send_tag_sync() -> None:
    controller = upload_runner.UploadRunnerController()
    controller.send_tag_sync("item-1", ["a", "b"])
    raw = controller.commands.get(timeout=1)
    event = upload_runner.WorkerEvent.from_json(raw)
    assert event.kind == "tag_sync"
    assert event.payload["item_id"] == "item-1"
    assert event.payload["tags"] == ["a", "b"]


def test_manual_review_provider_preserves_commands_for_other_items(monkeypatch, tmp_path: Path) -> None:
    decisions: list[tuple[str, str | None]] = []

    class DummyClient:
        def __init__(self, config, review_decision_provider=None, trace_hook=None):
            self.review_decision_provider = review_decision_provider

        def upload_items(self, items, *, diff_mode=False, manual_root_post_id="", item_result_callback=None):
            first = self.review_decision_provider(items[0], ["first"], True)
            second = self.review_decision_provider(items[1], ["second"], True)
            decisions.append((items[0].item_id, None if first is None else first.action))
            decisions.append((items[1].item_id, None if second is None else second.action))
            results = [
                SimpleNamespace(
                    item_id=item.item_id,
                    success=True,
                    tag_state="ok",
                    ai_tags=[],
                    post_id=f"post{i}",
                    uploaded_url=f"https://www.sankakucomplex.com/posts/post{i}",
                    error="",
                )
                for i, item in enumerate(items)
            ]
            if item_result_callback:
                for r in results:
                    item_result_callback(r)
            return results

    monkeypatch.setattr(upload_runner, "SankakuAutomationClient", DummyClient)

    task = UploadTask(task_name="two", task_type=TaskType.NORMAL_BATCH)
    for name in ["a.png", "b.png"]:
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        task.add_paths([path])
    settings = Settings(
        upload_page_url="https://example.com/upload",
        profile_dir=str(tmp_path / "profile"),
        review_mode=ReviewMode.MANUAL_REVIEW,
    )
    out_queue: Queue[str] = Queue()
    cmd_queue: Queue[str] = Queue()
    second_item_id = task.items[1].item_id
    cmd_queue.put(upload_runner.WorkerEvent("decision", {"item_id": second_item_id, "action": "confirm"}).to_json())

    upload_runner._run_upload_task(task.to_dict(), settings.to_dict(), out_queue, cmd_queue)

    assert decisions == [(task.items[0].item_id, None), (task.items[1].item_id, "confirm")]
