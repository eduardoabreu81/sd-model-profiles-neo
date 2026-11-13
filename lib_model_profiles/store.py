"""The user's profiles, kept in one JSON file in Forge's data folder (next to styles.csv), so an update of the extension
never touches them.

    {"version": 1,
     "profiles": {"qwen21": [{"id": "u:1a2b3c4d", "name": "Turbo 8 steps", "values": {...}}]},
     "defaults": {"qwen21": "u:1a2b3c4d"},     # the profile marked with a star for each preset
     "active": {"qwen21": "s:qwen2.1-forge-neo:base"},   # the profile applied last in each preset
     "snapshots": ["qwen21"]}                  # presets whose Forge settings were saved before the first write

Profiles of a preset whose extension is gone stay in the file and come back with it.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid

from .fields import ProfileError, normalize
from .history import daily_copy, write_json

VERSION = 1
NAME_LIMIT = 60
logger = logging.getLogger("model_profiles")


def _empty() -> dict:
    return {"version": VERSION, "profiles": {}, "defaults": {}, "active": {}, "snapshots": []}


def clean_name(name) -> str:
    if not isinstance(name, str):
        raise ProfileError("A profile needs a name.")
    name = " ".join(name.split())
    if not name:
        raise ProfileError("A profile needs a name.")
    return name[:NAME_LIMIT]


class Store:
    def __init__(self, path: str, backup_dir: str | None = None):
        self.path = path
        self.backup_dir = backup_dir
        self.lock = threading.RLock()
        self.data = _empty()
        self.load()

    # region file
    def load(self):
        with self.lock:
            if not os.path.isfile(self.path):
                self.data = _empty()
                return
            try:
                with open(self.path, encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                # keep the unreadable file aside instead of overwriting it on the next save
                backup = f"{self.path}.broken"
                logger.error("Model Profiles: could not read %s (%s); it was kept as %s", self.path, exc, backup)
                try:
                    os.replace(self.path, backup)
                except OSError:
                    pass
                self.data = _empty()
                return
            self.data = self._validate(raw)

    def _validate(self, raw) -> dict:
        data = _empty()
        if not isinstance(raw, dict):
            return data
        for arch, items in (raw.get("profiles") or {}).items():
            if not isinstance(items, list):
                continue
            kept = []
            for item in items:
                try:
                    kept.append({"id": str(item["id"]), "name": clean_name(item["name"]),
                                 "values": normalize(item["values"], source=f"{arch}/{item.get('name')}"),
                                 "link": bool(item.get("link", False))})
                except (KeyError, TypeError, ProfileError) as exc:
                    logger.warning("Model Profiles: skipped a saved profile of %s: %s", arch, exc)
            data["profiles"][str(arch)] = kept
        for key in ("defaults", "active"):
            value = raw.get(key)
            if isinstance(value, dict):
                data[key] = {str(k): str(v) for k, v in value.items() if isinstance(v, str)}
        snapshots = raw.get("snapshots")
        if isinstance(snapshots, list):
            data["snapshots"] = sorted({str(s) for s in snapshots})
        return data

    def save(self):
        with self.lock:
            if os.path.isfile(self.path):
                daily_copy(self.path, self.backup_dir, "model_profiles")
                try:
                    shutil.copy2(self.path, f"{self.path}.bak")
                except OSError as exc:
                    logger.warning("Model Profiles: could not keep the previous profiles file: %s", exc)
            write_json(self.path, self.data)

    # endregion

    # region profiles
    def profiles(self, arch: str) -> list[dict]:
        return list(self.data["profiles"].get(arch, []))

    def get(self, arch: str, profile_id: str) -> dict | None:
        return next((p for p in self.data["profiles"].get(arch, []) if p["id"] == profile_id), None)

    def _unique(self, arch: str, name: str, ignore: str | None = None) -> str:
        taken = {p["name"].casefold() for p in self.data["profiles"].get(arch, []) if p["id"] != ignore}
        if name.casefold() not in taken:
            return name
        n = 2
        while f"{name} {n}".casefold() in taken:
            n += 1
        return f"{name} {n}"

    def add(self, arch: str, name: str, values: dict) -> dict:
        with self.lock:
            profile = {"id": f"u:{uuid.uuid4().hex[:8]}", "name": self._unique(arch, clean_name(name)),
                       "values": normalize(values), "link": False}
            self.data["profiles"].setdefault(arch, []).append(profile)
            self.save()
            return profile

    def update(self, arch: str, profile_id: str, values: dict) -> dict:
        with self.lock:
            profile = self._require(arch, profile_id)
            profile["values"] = normalize(values)
            self.save()
            return profile

    def set_link(self, arch: str, profile_id: str, link: bool) -> dict:
        with self.lock:
            profile = self._require(arch, profile_id)
            profile["link"] = bool(link)
            self.save()
            return profile

    def rename(self, arch: str, profile_id: str, name: str) -> dict:
        with self.lock:
            profile = self._require(arch, profile_id)
            profile["name"] = self._unique(arch, clean_name(name), ignore=profile_id)
            self.save()
            return profile

    def delete(self, arch: str, profile_id: str) -> dict:
        with self.lock:
            profile = self._require(arch, profile_id)
            self.data["profiles"][arch] = [p for p in self.data["profiles"][arch] if p["id"] != profile_id]
            for key in ("defaults", "active"):
                if self.data[key].get(arch) == profile_id:
                    del self.data[key][arch]
            self.save()
            return profile

    def _require(self, arch: str, profile_id: str) -> dict:
        profile = self.get(arch, profile_id)
        if profile is None:
            raise ProfileError("That profile no longer exists.")
        return profile

    # endregion

    # region state
    def default(self, arch: str) -> str | None:
        return self.data["defaults"].get(arch)

    def set_default(self, arch: str, profile_id: str | None):
        with self.lock:
            if profile_id:
                self.data["defaults"][arch] = profile_id
            else:
                self.data["defaults"].pop(arch, None)
            self.save()

    def active(self, arch: str) -> str | None:
        return self.data["active"].get(arch)

    def set_active(self, arch: str, profile_id: str | None):
        with self.lock:
            if self.data["active"].get(arch) == profile_id:
                return
            if profile_id:
                self.data["active"][arch] = profile_id
            else:
                self.data["active"].pop(arch, None)
            self.save()

    def has_snapshot(self, arch: str) -> bool:
        return arch in self.data["snapshots"]

    def mark_snapshot(self, arch: str):
        with self.lock:
            if arch not in self.data["snapshots"]:
                self.data["snapshots"] = sorted([*self.data["snapshots"], arch])
                self.save()

    # endregion
