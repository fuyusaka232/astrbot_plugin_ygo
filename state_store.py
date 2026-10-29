from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _default(self) -> dict[str, Any]:
        return {"daily_targets": [], "daily_history": []}

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self._default()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return self._default()
            if not isinstance(data, dict):
                return self._default()
            data.setdefault("daily_targets", [])
            data.setdefault("daily_history", [])
            if not isinstance(data["daily_targets"], list):
                data["daily_targets"] = []
            if not isinstance(data["daily_history"], list):
                data["daily_history"] = []
            return data

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)

    def targets(self) -> list[str]:
        data = self.load()
        return [str(item) for item in data.get("daily_targets", []) if item]

    def add_target(self, umo: str) -> bool:
        umo = str(umo or "").strip()
        if not umo:
            return False
        data = self.load()
        targets = data.setdefault("daily_targets", [])
        if umo in targets:
            return False
        targets.append(umo)
        self.save(data)
        return True

    def remove_target(self, umo: str) -> bool:
        data = self.load()
        targets = data.setdefault("daily_targets", [])
        if umo not in targets:
            return False
        targets.remove(umo)
        self.save(data)
        return True

    def clear_targets(self) -> None:
        data = self.load()
        data["daily_targets"] = []
        self.save(data)

    def recent_ids(self, days: int) -> set[int]:
        days = max(1, int(days))
        cutoff = dt.date.today() - dt.timedelta(days=days)
        result: set[int] = set()
        for entry in self.load().get("daily_history", []):
            if not isinstance(entry, dict):
                continue
            try:
                entry_date = dt.date.fromisoformat(str(entry.get("date")))
                card_id = int(entry.get("id"))
            except (TypeError, ValueError):
                continue
            if entry_date >= cutoff:
                result.add(card_id)
        return result

    def record_daily(self, card_id: int, name: str, date_str: str, keep_days: int = 30) -> None:
        data = self.load()
        history = data.setdefault("daily_history", [])
        history = [
            entry
            for entry in history
            if not (isinstance(entry, dict) and str(entry.get("date")) == date_str)
        ]
        history.append({"date": date_str, "id": int(card_id), "name": str(name)})
        cutoff = dt.date.today() - dt.timedelta(days=max(14, keep_days))
        trimmed = []
        for entry in history:
            try:
                entry_date = dt.date.fromisoformat(str(entry.get("date")))
            except (TypeError, ValueError):
                continue
            if entry_date >= cutoff:
                trimmed.append(entry)
        data["daily_history"] = trimmed
        self.save(data)
