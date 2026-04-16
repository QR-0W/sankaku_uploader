from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
import html

from sankaku_uploader.application import TaskService, UploadRunnerController
from sankaku_uploader.domain import ItemStatus, ReviewMode, Settings, TaskStatus, TaskType, UploadTask
from sankaku_uploader.infrastructure.storage import JsonRepository


_TELEGRAM_STYLE = """
QMainWindow, QWidget {
  background: #18222d;
  color: #eaf2ff;
  font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
  font-size: 13px;
}
QLabel {
  color: #d8e7ff;
}
QLineEdit, QComboBox, QListWidget, QPlainTextEdit, QTextEdit {
  background: #22303d;
  border: 1px solid #2d4052;
  border-radius: 10px;
  color: #f2f7ff;
  padding: 6px 8px;
}
QListWidget::item {
  border-radius: 8px;
  padding: 8px;
  margin: 2px 0;
}
QListWidget::item:selected {
  background: #2f8ef9;
  color: #ffffff;
}
QPushButton {
  background: #2f8ef9;
  color: #ffffff;
  border: none;
  border-radius: 10px;
  padding: 8px 12px;
  font-weight: 600;
}
QPushButton:hover {
  background: #4aa0ff;
}
QPushButton:disabled {
  background: #304356;
  color: #99a9bc;
}
QSplitter::handle {
  background: #253646;
  width: 1px;
}
QCheckBox {
  spacing: 8px;
}
QCheckBox::indicator {
  width: 16px;
  height: 16px;
  border-radius: 8px;
  border: 1px solid #4f6780;
  background: #22303d;
}
QCheckBox::indicator:checked {
  background: #2f8ef9;
  border: 1px solid #2f8ef9;
}
"""


class TaskQueueListWidget(QListWidget):
    def __init__(self, on_paths_dropped, on_reordered, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_paths_dropped = on_paths_dropped
        self.on_reordered = on_reordered
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
            self.on_paths_dropped(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)
        self.on_reordered()

    def current_item_ids(self) -> list[str]:
        item_ids: list[str] = []
        for row in range(self.count()):
            item = self.item(row)
            item_id = item.data(Qt.ItemDataRole.UserRole)
            if item_id:
                item_ids.append(str(item_id))
        return item_ids


class TagEditorWidget(QPlainTextEdit):
    commitRequested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            super().keyPressEvent(event)
            self.commitRequested.emit()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Sankaku Uploader")
        self.resize(1420, 860)

        self.repository = JsonRepository()
        self.service = TaskService(self.repository)
        self.settings = self.repository.load_settings()
        self.runner = UploadRunnerController()

        self.active_task_id: str | None = None
        self.pending_review_item_id: str | None = None
        self.pending_review_item_ids: set[str] = set()
        self._last_flushed_tags: list[str] = []

        self._build_ui()
        self._apply_theme()
        self._load_settings_to_ui()
        self._refresh_task_list()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll_runner)
        self.poll_timer.start(300)


    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        settings_row = QHBoxLayout()
        layout.addLayout(settings_row)
        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        settings_row.addLayout(form, 2)

        self.upload_url_edit = QLineEdit()
        self.profile_dir_edit = QLineEdit()
        self.browser_channel_edit = QLineEdit()
        self.max_concurrent_pages_edit = QLineEdit()
        self.max_concurrent_pages_edit.setPlaceholderText("8")
        self.review_mode_combo = QComboBox()
        self.review_mode_combo.addItem("人工审核", ReviewMode.MANUAL_REVIEW.value)
        self.review_mode_combo.addItem("快速通过", ReviewMode.QUICK_PASS.value)
        self.headless_check = QCheckBox("后台运行（不弹浏览器）")

        form.addRow("上传页 URL", self.upload_url_edit)
        form.addRow("浏览器 Profile", self.profile_dir_edit)
        form.addRow("浏览器通道", self.browser_channel_edit)
        form.addRow("并发预取页数", self.max_concurrent_pages_edit)
        form.addRow("标签审核模式", self.review_mode_combo)
        form.addRow("运行方式", self.headless_check)

        self.save_settings_button = QPushButton("保存设置")
        self.save_settings_button.clicked.connect(self._save_settings_from_ui)
        settings_row.addWidget(self.save_settings_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("任务"))
        self.task_list = QListWidget()
        self.task_list.currentItemChanged.connect(self._on_task_selected)
        left_layout.addWidget(self.task_list, 1)

        left_layout.addWidget(self.task_list, 1)
        splitter.addWidget(left)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(QLabel("上传队列（每条下方展示解析后的 Tag）"))
        self.queue_list = TaskQueueListWidget(self._add_paths_to_active_task, self._persist_reorder)
        self.queue_list.currentItemChanged.connect(self._show_item_detail)
        center_layout.addWidget(self.queue_list, 1)

        queue_btn_row = QHBoxLayout()
        self.add_files_button = QPushButton("添加文件")
        self.add_folder_button = QPushButton("添加文件夹")
        self.remove_button = QPushButton("删除选中")
        self.clear_button = QPushButton("清空")
        self.add_files_button.clicked.connect(self._pick_files)
        self.add_folder_button.clicked.connect(self._pick_folder)
        self.remove_button.clicked.connect(self._remove_selected_items)
        self.clear_button.clicked.connect(self._clear_items)
        queue_btn_row.addWidget(self.add_files_button)
        queue_btn_row.addWidget(self.add_folder_button)
        queue_btn_row.addWidget(self.remove_button)
        queue_btn_row.addWidget(self.clear_button)
        center_layout.addLayout(queue_btn_row)

        run_btn_row = QHBoxLayout()
        self.start_button = QPushButton("开始")
        self.pause_button = QPushButton("暂停")
        self.resume_button = QPushButton("恢复")
        self.retry_button = QPushButton("重试失败项")
        self.start_button.clicked.connect(self._start_task)
        self.pause_button.clicked.connect(self._pause_task)
        self.resume_button.clicked.connect(self._resume_task)
        self.retry_button.clicked.connect(self._retry_failed)
        run_btn_row.addWidget(self.start_button)
        run_btn_row.addWidget(self.pause_button)
        run_btn_row.addWidget(self.resume_button)
        run_btn_row.addWidget(self.retry_button)
        center_layout.addLayout(run_btn_row)

        # Diff mode root post ID override (only visible when diff task is active)
        self.diff_parent_row = QHBoxLayout()
        self.diff_parent_label = QLabel("差分模式 父贴子ID")
        self.diff_parent_edit = QLineEdit()
        self.diff_parent_edit.setPlaceholderText("留空则由程序自动获取第一张上传的帖子 ID")
        self.diff_parent_save_btn = QPushButton("设置")
        self.diff_parent_save_btn.clicked.connect(self._save_diff_parent_post_id)
        self.diff_parent_row.addWidget(self.diff_parent_label)
        self.diff_parent_row.addWidget(self.diff_parent_edit, 1)
        self.diff_parent_row.addWidget(self.diff_parent_save_btn)
        center_layout.addLayout(self.diff_parent_row)
        self._set_diff_parent_row_visible(False)

        splitter.addWidget(center)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("当前文件详情"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        right_layout.addWidget(self.detail, 1)

        right_layout.addWidget(QLabel("手动标签编辑（每行或逗号分隔）"))
        self.tag_editor = TagEditorWidget()
        self.tag_editor.setPlaceholderText("例如：\n1girl\nsmile\noutdoors")
        self.tag_editor.setMaximumHeight(140)
        self.tag_editor.commitRequested.connect(self._flush_pending_local_tag_sync)
        right_layout.addWidget(self.tag_editor)

        tag_btn_row = QHBoxLayout()
        self.apply_tags_button = QPushButton("应用标签")
        self.reset_tags_button = QPushButton("还原检测标签")
        self.apply_tags_button.clicked.connect(self._apply_manual_tags)
        self.reset_tags_button.clicked.connect(self._reset_tags_from_detected)
        tag_btn_row.addWidget(self.apply_tags_button)
        tag_btn_row.addWidget(self.reset_tags_button)
        right_layout.addLayout(tag_btn_row)

        review_row = QHBoxLayout()
        self.confirm_review_button = QPushButton("确认提交")
        self.skip_review_button = QPushButton("跳过")
        self.retry_review_button = QPushButton("重试")
        self.confirm_review_button.clicked.connect(lambda: self._send_review_decision("confirm"))
        self.skip_review_button.clicked.connect(lambda: self._send_review_decision("skip"))
        self.retry_review_button.clicked.connect(lambda: self._send_review_decision("retry"))
        review_row.addWidget(self.confirm_review_button)
        review_row.addWidget(self.skip_review_button)
        review_row.addWidget(self.retry_review_button)
        right_layout.addLayout(review_row)

        right_layout.addWidget(QLabel("日志"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        right_layout.addWidget(self.log, 1)
        splitter.addWidget(right)

        splitter.setSizes([280, 760, 430])
        self._set_review_buttons_enabled(False)

    def _apply_theme(self) -> None:
        self.setStyleSheet(_TELEGRAM_STYLE)
        app_font = QFont("Segoe UI", 10)
        self.setFont(app_font)

    def _append_log(self, message: str) -> None:
        color = "#eaf2ff" # Default
        if "[Trace]" in message:
            color = "#888888"
        elif "[Worker]" in message:
            color = "#4aa0ff"
        elif "上传中" in message:
            color = "#2f8ef9"
        elif any(x in message for x in ("成功", "success=True", "全成功", "ok", "OK")):
            color = "#4caf50"
        elif any(x in message for x in ("失败", "failed", "error", "ERROR", "exception")):
            color = "#f44336"
        elif any(x in message for x in ("等待", "Warning", "paused", "暂停", "skipped")):
            color = "#ff9800"
            
        escaped = html.escape(message)
        self.log.appendHtml(f'<span style="color: {color};">{escaped}</span>')

    def _load_settings_to_ui(self) -> None:
        self.upload_url_edit.setText(self.settings.upload_page_url)
        self.profile_dir_edit.setText(self.settings.profile_dir)
        self.browser_channel_edit.setText(self.settings.browser_channel)
        self.max_concurrent_pages_edit.setText(str(self.settings.max_concurrent_pages))
        self.headless_check.setChecked(self.settings.headless)
        for i in range(self.review_mode_combo.count()):
            if self.review_mode_combo.itemData(i) == self.settings.review_mode.value:
                self.review_mode_combo.setCurrentIndex(i)
                break

    def _save_settings_from_ui(self) -> None:
        self.settings.upload_page_url = self.upload_url_edit.text().strip() or self.settings.upload_page_url
        self.settings.profile_dir = self.profile_dir_edit.text().strip() or self.settings.profile_dir
        self.settings.browser_channel = self.browser_channel_edit.text().strip() or self.settings.browser_channel
        try:
            self.settings.max_concurrent_pages = max(int(self.max_concurrent_pages_edit.text().strip() or "8"), 1)
        except ValueError:
            self.settings.max_concurrent_pages = 8
            self.max_concurrent_pages_edit.setText("8")
        self.settings.review_mode = ReviewMode(str(self.review_mode_combo.currentData()))
        self.settings.headless = self.headless_check.isChecked()
        self.repository.save_settings(self.settings)
        self._append_log("设置已保存")

    def _refresh_task_list(self) -> None:
        self.task_list.clear()
        for task in self.service.list_tasks():
            text = f"[{task.status.value}] {task.task_name} ({task.task_type.value})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, task.task_id)
            self.task_list.addItem(item)

        if self.active_task_id:
            for row in range(self.task_list.count()):
                item = self.task_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == self.active_task_id:
                    self.task_list.setCurrentItem(item)
                    return

        if self.task_list.count() > 0:
            self.task_list.setCurrentRow(0)

    def _create_task(self) -> None:
        pass

    def _delete_task(self) -> None:
        pass

    def _on_task_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        self.active_task_id = str(current.data(Qt.ItemDataRole.UserRole))
        task = self._active_task()
        is_diff = task is not None and task.task_type is TaskType.DIFF_GROUP
        self._set_diff_parent_row_visible(is_diff)
        if is_diff and task is not None:
            blocker = QSignalBlocker(self.diff_parent_edit)
            self.diff_parent_edit.setText(task.manual_root_post_id)
            del blocker
        self._render_active_task()

    def _set_diff_parent_row_visible(self, visible: bool) -> None:
        self.diff_parent_label.setVisible(visible)
        self.diff_parent_edit.setVisible(visible)
        self.diff_parent_save_btn.setVisible(visible)

    def _save_diff_parent_post_id(self) -> None:
        task = self._active_task()
        if task is None:
            return
        post_id = self.diff_parent_edit.text().strip()
        self.service.set_manual_root_post_id(task.task_id, post_id)
        self._append_log(f"差分父帖子 ID 已设置：{post_id or '(空 — 程序自动获取)'}"
        )

    def _active_task(self) -> UploadTask | None:
        if not self.active_task_id:
            return None
        try:
            return self.service.get_task(self.active_task_id)
        except KeyError:
            return None

    def _render_active_task(self) -> None:
        task = self._active_task()
        current_item = self.queue_list.currentItem()
        current_item_id = None
        if current_item is not None:
            current_item_id = str(current_item.data(Qt.ItemDataRole.UserRole))
        had_focus = self.queue_list.hasFocus()
        blocker = QSignalBlocker(self.queue_list)
        self.queue_list.clear()
        if task is None:
            del blocker
            return

        for item in sorted(task.items, key=lambda x: x.order_index):
            row_text = self._build_item_row_text(task, item)
            list_item = QListWidgetItem(row_text)
            list_item.setData(Qt.ItemDataRole.UserRole, item.item_id)
            self.queue_list.addItem(list_item)
            if item.item_id == current_item_id:
                self.queue_list.setCurrentItem(list_item)

        del blocker
        if current_item_id is not None:
            self._restore_queue_focus(current_item_id, had_focus)

    def _restore_queue_focus(self, item_id: str, had_focus: bool) -> None:
        for row in range(self.queue_list.count()):
            item = self.queue_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) != item_id:
                continue
            self.queue_list.setCurrentItem(item)
            self.queue_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
            if had_focus:
                self.queue_list.setFocus(Qt.FocusReason.OtherFocusReason)
            break

    def _build_item_row_text(self, task: UploadTask, item) -> str:
        if task.task_type is TaskType.DIFF_GROUP:
            role = "ROOT" if item.order_index == 0 else "CHILD"
        else:
            role = "ITEM"

        line1 = f"[{role}] #{item.order_index + 1:03d}  {item.file_name}"
        line2 = (
            f"状态: {item.status.value}   类型: {item.file_type.value}   "
            f"post: {item.created_post_id or '-'}   parent: {item.parent_post_id or '-'}"
        )
        tags, manual_cleared = self._effective_item_tags(item)
        if tags:
            preview = tags[:10]
            suffix = " …" if len(tags) > 10 else ""
            line3 = "🏷 " + "  ·  ".join(preview) + suffix
        elif manual_cleared:
            line3 = "🏷 已手动清空标签"
        else:
            line3 = "🏷 等待网页标签返回"
        return "\n".join([line1, line2, line3])

    @staticmethod
    def _effective_item_tags(item) -> tuple[list[str], bool]:
        if item.final_tags_locked:
            return list(item.final_tags), len(item.final_tags) == 0
        if item.final_tags:
            return list(item.final_tags), False
        return list(item.detected_tags), False

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件")
        self._add_paths_to_active_task([Path(file) for file in files])

    def _pick_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not directory:
            return
        root = Path(directory)
        paths = [path for path in root.rglob("*") if path.is_file()]
        self._add_paths_to_active_task(paths)

    def _add_paths_to_active_task(self, paths: Iterable[Path]) -> None:
        task = self._active_task()
        if task is None:
            QMessageBox.warning(self, "提示", "请先创建或选择任务")
            return
        path_list = list(paths)
        self.service.add_files(task.task_id, path_list)
        self._render_active_task()
        self._append_log(f"添加 {len(path_list)} 个文件到任务 {task.task_name}")

    def _persist_reorder(self) -> None:
        task = self._active_task()
        if task is None:
            return
        ordered = self.queue_list.current_item_ids()
        self.service.reorder_items(task.task_id, ordered)
        self._render_active_task()

    def _remove_selected_items(self) -> None:
        task = self._active_task()
        if task is None:
            return
        for list_item in self.queue_list.selectedItems():
            item_id = str(list_item.data(Qt.ItemDataRole.UserRole))
            self.service.remove_item(task.task_id, item_id)
        self._render_active_task()

    def _clear_items(self) -> None:
        task = self._active_task()
        if task is None:
            return
        self.service.clear_items(task.task_id)
        self._render_active_task()

    def _show_item_detail(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self.detail.clear()
        self._set_tag_editor_text([])
        if current is None:
            return
        task = self._active_task()
        if task is None:
            return
        item_id = str(current.data(Qt.ItemDataRole.UserRole))
        for item in task.items:
            if item.item_id != item_id:
                continue
            lines = [
                f"item_id: {item.item_id}",
                f"file: {item.file_path}",
                f"status: {item.status.value}",
                f"detected_tags ({len(item.detected_tags)}): {item.detected_tags}",
                f"final_tags ({len(item.final_tags)}): {item.final_tags}",
                f"final_tags_locked: {item.final_tags_locked}",
                f"parent_post_id: {item.parent_post_id}",
                f"created_post_id: {item.created_post_id}",
                f"error: {item.error_message}",
            ]
            self.detail.setPlainText("\n".join(lines))
            tag_source, _ = self._effective_item_tags(item)
            if tag_source:
                self._set_tag_editor_text(tag_source)
            return

    def _set_tag_editor_text(self, tags: list[str]) -> None:
        blocker = QSignalBlocker(self.tag_editor)
        try:
            self.tag_editor.setPlainText("\n".join(tags))
        finally:
            del blocker

    def _selected_item_context(self):
        task = self._active_task()
        current = self.queue_list.currentItem()
        if task is None or current is None:
            return None, None
        item_id = str(current.data(Qt.ItemDataRole.UserRole))
        for item in task.items:
            if item.item_id == item_id:
                return task, item
        return task, None

    def _flush_pending_local_tag_sync(self) -> None:
        task, item = self._selected_item_context()
        if task is None or item is None or item.item_id not in self.pending_review_item_ids:
            return
        tags = self._parse_manual_tags(self.tag_editor.toPlainText())
        if tags == self._last_flushed_tags:
            return
        self._last_flushed_tags = list(tags)
        self.service.update_item_tags(task.task_id, item.item_id, tags)
        self.runner.send_tag_sync(item.item_id, tags)
        self._append_log(f"本地标签已推送到网页：{item.file_name} ({len(tags)} tags)")
        self._render_active_task()

    def _apply_manual_tags(self) -> None:
        task, item = self._selected_item_context()
        if task is None or item is None:
            return
        raw = self.tag_editor.toPlainText().strip()
        if not raw:
            tags: list[str] = []
        else:
            tags = self._parse_manual_tags(raw)
        self.service.update_item_tags(task.task_id, item.item_id, tags)
        self._last_flushed_tags = list(tags)
        self.runner.send_tag_sync(item.item_id, tags)
        self._append_log(f"已更新标签：{item.file_name} ({len(tags)} tags)")
        self._render_active_task()

    def _reset_tags_from_detected(self) -> None:
        task, item = self._selected_item_context()
        if task is None or item is None:
            return
        self.service.update_item_tags(task.task_id, item.item_id, list(item.detected_tags))
        self._last_flushed_tags = list(item.detected_tags)
        self.runner.send_tag_sync(item.item_id, list(item.detected_tags))
        self._set_tag_editor_text(item.detected_tags)
        self._append_log(f"已还原检测标签：{item.file_name}")
        self._render_active_task()

    @staticmethod
    def _parse_manual_tags(raw: str) -> list[str]:
        import re

        tokens = [part.strip() for part in re.split(r"[\n,]+", raw) if part.strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for tag in tokens:
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(tag)
        return deduped

    def _start_task(self) -> None:
        task = self._active_task()
        if task is None:
            return
        if self.runner.is_running():
            self._append_log("已有任务在执行")
            return

        self._save_settings_from_ui()

        try:
            task.validate()
        except Exception as exc:
            QMessageBox.warning(self, "任务校验失败", str(exc))
            return

        self.service.set_task_status(task.task_id, TaskStatus.RUNNING, force=True)
        self.runner.start(task, self.settings)
        self._refresh_task_list()
        self._append_log(f"任务开始：{task.task_name}")

    def _pause_task(self) -> None:
        task = self._active_task()
        if task is None:
            return
        if self.runner.is_running():
            self.runner.stop()
        for item in task.items:
            if item.status in {ItemStatus.UPLOADING, ItemStatus.WAITING_TAGS, ItemStatus.WAITING_USER_CONFIRM, ItemStatus.SUBMITTING}:
                item.set_status(ItemStatus.PENDING)
        self.service.set_task_status(task.task_id, TaskStatus.PAUSED, force=True)
        self.service.persist()
        self._refresh_task_list()
        self._render_active_task()
        self._append_log("任务已暂停")

    def _resume_task(self) -> None:
        task = self._active_task()
        if task is None:
            return
        if self.runner.is_running():
            self._append_log("任务正在运行")
            return
        self.service.set_task_status(task.task_id, TaskStatus.RUNNING, force=True)
        self.runner.start(task, self.settings)
        self._refresh_task_list()
        self._append_log("任务已恢复")

    def _retry_failed(self) -> None:
        task = self._active_task()
        if task is None:
            return
        count = self.service.retry_failed_items(task.task_id)
        self._render_active_task()
        self._refresh_task_list()
        self._append_log(f"已重置 {count} 个失败项")

    def _set_review_buttons_enabled(self, enabled: bool) -> None:
        self.confirm_review_button.setEnabled(enabled)
        self.skip_review_button.setEnabled(enabled)
        self.retry_review_button.setEnabled(enabled)

    def _send_review_decision(self, action: str) -> None:
        target_item_id = self._current_review_item_id()
        if not target_item_id:
            return
        if action == "confirm":
            self._flush_pending_local_tag_sync()
        tags_override = None
        tags_override_allow_empty = False
        if action == "confirm":
            task, item = self._selected_item_context()
            if task is not None and item is not None:
                edited_tags = self._parse_manual_tags(self.tag_editor.toPlainText())
                current_tags, _ = self._effective_item_tags(item)
                force_override = bool(item.final_tags_locked)
                if edited_tags != current_tags:
                    tags_override = edited_tags
                    tags_override_allow_empty = self.tag_editor.toPlainText().strip() == ""
                    self.service.update_item_tags(task.task_id, item.item_id, edited_tags)
                    self._render_active_task()
                    self._append_log(f"确认前应用本地标签编辑：{item.file_name} ({len(edited_tags)} tags)")
                elif force_override:
                    tags_override = edited_tags
                    tags_override_allow_empty = self.tag_editor.toPlainText().strip() == ""
                    self._append_log(f"确认前强制应用本地标签：{item.file_name} ({len(edited_tags)} tags)")

        self.runner.send_decision(
            target_item_id,
            action,
            tags_override=tags_override,
            tags_override_allow_empty=tags_override_allow_empty,
        )
        self.pending_review_item_ids.discard(target_item_id)
        self.pending_review_item_id = next(iter(self.pending_review_item_ids), None)
        self._set_review_buttons_enabled(bool(self.pending_review_item_ids))
        self._append_log(f"发送审核指令：{action}")

    def _current_review_item_id(self) -> str | None:
        task, item = self._selected_item_context()
        if item is not None and item.item_id in self.pending_review_item_ids:
            return item.item_id
        if self.pending_review_item_id in self.pending_review_item_ids:
            return self.pending_review_item_id
        return next(iter(self.pending_review_item_ids), None)

    def _poll_runner(self) -> None:
        for event in self.runner.poll():
            if event.kind == "task_started":
                self._append_log(f"[Worker] 任务启动：{event.payload.get('task_name')}")
            elif event.kind == "log":
                self._append_log(f"[Trace] {event.payload.get('message', '')}")
            elif event.kind == "item_status":
                self._on_item_status(event.payload)
            elif event.kind == "item_review":
                self._on_item_review(event.payload)
            elif event.kind == "item_review_update":
                self._on_item_review_update(event.payload)
            elif event.kind == "item_result":
                self._on_item_result(event.payload)
            elif event.kind == "task_complete":
                self._on_task_complete(event.payload)

    def _on_item_status(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        item_id = str(payload.get("item_id") or "")
        status = str(payload.get("status") or "")
        if status == "uploading":
            self.service.update_item_result(task_id, item_id, status=ItemStatus.UPLOADING)
            self._append_log(f"上传中：{payload.get('file_name')}")
            self._render_active_task()

    def _on_item_review(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        item_id = str(payload.get("item_id") or "")
        tags = list(payload.get("ai_tags") or [])
        self.pending_review_item_id = item_id
        self.pending_review_item_ids.add(item_id)
        self._set_review_buttons_enabled(True)
        self.service.update_item_result(
            task_id,
            item_id,
            status=ItemStatus.WAITING_USER_CONFIRM,
            detected_tags=tags,
            final_tags=tags,
        )
        self._last_flushed_tags = list(tags)
        if self.queue_list.currentItem() is not None and str(self.queue_list.currentItem().data(Qt.ItemDataRole.UserRole)) == item_id:
            self._set_tag_editor_text(tags)
        self._append_log(f"等待人工确认：{payload.get('file_name')} tags={tags}")
        self._render_active_task()

    def _on_item_review_update(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        item_id = str(payload.get("item_id") or "")
        tags = list(payload.get("ai_tags") or [])
        self.pending_review_item_id = item_id
        self.pending_review_item_ids.add(item_id)
        self._set_review_buttons_enabled(True)
        self.service.update_item_result(task_id, item_id, status=ItemStatus.WAITING_USER_CONFIRM, final_tags=tags)
        if item_id in self.pending_review_item_ids:
            current = self.queue_list.currentItem()
            if current is not None and str(current.data(Qt.ItemDataRole.UserRole)) == item_id:
                self._set_tag_editor_text(tags)
                self._last_flushed_tags = list(tags)
        self._append_log(f"网页标签已同步：{payload.get('file_name')} ({len(tags)} tags)")
        self._render_active_task()

    def _on_item_result(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        item_id = str(payload.get("item_id") or "")
        success = bool(payload.get("success"))
        tags = list(payload.get("ai_tags") or [])
        post_id = str(payload.get("post_id") or "")
        error = str(payload.get("error") or "")

        status = ItemStatus.SUCCESS if success else ItemStatus.FAILED
        if not success:
            tag_state = str(payload.get("tag_state") or "")
            if tag_state == "tag_error":
                status = ItemStatus.TAG_ERROR
            elif tag_state == "duplicate":
                status = ItemStatus.DUPLICATE
                
        if success:
            self.pending_review_item_ids.discard(item_id)
            if self.pending_review_item_id == item_id:
                self.pending_review_item_id = next(iter(self.pending_review_item_ids), None)
        self.service.update_item_result(
            task_id,
            item_id,
            status=status,
            final_tags=tags,
            post_id=post_id,
            error=error,
        )
        detail = f"结果: item={item_id} success={success} post_id={post_id or '-'}"
        if error:
            detail += f" error={error}"
        self._append_log(detail)
        self._render_active_task()

    def _on_task_complete(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        has_failures = bool(payload.get("has_failures"))
        if has_failures:
            self.service.set_task_status(task_id, TaskStatus.PARTIAL_FAILED, force=True)
            self._append_log("任务完成（部分失败）")
        else:
            self.service.set_task_status(task_id, TaskStatus.COMPLETED, force=True)
            self._append_log("任务完成（全部成功）")

        self.pending_review_item_id = None
        self.pending_review_item_ids.clear()
        self._set_review_buttons_enabled(False)
        self._refresh_task_list()
        self._render_active_task()


def build_app() -> QApplication:
    return QApplication.instance() or QApplication([])
