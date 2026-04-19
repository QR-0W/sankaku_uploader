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
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
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
/* ==== Sidebar ==== */
QWidget#sidebar {
  background: #0f1923;
  border-right: 1px solid #1a2d3d;
}
QPushButton#nav_btn {
  background: transparent;
  color: #7a99b8;
  border: none;
  border-radius: 10px;
  padding: 11px 16px;
  text-align: left;
  font-weight: 500;
  font-size: 13px;
}
QPushButton#nav_btn:hover {
  background: #162230;
  color: #d8e7ff;
}
QPushButton#nav_btn:checked {
  background: #1a3555;
  color: #5aadff;
  font-weight: 700;
}
QLabel#sidebar_logo {
  color: #eaf2ff;
  font-size: 28px;
  padding: 4px;
}
QLabel#sidebar_app_name {
  color: #4f7090;
  font-size: 10px;
  padding-bottom: 8px;
  letter-spacing: 1px;
}
QLabel#status_label {
  color: #3d596e;
  font-size: 11px;
  padding: 4px;
}
QLabel#page_section_title {
  color: #d8e7ff;
  font-size: 15px;
  font-weight: 700;
  padding: 4px 0 10px 0;
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
        self._upload_all_queue: list = []  # used by "upload all" feature
        self._last_synced_effective_tags_by_item: dict[str, list[str]] = {}

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
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ==== LEFT SIDEBAR ====
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(185)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(12, 20, 12, 16)
        sb.setSpacing(3)

        logo = QLabel("\u2b06")
        logo.setObjectName("sidebar_logo")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb.addWidget(logo)

        app_name = QLabel("SANKAKU UPLOAD")
        app_name.setObjectName("sidebar_app_name")
        app_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sb.addWidget(app_name)
        sb.addSpacing(12)

        self._nav_btns: list[QPushButton] = []
        for label, idx in [("\U0001f4cb  \u4efb\u52a1\u961f\u5217", 0), ("\u2699\ufe0f  \u8bbe\u7f6e", 1), ("\U0001f4dc  \u65e5\u5fd7", 2)]:
            btn = QPushButton(label)
            btn.setObjectName("nav_btn")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.clicked.connect(lambda _checked, i=idx: self._switch_page(i))
            self._nav_btns.append(btn)
            sb.addWidget(btn)

        sb.addStretch()

        self.status_indicator = QLabel()
        self.status_indicator.setObjectName("status_label")
        self.status_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._update_status_indicator("\u7a7a\u95f2", "#3d596e")
        sb.addWidget(self.status_indicator)

        root_layout.addWidget(sidebar)

        # ==== PAGE STACK ====
        self.page_stack = QStackedWidget()
        root_layout.addWidget(self.page_stack, 1)

        self.page_stack.addWidget(self._build_queue_page())    # 0
        self.page_stack.addWidget(self._build_settings_page()) # 1
        self.page_stack.addWidget(self._build_log_page())      # 2

        self._switch_page(0)

    def _switch_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_btns):
            btn.setChecked(i == index)

    def _build_queue_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # Left: Task Queue management
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 6, 10)
        left_layout.addWidget(QLabel("\u4efb\u52a1\u961f\u5217"))
        self.task_list = QListWidget()
        self.task_list.currentItemChanged.connect(self._on_task_selected)
        left_layout.addWidget(self.task_list, 1)

        self.add_queue_btn = QPushButton("\uff0b \u6dfb\u52a0\u961f\u5217 \u25be")
        self.add_queue_btn.setObjectName("add_queue_btn")
        self.add_queue_btn.clicked.connect(self._show_add_queue_menu)
        left_layout.addWidget(self.add_queue_btn)

        queue_mgmt_row = QHBoxLayout()
        self.rename_task_btn = QPushButton("\u91cd\u547d\u540d")
        self.delete_task_btn = QPushButton("\u5220\u9664")
        self.rename_task_btn.clicked.connect(self._rename_task)
        self.delete_task_btn.clicked.connect(self._delete_task)
        queue_mgmt_row.addWidget(self.rename_task_btn)
        queue_mgmt_row.addWidget(self.delete_task_btn)
        left_layout.addLayout(queue_mgmt_row)

        self.upload_all_button = QPushButton("\u2b06 \u4e0a\u4f20\u5168\u90e8\u961f\u5217")
        self.upload_all_button.clicked.connect(self._start_all_tasks)
        left_layout.addWidget(self.upload_all_button)

        splitter.addWidget(left)

        # Center: Upload Queue file list
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(6, 10, 6, 10)
        center_layout.addWidget(QLabel("\u4e0a\u4f20\u961f\u5217\uff08\u6bcf\u6761\u4e0b\u65b9\u5c55\u793a\u89e3\u6790\u540e\u7684 Tag\uff09"))
        self.queue_list = TaskQueueListWidget(self._add_paths_to_active_task, self._persist_reorder)
        self.queue_list.currentItemChanged.connect(self._show_item_detail)
        center_layout.addWidget(self.queue_list, 1)

        queue_btn_row = QHBoxLayout()
        self.add_files_button = QPushButton("\u6dfb\u52a0\u6587\u4ef6")
        self.add_folder_button = QPushButton("\u6dfb\u52a0\u6587\u4ef6\u5939")
        self.remove_button = QPushButton("\u5220\u9664\u9009\u4e2d")
        self.clear_button = QPushButton("\u6e05\u7a7a")
        self.add_files_button.clicked.connect(self._pick_files)
        self.add_folder_button.clicked.connect(self._pick_folder)
        self.remove_button.clicked.connect(self._remove_selected_items)
        self.clear_button.clicked.connect(self._clear_items)
        for btn in (self.add_files_button, self.add_folder_button, self.remove_button, self.clear_button):
            queue_btn_row.addWidget(btn)
        center_layout.addLayout(queue_btn_row)

        run_btn_row = QHBoxLayout()
        self.start_button = QPushButton("\u5f00\u59cb")
        self.pause_button = QPushButton("\u6682\u505c")
        self.resume_button = QPushButton("\u6062\u590d")
        self.retry_button = QPushButton("\u91cd\u8bd5\u5931\u8d25\u9879")
        self.start_button.clicked.connect(self._start_task)
        self.pause_button.clicked.connect(self._pause_task)
        self.resume_button.clicked.connect(self._resume_task)
        self.retry_button.clicked.connect(self._retry_failed)
        for btn in (self.start_button, self.pause_button, self.resume_button, self.retry_button):
            run_btn_row.addWidget(btn)
        center_layout.addLayout(run_btn_row)

        # Diff mode parent post ID row
        self.diff_parent_row = QHBoxLayout()
        self.diff_parent_label = QLabel("\u5dee\u5206\u6a21\u5f0f \u88ab\u6bcf\u5b50ID")
        self.diff_parent_edit = QLineEdit()
        self.diff_parent_edit.setPlaceholderText("\u7559\u7a7a\u5219\u7531\u7a0b\u5e8f\u81ea\u52a8\u83b7\u53d6\u7b2c\u4e00\u5f20\u4e0a\u4f20\u7684\u5e16\u5b50 ID")
        self.diff_parent_save_btn = QPushButton("\u8bbe\u7f6e")
        self.diff_parent_save_btn.clicked.connect(self._save_diff_parent_post_id)
        self.diff_parent_row.addWidget(self.diff_parent_label)
        self.diff_parent_row.addWidget(self.diff_parent_edit, 1)
        self.diff_parent_row.addWidget(self.diff_parent_save_btn)
        center_layout.addLayout(self.diff_parent_row)
        self._set_diff_parent_row_visible(False)

        splitter.addWidget(center)

        # Right: File details + tag editor + review buttons
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 10, 10, 10)
        right_layout.addWidget(QLabel("\u5f53\u524d\u6587\u4ef6\u8be6\u60c5"))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        right_layout.addWidget(self.detail, 1)

        tag_header_layout = QHBoxLayout()
        tag_header_layout.addWidget(QLabel("\u624b\u52a8\u6807\u7b7e\u7f16\u8f91\uff08\u6bcf\u884c\u6216\u9017\u53f7\u5206\u9694\uff09"))
        self.tag_count_label = QLabel("0 / 20")
        self.tag_count_label.setStyleSheet("color: #f44336; font-weight: bold;")
        tag_header_layout.addStretch()
        tag_header_layout.addWidget(self.tag_count_label)
        right_layout.addLayout(tag_header_layout)

        self.author_tags_label = QLabel("作者标签（仅差分队列，队列级）")
        right_layout.addWidget(self.author_tags_label)
        self.author_tags_editor = QPlainTextEdit()
        self.author_tags_editor.setPlaceholderText("每行一个 tag，例如：\nauthor_id_123\nsource_name")
        self.author_tags_editor.setMaximumHeight(96)
        right_layout.addWidget(self.author_tags_editor)
        self.author_tags_timer = QTimer(self)
        self.author_tags_timer.setSingleShot(True)
        self.author_tags_timer.setInterval(350)
        self.author_tags_timer.timeout.connect(self._save_author_tags_from_ui)
        self.author_tags_editor.textChanged.connect(lambda: self.author_tags_timer.start())
        self._set_diff_author_tag_controls_visible(False)

        right_layout.addWidget(QLabel("手动标签编辑（每行或逗号分隔）"))
        self.tag_editor = TagEditorWidget()
        self.tag_editor.setPlaceholderText("\u4f8b\u5982\uff1a\n1girl\nsmile\noutdoors")
        self.tag_editor.setMaximumHeight(140)
        self.tag_editor.commitRequested.connect(self._flush_pending_local_tag_sync)
        self.tag_editor.textChanged.connect(self._update_tag_count_display)
        right_layout.addWidget(self.tag_editor)

        tag_btn_row = QHBoxLayout()
        self.apply_tags_button = QPushButton("\u5e94\u7528\u6807\u7b7e")
        self.reset_tags_button = QPushButton("\u8fd8\u539f\u68c0\u6d4b\u6807\u7b7e")
        self.apply_tags_button.clicked.connect(self._apply_manual_tags)
        self.reset_tags_button.clicked.connect(self._reset_tags_from_detected)
        tag_btn_row.addWidget(self.apply_tags_button)
        tag_btn_row.addWidget(self.reset_tags_button)
        right_layout.addLayout(tag_btn_row)

        review_row = QHBoxLayout()
        self.confirm_review_button = QPushButton("\u786e\u8ba4\u63d0\u4ea4")
        self.skip_review_button = QPushButton("\u8df3\u8fc7")
        self.retry_review_button = QPushButton("\u91cd\u8bd5")
        self.confirm_review_button.clicked.connect(lambda: self._send_review_decision("confirm"))
        self.skip_review_button.clicked.connect(lambda: self._send_review_decision("skip"))
        self.retry_review_button.clicked.connect(lambda: self._send_review_decision("retry"))
        review_row.addWidget(self.confirm_review_button)
        review_row.addWidget(self.skip_review_button)
        review_row.addWidget(self.retry_review_button)
        right_layout.addLayout(review_row)

        splitter.addWidget(right)
        splitter.setSizes([235, 720, 430])
        self._set_review_buttons_enabled(False)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(48, 32, 48, 32)
        outer.setSpacing(0)

        title = QLabel("\u8bbe\u7f6e")
        title.setObjectName("page_section_title")
        outer.addWidget(title)

        form = QFormLayout()
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(14)

        self.upload_url_edit = QLineEdit()
        self.profile_dir_edit = QLineEdit()
        self.browser_channel_edit = QLineEdit()
        self.max_concurrent_pages_edit = QLineEdit()
        self.max_concurrent_pages_edit.setPlaceholderText("8")
        self.proxy_server_edit = QLineEdit()
        self.proxy_server_edit.setPlaceholderText(
            "\u4f8b\u5982\uff1ahttp://127.0.0.1:7890 \u6216 socks5://127.0.0.1:1080\uff08\u7559\u7a7a\u5219\u4e0d\u4f7f\u7528\u4ee3\u7406\uff09"
        )
        self.review_mode_combo = QComboBox()
        self.review_mode_combo.addItem("\u4eba\u5de5\u5ba1\u6838", ReviewMode.MANUAL_REVIEW.value)
        self.review_mode_combo.addItem("\u5feb\u901f\u901a\u8fc7", ReviewMode.QUICK_PASS.value)
        self.headless_check = QCheckBox("\u540e\u53f0\u8fd0\u884c\uff08\u4e0d\u5f39\u6d4f\u89c8\u5668\uff09")

        form.addRow("\u4e0a\u4f20\u9875 URL", self.upload_url_edit)
        form.addRow("\u6d4f\u89c8\u5668 Profile", self.profile_dir_edit)
        form.addRow("\u6d4f\u89c8\u5668\u901a\u9053", self.browser_channel_edit)
        form.addRow("\u5e76\u53d1\u9884\u53d6\u9875\u6570", self.max_concurrent_pages_edit)
        form.addRow("\u4ee3\u7406\u670d\u52a1\u5668", self.proxy_server_edit)
        form.addRow("\u6807\u7b7e\u5ba1\u6838\u6a21\u5f0f", self.review_mode_combo)
        form.addRow("\u8fd0\u884c\u65b9\u5f0f", self.headless_check)
        outer.addLayout(form)
        outer.addSpacing(24)

        self.save_settings_button = QPushButton("\u4fdd\u5b58\u8bbe\u7f6e")
        self.save_settings_button.setFixedWidth(150)
        self.save_settings_button.clicked.connect(self._save_settings_from_ui)
        outer.addWidget(self.save_settings_button)
        outer.addStretch()
        return page

    def _build_log_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("\u8fd0\u884c\u65e5\u5fd7")
        title.setObjectName("page_section_title")
        header.addWidget(title)
        header.addStretch()
        clear_btn = QPushButton("\u6e05\u7a7a")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(lambda: self.log.clear())
        header.addWidget(clear_btn)
        layout.addLayout(header)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        return page

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
        elif "[已存在]" in message:
            color = "#00bcd4"
            
        escaped = html.escape(message)
        self.log.append(f'<span style="color: {color};">{escaped}</span>')

    def _load_settings_to_ui(self) -> None:
        self.upload_url_edit.setText(self.settings.upload_page_url)
        self.profile_dir_edit.setText(self.settings.profile_dir)
        self.browser_channel_edit.setText(self.settings.browser_channel)
        self.max_concurrent_pages_edit.setText(str(self.settings.max_concurrent_pages))
        self.proxy_server_edit.setText(getattr(self.settings, 'proxy_server', ''))
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
        self.settings.proxy_server = self.proxy_server_edit.text().strip()
        self.repository.save_settings(self.settings)
        self._append_log("设置已保存")

    def _refresh_task_list(self) -> None:
        blocker = QSignalBlocker(self.task_list)
        self.task_list.clear()
        for task in self.service.list_tasks():
            text = f"[{task.status.value}] {task.task_name} ({task.task_type.value})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, task.task_id)
            self.task_list.addItem(item)
        del blocker

        # Restore selection to the previously active task without triggering
        # _on_task_selected (which would re-render and lose queue focus).
        if self.active_task_id:
            for row in range(self.task_list.count()):
                item = self.task_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == self.active_task_id:
                    b2 = QSignalBlocker(self.task_list)
                    self.task_list.setCurrentItem(item)
                    del b2
                    return

        # Fallback: nothing matched, select first
        if self.task_list.count() > 0:
            self.task_list.setCurrentRow(0)

    def _show_add_queue_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("普通队列", lambda: self._create_task("normal"))
        menu.addAction("差分队列", lambda: self._create_task("diff"))
        menu.exec(self.add_queue_btn.mapToGlobal(self.add_queue_btn.rect().bottomLeft()))

    def _create_task(self, mode: str) -> None:
        from sankaku_uploader.domain import TaskType
        task_type = TaskType.DIFF_GROUP if mode == "diff" else TaskType.NORMAL_BATCH
        tasks = self.service.list_tasks()
        normal_count = sum(1 for t in tasks if t.task_type == TaskType.NORMAL_BATCH)
        diff_count = sum(1 for t in tasks if t.task_type == TaskType.DIFF_GROUP)
        default_name = (
            f"差分队列 {diff_count + 1}" if task_type == TaskType.DIFF_GROUP
            else f"普通队列 {normal_count + 1}"
        )
        name, ok = QInputDialog.getText(self, "创建队列", "队列名称：", text=default_name)
        if not ok or not name.strip():
            return
        task = self.service.create_task(name.strip(), task_type)
        self.active_task_id = task.task_id
        self._refresh_task_list()
        for row in range(self.task_list.count()):
            item = self.task_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == task.task_id:
                self.task_list.setCurrentItem(item)
                break
        self._append_log(f"已创建队列：{task.task_name} ({task.task_type.value})")

    def _delete_task(self) -> None:
        task = self._active_task()
        if task is None:
            return
        reply = QMessageBox.question(
            self, "删除队列",
            f"确定删除队列 \u0027{task.task_name}\u0027？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete_task(task.task_id)
        except ValueError as exc:
            QMessageBox.warning(self, "无法删除", str(exc))
            return
        self.active_task_id = None
        self._refresh_task_list()
        if self.task_list.count() > 0:
            self.task_list.setCurrentRow(0)
        self._append_log(f"已删除队列：{task.task_name}")

    def _rename_task(self) -> None:
        task = self._active_task()
        if task is None:
            return
        name, ok = QInputDialog.getText(self, "重命名队列", "新名称：", text=task.task_name)
        if not ok or not name.strip():
            return
        self.service.rename_task(task.task_id, name.strip())
        self._refresh_task_list()
        self._append_log(f"队列已重命名为：{name.strip()}")

    def _start_all_tasks(self) -> None:
        if self.runner.is_running():
            self._append_log("已有任务在执行，请等待当前任务完成后再上传全部")
            return
        pending = [t for t in self.service.list_tasks() if t.items]
        if not pending:
            QMessageBox.information(self, "提示", "所有队列均为空")
            return
        self._upload_all_queue = list(pending)
        self._append_log(f"准备依次上传 {len(self._upload_all_queue)} 个队列...")
        self._run_next_in_all_queue()

    def _run_next_in_all_queue(self) -> None:
        while self._upload_all_queue:
            task = self._upload_all_queue.pop(0)
            try:
                task.validate()
            except Exception as exc:
                self._append_log(f"跳过队列 {task.task_name}：{exc}")
                continue
            self._save_settings_from_ui()
            self.service.set_task_status(task.task_id, TaskStatus.RUNNING, force=True)
            self.active_task_id = task.task_id
            self.runner.start(task, self.settings)
            self._refresh_task_list()
            self._render_active_task()
            self._append_log(f"[批量] 开始队列：{task.task_name}")
            return
        self._append_log("所有队列上传完毕")


    def _on_task_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        self._save_author_tags_from_ui()
        self.active_task_id = str(current.data(Qt.ItemDataRole.UserRole))
        task = self._active_task()
        is_diff = task is not None and task.task_type is TaskType.DIFF_GROUP
        self._set_diff_parent_row_visible(is_diff)
        self._set_diff_author_tag_controls_visible(is_diff)
        if is_diff and task is not None:
            blocker = QSignalBlocker(self.diff_parent_edit)
            self.diff_parent_edit.setText(task.manual_root_post_id)
            del blocker
            self._set_author_tags_text(task.author_tags)
        else:
            self._set_author_tags_text([])
        self._render_active_task()

    def _set_diff_parent_row_visible(self, visible: bool) -> None:
        self.diff_parent_label.setVisible(visible)
        self.diff_parent_edit.setVisible(visible)
        self.diff_parent_save_btn.setVisible(visible)

    def _set_diff_author_tag_controls_visible(self, visible: bool) -> None:
        self.author_tags_label.setVisible(visible)
        self.author_tags_editor.setVisible(visible)

    def _save_author_tags_from_ui(self) -> None:
        task = self._active_task()
        if task is None or task.task_type is not TaskType.DIFF_GROUP:
            return
        tags = self._parse_manual_tags(self.author_tags_editor.toPlainText())
        if tags == list(task.author_tags):
            return
        self.service.set_author_tags(task.task_id, tags)
        self._append_log(f"已保存作者标签：{len(tags)} 个")
        self._render_active_task()

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
        tags, manual_cleared = self._effective_item_tags(task, item)
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
    def _item_base_tags(item) -> tuple[list[str], bool]:
        if item.final_tags_locked:
            return list(item.final_tags), len(item.final_tags) == 0
        if item.final_tags:
            return list(item.final_tags), False
        return list(item.detected_tags), False

    @staticmethod
    def _merge_tags(base_tags: list[str], extra_tags: list[str]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for tag in [*base_tags, *extra_tags]:
            clean = str(tag).strip()
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(clean)
        return merged

    @staticmethod
    def _strip_tags(source_tags: list[str], excluded_tags: list[str]) -> list[str]:
        excluded = {str(tag).strip().lower() for tag in excluded_tags if str(tag).strip()}
        result: list[str] = []
        for tag in source_tags:
            clean = str(tag).strip()
            if not clean or clean.lower() in excluded:
                continue
            result.append(clean)
        return result

    def _task_author_tags(self, task: UploadTask | None) -> list[str]:
        if task is None or task.task_type is not TaskType.DIFF_GROUP:
            return []
        return list(task.author_tags)

    def _effective_item_tags(self, task: UploadTask, item) -> tuple[list[str], bool]:
        base_tags, manual_cleared = self._item_base_tags(item)
        return self._merge_tags(base_tags, self._task_author_tags(task)), manual_cleared

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
                f"author_tags ({len(self._task_author_tags(task))}): {self._task_author_tags(task)}",
                f"effective_tags ({len(self._effective_item_tags(task, item)[0])}): {self._effective_item_tags(task, item)[0]}",
                f"final_tags_locked: {item.final_tags_locked}",
                f"parent_post_id: {item.parent_post_id}",
                f"created_post_id: {item.created_post_id}",
                f"error: {item.error_message}",
            ]
            self.detail.setPlainText("\n".join(lines))
            tag_source, _ = self._item_base_tags(item)
            if tag_source:
                self._set_tag_editor_text(tag_source)
            self._update_tag_count_display()
            if task.task_type is TaskType.DIFF_GROUP:
                self._set_author_tags_text(task.author_tags)
            else:
                self._set_author_tags_text([])
            return

    def _set_tag_editor_text(self, tags: list[str]) -> None:
        blocker = QSignalBlocker(self.tag_editor)
        try:
            self.tag_editor.setPlainText("\n".join(tags))
        finally:
            del blocker

    def _set_author_tags_text(self, tags: list[str]) -> None:
        blocker = QSignalBlocker(self.author_tags_editor)
        try:
            self.author_tags_editor.setPlainText("\n".join(tags))
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

    def _selected_item_tag_state(self):
        task, item = self._selected_item_context()
        if task is None or item is None:
            return None
        base_tags = self._parse_manual_tags(self.tag_editor.toPlainText())
        author_tags = self._task_author_tags(task)
        effective_tags = self._merge_tags(base_tags, author_tags)
        return task, item, base_tags, author_tags, effective_tags

    def _sync_effective_tags_if_changed(self, item, effective_tags: list[str]) -> bool:
        last_synced = self._last_synced_effective_tags_by_item.get(item.item_id)
        if last_synced == effective_tags:
            return False
        self.runner.send_tag_sync(item.item_id, effective_tags)
        self._last_synced_effective_tags_by_item[item.item_id] = list(effective_tags)
        return True

    def _flush_pending_local_tag_sync(self) -> None:
        state = self._selected_item_tag_state()
        if state is None:
            return
        task, item, base_tags, _author_tags, effective_tags = state
        if item.item_id not in self.pending_review_item_ids:
            return
        self.service.update_item_tags(task.task_id, item.item_id, base_tags)
        if self._sync_effective_tags_if_changed(item, effective_tags):
            self._append_log(f"本地标签已推送到网页：{item.file_name} ({len(effective_tags)} tags)")
            self._render_active_task()

    def _apply_manual_tags(self) -> None:
        state = self._selected_item_tag_state()
        if state is None:
            return
        task, item, base_tags, _author_tags, effective_tags = state
        self.service.update_item_tags(task.task_id, item.item_id, base_tags)
        changed = self._sync_effective_tags_if_changed(item, effective_tags)
        if changed:
            self._append_log(f"已更新标签：{item.file_name} ({len(effective_tags)} tags)")
            self._render_active_task()
        else:
            self._append_log(f"已更新标签：{item.file_name}（无变化）")

    def _reset_tags_from_detected(self) -> None:
        state = self._selected_item_tag_state()
        if state is None:
            return
        task, item, _base_tags, author_tags, _effective_tags = state
        base_tags = list(item.detected_tags)
        effective_tags = self._merge_tags(base_tags, author_tags)
        self.service.update_item_tags(task.task_id, item.item_id, base_tags)
        self._set_tag_editor_text(base_tags)
        if self._sync_effective_tags_if_changed(item, effective_tags):
            self._append_log(f"已还原检测标签：{item.file_name} ({len(effective_tags)} tags)")
            self._render_active_task()
        else:
            self._append_log(f"已还原检测标签：{item.file_name}")

    @staticmethod
    def _parse_manual_tags(raw: str) -> list[str]:
        import re

        tokens = [part.strip().replace(" ", "_") for part in re.split(r"[\n,]+", raw) if part.strip()]
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
        self._save_author_tags_from_ui()
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
        self._update_status_indicator("\u8fd0\u884c\u4e2d", "#4caf50")
        self._refresh_task_list()
        self._append_log(f"\u4efb\u52a1\u5f00\u59cb\uff1a{task.task_name}")

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
        self._update_status_indicator("\u5df2\u6682\u505c", "#ff9800")
        self._refresh_task_list()
        self._render_active_task()
        self._append_log("\u4efb\u52a1\u5df2\u6682\u505c")

    def _resume_task(self) -> None:
        task = self._active_task()
        if task is None:
            return
        if self.runner.is_running():
            self._append_log("任务正在运行")
            return
        self.service.set_task_status(task.task_id, TaskStatus.RUNNING, force=True)
        self.runner.start(task, self.settings)
        self._update_status_indicator("\u8fd0\u884c\u4e2d", "#4caf50")
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
        tags_override = None
        tags_override_allow_empty = False
        if action == "confirm":
            state = self._selected_item_tag_state()
            if state is not None:
                task, item, base_tags, _author_tags, effective_tags = state
                if len(effective_tags) < 20:
                    reply = QMessageBox.warning(
                        self, "标签不足",
                        f"当前仅有 {len(effective_tags)} 个标签。Sankaku 要求至少 20 个才能提交。\n\n是否仍然尝试强行提交？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.No:
                        return
                current_effective = self._last_synced_effective_tags_by_item.get(item.item_id)
                tags_override_allow_empty = self.tag_editor.toPlainText().strip() == ""
                self.service.update_item_tags(task.task_id, item.item_id, base_tags)
                if effective_tags != current_effective:
                    tags_override = effective_tags
                    self._last_synced_effective_tags_by_item[item.item_id] = list(effective_tags)
                    self._append_log(f"确认前应用标签：{item.file_name} ({len(effective_tags)} tags)")
                    self._render_active_task()

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
                self._update_status_indicator("\u7a7a\u95f2", "#3d596e")
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
        task = self._active_task() if self.active_task_id == task_id else None
        author_tags = self._task_author_tags(task)
        base_tags = self._strip_tags(tags, author_tags)
        self.pending_review_item_id = item_id
        self.pending_review_item_ids.add(item_id)
        self._set_review_buttons_enabled(True)
        self.service.update_item_result(
            task_id,
            item_id,
            status=ItemStatus.WAITING_USER_CONFIRM,
            detected_tags=base_tags,
            final_tags=base_tags,
        )
        self._last_synced_effective_tags_by_item[item_id] = list(tags)
        if self.queue_list.currentItem() is not None and str(self.queue_list.currentItem().data(Qt.ItemDataRole.UserRole)) == item_id:
            self._set_tag_editor_text(base_tags)
            if task is not None and task.task_type is TaskType.DIFF_GROUP:
                self._set_author_tags_text(author_tags)
        self._append_log(f"等待人工确认：{payload.get('file_name')} tags={tags}")
        self._render_active_task()

    def _on_item_review_update(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        item_id = str(payload.get("item_id") or "")
        tags = list(payload.get("ai_tags") or [])
        task = self._active_task() if self.active_task_id == task_id else None
        author_tags = self._task_author_tags(task)
        base_tags = self._strip_tags(tags, author_tags)
        self.pending_review_item_id = item_id
        self.pending_review_item_ids.add(item_id)
        self._set_review_buttons_enabled(True)
        self.service.update_item_result(task_id, item_id, status=ItemStatus.WAITING_USER_CONFIRM, final_tags=base_tags)
        self._last_synced_effective_tags_by_item[item_id] = list(tags)
        if item_id in self.pending_review_item_ids:
            current = self.queue_list.currentItem()
            if current is not None and str(current.data(Qt.ItemDataRole.UserRole)) == item_id:
                self._set_tag_editor_text(base_tags)
                if task is not None and task.task_type is TaskType.DIFF_GROUP:
                    self._set_author_tags_text(author_tags)
        self._append_log(f"网页标签已同步：{payload.get('file_name')} ({len(tags)} tags)")
        self._render_active_task()

    def _on_item_result(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        item_id = str(payload.get("item_id") or "")
        success = bool(payload.get("success"))
        post_id = str(payload.get("post_id") or "")
        error = str(payload.get("error") or "")
        is_duplicate = bool(payload.get("is_duplicate"))

        if is_duplicate:
            status = ItemStatus.DUPLICATE
            detail = f"[已存在] {payload.get('file_name')} -> 帖子 #{post_id}"
        elif success:
            status = ItemStatus.SUCCESS
            detail = f"完成：{payload.get('file_name')} (ID: {post_id})"
        else:
            status = ItemStatus.FAILED
            tag_state = str(payload.get("tag_state") or "")
            if tag_state == "tag_error":
                status = ItemStatus.TAG_ERROR
                detail = f"失败：{payload.get('file_name')} (需要手动检查 AI 标签)"
            else:
                detail = f"失败：{payload.get('file_name')} - {error}"

        if success:
            self.pending_review_item_ids.discard(item_id)
            if self.pending_review_item_id == item_id:
                self.pending_review_item_id = next(iter(self.pending_review_item_ids), None)

        self.service.update_item_result(
            task_id,
            item_id,
            status=status,
            final_tags=list(payload.get("ai_tags") or []),
            post_id=post_id,
            error=error,
        )
        self._append_log(detail)
        self._render_active_task()

    def _on_task_complete(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        has_failures = bool(payload.get("has_failures"))
        if has_failures:
            self.service.set_task_status(task_id, TaskStatus.PARTIAL_FAILED, force=True)
            self._append_log("\u4efb\u52a1\u5b8c\u6210\uff08\u90e8\u5206\u5931\u8d25\uff09")
        else:
            self.service.set_task_status(task_id, TaskStatus.COMPLETED, force=True)
            self._append_log("\u4efb\u52a1\u5b8c\u6210\uff08\u5168\u90e8\u6210\u529f\uff09")

        self.pending_review_item_id = None
        self.pending_review_item_ids.clear()
        self._set_review_buttons_enabled(False)
        self._refresh_task_list()
        self._render_active_task()

        # If we're in batch-upload mode, continue with the next queue
        if self._upload_all_queue:
            self._run_next_in_all_queue()

    def _update_status_indicator(self, text: str, color: str) -> None:
        self.status_indicator.setText(f'<span style="color: {color};">\u25cf</span> {text}')

    def _update_tag_count_display(self) -> None:
        tags = self._parse_manual_tags(self.tag_editor.toPlainText())
        count = len(tags)
        self.tag_count_label.setText(f"{count} / 20")
        if count < 20:
            self.tag_count_label.setStyleSheet("color: #f44336; font-weight: bold;")
        else:
            self.tag_count_label.setStyleSheet("color: #4caf50; font-weight: bold;")


def build_app() -> QApplication:
    return QApplication.instance() or QApplication([])

