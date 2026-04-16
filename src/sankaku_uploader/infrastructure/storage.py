from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

from sankaku_uploader.domain import Settings, UploadTask


class JsonRepository:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or (Path.home() / ".sankaku-uploader" / "v2")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_file = self.base_dir / "tasks.json"
        self.settings_file = self.base_dir / "settings.json"

    def load_tasks(self) -> list[UploadTask]:
        data = self._read_json(self.tasks_file, default=[])
        return [UploadTask.from_dict(raw) for raw in data]

    def save_tasks(self, tasks: list[UploadTask]) -> None:
        payload = [task.to_dict() for task in tasks]
        self._write_json(self.tasks_file, payload)

    def load_settings(self) -> Settings:
        data = self._read_json(self.settings_file, default={})
        return Settings.from_dict(data)

    def save_settings(self, settings: Settings) -> None:
        self._write_json(self.settings_file, settings.to_dict())

    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
