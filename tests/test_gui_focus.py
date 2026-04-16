from pathlib import Path

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from sankaku_uploader.domain import TaskType
from sankaku_uploader.ui.main_window import MainWindow


def test_render_active_task_preserves_selection(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    window.repository.base_dir = tmp_path
    task = window.service.create_task("focus", TaskType.NORMAL_BATCH)

    paths = []
    for name in ["a.png", "b.png"]:
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    window.service.add_files(task.task_id, paths)
    window.active_task_id = task.task_id
    window._render_active_task()

    window.queue_list.setCurrentRow(1)
    selected_before = window.queue_list.currentItem().data(Qt.ItemDataRole.UserRole)
    window._render_active_task()
    selected_after = window.queue_list.currentItem().data(Qt.ItemDataRole.UserRole)

    assert selected_before == selected_after
