"""What the buttons do, without Gradio. `forge` is lib_model_profiles.forge or a stand-in with the same functions (the
tests use one), so every rule here runs without a Forge install.

How a profile lives inside Forge:
- Applying a profile writes its settings into the preset's own options (the values in Settings > presets) and selects
  its model files through Forge. So Forge's preset *is* the profile in use: a page reload or a switch back to the
  preset brings it back, and model extensions that read the preset's options (MiniMax-H3, LTX, Idol) read the
  profile's values.
- Before the first write to a preset, its current settings are kept as a profile ("My Forge preset") when they are
  not Forge's factory values, so nothing the user set in Settings is lost.
- The star marks a preset's default: leaving the preset puts the starred profile back into it, so the next visit
  starts from the default and the others stay one click away. With no star nothing is restored (Forge as before).
"""

from __future__ import annotations

import json
import logging

from . import fields
from .catalog import FACTORY, USER, Catalog, scan
from .fields import TABS, ProfileError
from .store import Store

logger = logging.getLogger("model_profiles")
SNAPSHOT_NAME = "My Forge preset"


class Service:
    def __init__(self, forge, store: Store | None = None):
        self.forge = forge
        self.store = store if store is not None else Store(forge.data_file())
        self._suggestions = None

    # region catalog
    def suggestions(self) -> list[dict]:
        if self._suggestions is None:
            self._suggestions = scan(self.forge.extension_dirs())
        return self._suggestions

    def reload_suggestions(self):
        self._suggestions = None

    def catalog(self, arch: str) -> Catalog:
        factory = None
        try:
            factory = self.forge.factory_values(arch) or None
        except Exception as exc:
            logger.warning("Model Profiles: no factory values for %s: %s", arch, exc)
        return Catalog(arch, self.store.profiles(arch), self.suggestions(), factory, self.store.default(arch))

    def selected(self, arch: str) -> str | None:
        cat = self.catalog(arch)
        active = self.store.active(arch)
        return active if cat.get(active) else None

    # endregion

    # region apply
    def _snapshot(self, arch: str):
        """Keep the preset's settings as a profile before the extension first writes over them."""
        if self.store.has_snapshot(arch):
            return
        try:
            current = self.forge.preset_values(arch)
            factory = self.forge.factory_values(arch)
        except Exception as exc:
            logger.warning("Model Profiles: could not read the %s preset: %s", arch, exc)
            return
        if fields.settings_only(current) and fields.differences(fields.settings_only(current), factory):
            self.store.add(arch, SNAPSHOT_NAME, current)
            logger.info("Model Profiles: kept the %s preset settings as \"%s\"", arch, SNAPSHOT_NAME)
        self.store.mark_snapshot(arch)

    def apply(self, arch: str, profile_id: str) -> tuple[dict, dict, list[str]]:
        """Apply a profile to the preset on screen. Returns (values for the settings controls, values for the model
        dropdowns, warnings)."""
        item = self.catalog(arch).get(profile_id)
        if item is None:
            raise ProfileError("That profile is not available for this preset.")
        values, warnings = self.forge.usable(item["values"])
        settings = fields.settings_only(values)
        if settings:
            self._snapshot(arch)
            self.forge.write_settings(arch, settings)
        shown_model = {}
        if values.get("model"):
            shown_model, model_warnings = self.forge.apply_model(arch, values["model"])
            warnings += model_warnings
        if not shown_model:
            self.forge.save_options()  # apply_model saves only when it changed a model file
        self.store.set_active(arch, profile_id)
        return self.visible_settings(arch, settings), shown_model, warnings

    def visible_settings(self, arch: str, settings: dict) -> dict:
        """The settings limited to the controls this preset uses (no Shift slider value for SDXL, for example)."""
        keys = self.forge.option_keys(arch)
        return {tab: {f: v for f, v in (settings.get(tab) or {}).items() if fields.option_key(arch, tab, f) in keys}
                for tab in TABS}

    def reapply_settings(self, arch: str, profile_id: str) -> dict:
        """The settings again, when a model extension's panel changed some of them right after the checkpoint switch."""
        item = self.catalog(arch).get(profile_id)
        if item is None:
            return {tab: {} for tab in TABS}
        values, _ = self.forge.usable(item["values"])
        return self.visible_settings(arch, fields.settings_only(values))

    def leave(self, arch: str, restore: bool = True, save: bool = True) -> bool:
        """The user switched away from `arch`: put its starred profile back into the preset. True when it did (with
        `save=False` the caller saves Forge's options)."""
        default_id = self.store.default(arch)
        if not restore or not default_id or self.store.active(arch) == default_id:
            return False
        item = self.catalog(arch).get(default_id)
        if item is None:
            return False
        values, warnings = self.forge.usable(item["values"])
        for w in warnings:
            logger.warning("Model Profiles: %s", w)
        settings = fields.settings_only(values)
        if settings:
            self._snapshot(arch)
            self.forge.write_settings(arch, settings)
        if values.get("model"):
            self.forge.write_model_options(arch, values["model"])
        if save:
            self.forge.save_options()
        self.store.set_active(arch, default_id)
        return True

    # endregion

    # region editing
    def current_values(self, arch: str, model: dict, settings: dict) -> dict:
        """Canonical values from what the controls show; `settings` maps tab -> field -> value."""
        keys = self.forge.option_keys(arch)
        values: dict = {}
        m = {}
        if model.get("checkpoint"):
            m["checkpoint"] = self.forge.checkpoint_key(model["checkpoint"])
        if model.get("modules") is not None:
            m["modules"] = list(model["modules"])
        if model.get("dtype"):
            m["dtype"] = model["dtype"]
        if m:
            values["model"] = m
        for tab in TABS:
            tab_values = {f: v for f, v in (settings.get(tab) or {}).items()
                          if v is not None and fields.option_key(arch, tab, f) in keys}
            if tab_values:
                values[tab] = tab_values
        return fields.normalize(values)

    def _make_active(self, arch: str, profile: dict):
        settings = fields.settings_only(profile["values"])
        if settings:
            self._snapshot(arch)
            self.forge.write_settings(arch, settings)
            self.forge.save_options()
        self.store.set_active(arch, profile["id"])

    def command(self, arch: str, selected_id: str | None, op: str, name: str | None, current: dict) -> tuple[str | None, str]:
        """Run a menu command; returns (the profile to select, a message for the user)."""
        cat = self.catalog(arch)
        item = cat.get(selected_id)
        if op == "save":
            if item is None or item["kind"] != USER:
                raise ProfileError("Choose a name to save these settings as a new profile.")
            profile = self.store.update(arch, item["id"], current)
            self._make_active(arch, profile)
            return profile["id"], f"Saved \"{profile['name']}\"."
        if op == "save_new":
            profile = self.store.add(arch, name, current)
            self._make_active(arch, profile)
            return profile["id"], f"Saved \"{profile['name']}\" for {arch}."
        if item is None:
            raise ProfileError("Select a profile first.")
        if op == "default":
            self.store.set_default(arch, item["id"])
            return item["id"], f"\"{item['name']}\" is now the default for {arch}."
        if op == "undefault":
            self.store.set_default(arch, None)
            return item["id"], f"{arch} has no default profile now."
        if item["kind"] != USER:
            raise ProfileError("Suggestions cannot be changed; save a copy with \"Save as new\".")
        if op == "rename":
            profile = self.store.rename(arch, item["id"], name)
            return profile["id"], f"Renamed to \"{profile['name']}\"."
        if op == "delete":
            self.store.delete(arch, item["id"])
            return None, f"Deleted \"{item['name']}\"."
        raise ProfileError(f"Unknown command {op!r}.")

    # endregion

    # region infotext
    def infotext_name(self, arch: str, model: dict, current: dict) -> str | None:
        """The "Profile" value for a generation: the active profile, "(modified)" when the settings differ, nothing
        when another checkpoint is in use (then it is not that profile)."""
        item = self.catalog(arch).get(self.store.active(arch))
        if item is None:
            return None
        values = item["values"]
        want = (values.get("model") or {}).get("checkpoint")
        if want and model.get("checkpoint") and not self.forge.same_checkpoint(want, model["checkpoint"]):
            return None
        visible = self.visible_settings(arch, fields.settings_only(values))
        diffs = fields.differences(visible, current)
        return f"{item['name']} (modified)" if diffs else item["name"]

    # endregion

    # region page state
    def page_state(self, arch: str) -> str:
        """JSON for the page script: the menu entries and, for the selected profile, what each control should show
        (the script compares them to mark the profile as modified)."""
        cat = self.catalog(arch)
        selected = cat.get(self.store.active(arch))
        expect = {}
        if selected is not None:
            values, _ = self.forge.usable(selected["values"])
            for tab, tab_values in self.visible_settings(arch, fields.settings_only(values)).items():
                for f, v in tab_values.items():
                    expect[fields.ELEM_IDS[(tab, f)]] = v
            model = values.get("model") or {}
            if model.get("checkpoint"):
                info = self.forge.resolve_checkpoint(model["checkpoint"])
                if info is not None:
                    expect[fields.MODEL_ELEM_IDS["checkpoint"]] = self.forge.checkpoint_title(info)
            if model.get("dtype"):
                expect[fields.MODEL_ELEM_IDS["dtype"]] = model["dtype"]
        return json.dumps({
            "arch": arch,
            "items": cat.describe(),
            "selected": None if selected is None else {
                "id": selected["id"], "name": selected["name"], "kind": selected["kind"],
                "editable": selected["kind"] == USER, "factory": selected["kind"] == FACTORY,
                "default": selected["id"] == cat.default_id},
            "default": cat.default_id,
            "expect": expect,
        })

    # endregion
