from __future__ import annotations

from dataclasses import dataclass
from multiprocessing import Process, Queue
from pathlib import Path
from queue import Empty
from typing import Any
import json
import time

from sankaku_uploader.domain import Settings, TaskType, UploadTask
from sankaku_uploader.infrastructure.automation import AutomationConfig, SankakuAutomationClient


@dataclass(slots=True)
class WorkerEvent:
    kind: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"kind": self.kind, "payload": self.payload}, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "WorkerEvent":
        data = json.loads(raw)
        return cls(kind=str(data.get("kind") or ""), payload=dict(data.get("payload") or {}))


def _run_upload_task(task_payload: dict[str, Any], settings_payload: dict[str, Any], out_queue: Queue, cmd_queue: Queue) -> None:
    task = UploadTask.from_dict(task_payload)
    settings = Settings.from_dict(settings_payload)

    def emit(kind: str, payload: dict[str, Any]) -> None:
        out_queue.put(WorkerEvent(kind, payload).to_json())

    def review_provider(item, tags, available):
        emit(
            "item_review",
            {
                "task_id": task.task_id,
                "item_id": item.item_id,
                "file_name": item.file_name,
                "ai_tags": list(tags),
                "tag_available": available,
            },
        )
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            try:
                raw = cmd_queue.get(timeout=0.25)
            except Exception:
                continue
            command = WorkerEvent.from_json(raw)
            if command.kind != "decision":
                continue
            if command.payload.get("item_id") != item.item_id:
                continue
            action = str(command.payload.get("action") or "").strip().lower()
            if action in {"confirm", "skip", "retry"}:
                return action
        return "skip"

    emit("task_started", {"task_id": task.task_id, "task_name": task.task_name, "task_type": task.task_type.value})

    needs_manual_review = settings.review_mode.value == "manual_review"

    client = SankakuAutomationClient(
        AutomationConfig(
            upload_url=settings.upload_page_url,
            profile_dir=Path(settings.profile_dir),
            browser_channel=settings.browser_channel,
            headless=settings.headless,
            run_mode="auto_submit",
        ),
        review_decision_provider=review_provider if needs_manual_review else None,
    )

    pending_items = task.pending_items()
    if not pending_items:
        emit("task_complete", {"task_id": task.task_id, "success": True, "results": []})
        return

    for item in pending_items:
        emit(
            "item_status",
            {
                "task_id": task.task_id,
                "item_id": item.item_id,
                "file_name": item.file_name,
                "status": "uploading",
            },
        )

    results = client.upload_items(pending_items, diff_mode=task.task_type is TaskType.DIFF_GROUP)

    has_failures = False
    for result in results:
        if not result.success:
            has_failures = True
        emit(
            "item_result",
            {
                "task_id": task.task_id,
                "item_id": result.item_id,
                "success": result.success,
                "tag_state": result.tag_state,
                "ai_tags": result.ai_tags,
                "post_id": result.post_id,
                "uploaded_url": result.uploaded_url,
                "error": result.error,
            },
        )

    emit(
        "task_complete",
        {
            "task_id": task.task_id,
            "success": not has_failures,
            "has_failures": has_failures,
            "results": [
                {
                    "item_id": result.item_id,
                    "success": result.success,
                    "tag_state": result.tag_state,
                    "post_id": result.post_id,
                    "error": result.error,
                }
                for result in results
            ],
        },
    )


class UploadRunnerController:
    def __init__(self) -> None:
        self.process: Process | None = None
        self.messages: Queue[str] = Queue()
        self.commands: Queue[str] = Queue()

    def start(self, task: UploadTask, settings: Settings) -> None:
        if self.process and self.process.is_alive():
            raise RuntimeError("upload runner is already active")
        self.process = Process(
            target=_run_upload_task,
            args=(task.to_dict(), settings.to_dict(), self.messages, self.commands),
            daemon=True,
        )
        self.process.start()

    def is_running(self) -> bool:
        return bool(self.process and self.process.is_alive())

    def send_decision(self, item_id: str, action: str) -> None:
        self.commands.put(WorkerEvent("decision", {"item_id": item_id, "action": action}).to_json())

    def poll(self) -> list[WorkerEvent]:
        events: list[WorkerEvent] = []
        while True:
            try:
                raw = self.messages.get_nowait()
            except Empty:
                break
            events.append(WorkerEvent.from_json(raw))
        return events

    def stop(self) -> None:
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=5)
