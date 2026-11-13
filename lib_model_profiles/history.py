"""History and backups, so writing into Forge's presets never loses anything.

- History (`model_profiles_history.json`): before a preset's settings are overwritten, what it held is recorded; so is
  the previous version of a profile that is saved over or deleted. The last HISTORY_LIMIT entries of each preset are
  kept and any of them can be restored from the menu.
- Backups (`model_profiles_backups/`): one copy a day of Forge's config.json before the extension first writes to it,
  and of the profiles file before it changes, the last BACKUP_DAYS days of each. The profiles file also keeps its
  previous version as `model_profiles.json.bak`.
"""

from __future__ import annotations

import datetime
import glob
import json
import logging
import os
import shutil
import tempfile
import threading
import uuid

from .fields import ProfileError, normalize

HISTORY_LIMIT = 30
BACKUP_DAYS = 14
logger = logging.getLogger("model_profiles")

PRESET, PROFILE = "preset", "profile"


def write_json(path: str, data):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".model_profiles.", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def daily_copy(src: str, folder: str | None, prefix: str, keep: int = BACKUP_DAYS, today: str | None = None) -> str | None:
    """Copy `src` to `folder/prefix-YYYYMMDD.json` once a day; keep the newest `keep` copies. Returns the new copy."""
    if not folder or not os.path.isfile(src):
        return None
    day = today or datetime.date.today().strftime("%Y%m%d")
    target = os.path.join(folder, f"{prefix}-{day}.json")
    if os.path.exists(target):
        return None
    try:
        os.makedirs(folder, exist_ok=True)
        shutil.copy2(src, target)
        old = sorted(glob.glob(os.path.join(folder, f"{prefix}-*.json")))
        for path in old[:-keep]:
            os.remove(path)
        return target
    except OSError as exc:
        logger.warning("Model Profiles: could not back up %s: %s", src, exc)
        return None


class History:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.data: dict[str, list] = {}
        self.load()

    def load(self):
        with self.lock:
            self.data = {}
            if not os.path.isfile(self.path):
                return
            try:
                with open(self.path, encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Model Profiles: history not readable (%s); starting a new one", exc)
                return
            for arch, items in (raw.get("presets") or {}).items() if isinstance(raw, dict) else []:
                kept = []
                for item in items if isinstance(items, list) else []:
                    try:
                        kept.append({"id": str(item["id"]), "at": str(item["at"]), "kind": str(item["kind"]),
                                     "name": str(item.get("name", "")), "reason": str(item.get("reason", "")),
                                     "profile_id": item.get("profile_id"), "values": normalize(item["values"])})
                    except (KeyError, TypeError, ProfileError):
                        continue
                self.data[str(arch)] = kept

    def save(self):
        with self.lock:
            write_json(self.path, {"version": 1, "presets": self.data})

    def record(self, arch: str, kind: str, name: str, values: dict, reason: str, profile_id: str | None = None):
        if not values:
            return None
        with self.lock:
            items = self.data.setdefault(arch, [])
            if items and items[0]["kind"] == kind and items[0]["values"] == values and items[0]["name"] == name:
                return items[0]  # the same state twice in a row is one entry
            entry = {"id": uuid.uuid4().hex[:8], "at": datetime.datetime.now().isoformat(timespec="seconds"),
                     "kind": kind, "name": name, "reason": reason, "profile_id": profile_id, "values": values}
            items.insert(0, entry)
            del items[HISTORY_LIMIT:]
            try:
                self.save()
            except OSError as exc:
                logger.warning("Model Profiles: could not save the history: %s", exc)
            return entry

    def entries(self, arch: str) -> list[dict]:
        return list(self.data.get(arch, []))

    def get(self, arch: str, entry_id: str) -> dict | None:
        return next((e for e in self.data.get(arch, []) if e["id"] == entry_id), None)
