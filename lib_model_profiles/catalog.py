"""Everything the Profile dropdown lists for one UI Preset: the user's profiles, then the suggestions.

Suggestions come from two places:
- "Forge default": the factory values of the preset's options, so it is right for any preset, including the ones
  model extensions add at runtime (and their own defaults, such as the 2048 sizes of MiniMax-H3-Image).
- a `model_profiles.json` in the folder of any enabled extension, this one included. A model extension can ship the
  settings its model wants; without Model Profiles installed the file is simply ignored.

    {"version": 1,
     "profiles": [
       {"preset": "qwen21", "name": "Base", "values": {"sampler": "Euler", "scheduler": "Simple", "steps": 20, "cfg": 2.5}},
       {"preset": "h3i", "name": "H3-Image LCM", "values": {"sampler": "H3-Image LCM", "steps": 6, "cfg": 1,
                                                          "img2img": {"steps": 8}}}
     ]}

Top-level settings apply to both tabs; "txt2img" / "img2img" objects override them; "checkpoint", "modules" and
"dtype" pick the model files. Suggestions are read-only: saving one makes a copy among the user's profiles.
"""

from __future__ import annotations

import json
import logging
import os
import re

from .fields import ProfileError, normalize

SUGGESTION_FILE = "model_profiles.json"
FORGE_SOURCE = "Forge"
logger = logging.getLogger("model_profiles")

USER, SUGGESTION, FACTORY = "user", "suggestion", "factory"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "profile"


def factory_id(arch: str) -> str:
    return f"f:{arch}"


def read_suggestions(path: str, source: str) -> list[dict]:
    """Suggestions of one `model_profiles.json`; a broken entry is skipped with a warning, never fatal."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Model Profiles: could not read %s: %s", path, exc)
        return []
    try:
        return parse_suggestions(raw, source, where=path)
    except ProfileError as exc:
        logger.warning("Model Profiles: %s", exc)
        return []


def parse_suggestions(raw, source: str, where: str = "the file") -> list[dict]:
    """Entries of a suggestion file (also the format of exported profiles); bad entries are skipped with a warning."""
    items = raw.get("profiles") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ProfileError(f"{where} has no \"profiles\" list")
    path = where
    out, seen = [], set()
    for n, item in enumerate(items, 1):
        try:
            if not isinstance(item, dict):
                raise ProfileError("entry is not an object")
            arch = item.get("preset") or item.get("arch")
            name = item.get("name")
            if not isinstance(arch, str) or not arch.strip():
                raise ProfileError("missing \"preset\"")
            if not isinstance(name, str) or not name.strip():
                raise ProfileError("missing \"name\"")
            arch, name = arch.strip(), " ".join(name.split())
            profile_id = f"s:{source}:{arch}:{slug(name)}"
            if profile_id in seen:
                raise ProfileError(f"\"{name}\" appears twice for {arch}")
            seen.add(profile_id)
            out.append({"id": profile_id, "arch": arch, "name": name, "kind": SUGGESTION, "source": source,
                        "values": normalize(item.get("values"), source=f"{source} #{n}")})
        except ProfileError as exc:
            logger.warning("Model Profiles: skipped suggestion #%d of %s: %s", n, path, exc)
    return out


def scan(extension_dirs: list[tuple[str, str]]) -> list[dict]:
    """Suggestions of every (name, folder) given, in that order."""
    out = []
    for name, folder in extension_dirs:
        path = os.path.join(folder, SUGGESTION_FILE)
        if os.path.isfile(path):
            out.extend(read_suggestions(path, name))
    return out


class Catalog:
    """The list for one preset at one moment; built per request, it is cheap."""

    def __init__(self, arch: str, user: list[dict], suggestions: list[dict], factory: dict | None,
                 default_id: str | None = None):
        self.arch = arch
        self.default_id = default_id
        self.items: list[dict] = [dict(p, kind=USER, arch=arch) for p in user]
        self.items += [s for s in suggestions if s["arch"] == arch]
        if factory:
            self.items.append({"id": factory_id(arch), "arch": arch, "name": "Forge default", "kind": FACTORY,
                               "source": FORGE_SOURCE, "values": factory})

    def get(self, profile_id: str | None) -> dict | None:
        return next((p for p in self.items if p["id"] == profile_id), None)

    def find_name(self, name: str) -> dict | None:
        name = name.strip().casefold()
        return next((p for p in self.items if p["name"].casefold() == name), None)

    def label(self, item: dict) -> str:
        star = "★ " if item["id"] == self.default_id else ""
        if item["kind"] == USER:
            return f"{star}{item['name']}"
        return f"{star}{item['name']} · {item['source']}"

    def choices(self) -> list[tuple[str, str]]:
        return [(self.label(p), p["id"]) for p in self.items]

    def export(self) -> dict:
        """The user's profiles of this preset in the suggestion-file format, so an export can also be shipped by an
        extension as its model_profiles.json."""
        return {"version": 1, "profiles": [{"preset": self.arch, "name": p["name"], "values": p["values"]}
                                           for p in self.items if p["kind"] == USER]}

    def describe(self) -> list[dict]:
        """What the page script needs for its menu."""
        return [{"id": p["id"], "name": p["name"], "kind": p["kind"], "source": p.get("source", ""),
                 "default": p["id"] == self.default_id} for p in self.items]
