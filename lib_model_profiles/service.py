"""What the buttons do, without Gradio. `forge` is lib_model_profiles.forge or a stand-in with the same functions (the
tests use one), so every rule here runs without a Forge install.

How a profile lives inside Forge:
- Applying a profile writes its settings into the preset's own options (the values in Settings > presets) and selects
  its model files through Forge. So Forge's preset *is* the profile in use: a page reload or a switch back to the
  preset brings it back, and model extensions that read the preset's options (MiniMax-H3, LTX, Idol) read the
  profile's values.
- Nothing is lost: before the first write to a preset, its settings become the profile "My Forge preset" when they
  are not Forge's factory values; every later overwrite is recorded in the history (restorable from the menu); and
  Forge's config.json and the profiles file get a daily backup.
- The star marks a preset's default: leaving the preset puts the starred profile back into it, so the next visit
  starts from the default and the others stay one click away. With no star nothing is restored (Forge as before).
- A profile can carry prompt tokens (LoRA tags): applying it adds them to the prompts, switching to another profile or
  preset removes exactly those tokens.
- A profile has a scope: one checkpoint (it holds the model files and is applied when that checkpoint is chosen in
  Forge's list), its preset (settings on top of any of the preset's checkpoints) or every preset.
"""

from __future__ import annotations

import json
import logging
import os

from . import fields
from .catalog import FACTORY, USER, Catalog, parse_suggestions, scan
from .fields import TABS, TOKEN_PARTS, ProfileError
from .history import PRESET as PRESET_ENTRY
from .history import PROFILE, History
from .store import CHECKPOINT, GLOBAL, GLOBAL_KEY, PRESET, SCOPES, Store

logger = logging.getLogger("model_profiles")
SNAPSHOT_NAME = "My Forge preset"
# the image size is the user's, not the profile's: saving from the controls leaves it out (a suggestion file may
# still set one)
UNSAVED_FIELDS = {"width", "height"}
# Shift and Distilled CFG mean different things in different presets: a profile for every preset leaves them out
PRESET_ONLY_FIELDS = {"dcfg", "hr_dcfg"}
SCOPE_NAMES = {CHECKPOINT: "this checkpoint", PRESET: "this preset", GLOBAL: "every preset"}
HISTORY_SHOWN = 15


def _tokens(item: dict | None) -> dict:
    return dict((item or {}).get("values", {}).get("tokens") or {})


class Result(dict):
    """settings, model, prompts (None when not touched), warnings, select"""


class Service:
    def __init__(self, forge, store: Store | None = None, history: History | None = None,
                 backup_dir: str | None = None):
        self.forge = forge
        data = forge.data_file() if store is None or history is None else None
        folder = os.path.dirname(data) if data else None
        self.backup_dir = backup_dir or (os.path.join(folder, "model_profiles_backups") if folder else None)
        self.store = store if store is not None else Store(data, backup_dir=self.backup_dir)
        self.history = history if history is not None else History(os.path.join(folder, "model_profiles_history.json"))
        self._suggestions = None

    # region catalog
    def suggestions(self) -> list[dict]:
        if self._suggestions is None:
            self._suggestions = scan(self.forge.extension_dirs())
        return self._suggestions

    def reload_suggestions(self):
        self._suggestions = None

    def current_checkpoint(self) -> str | None:
        try:
            return self.forge.current_checkpoint()
        except Exception:
            return None

    def catalog(self, arch: str, checkpoint: str | None = None) -> Catalog:
        """The list for `arch`, ordered for the checkpoint in use (`checkpoint`, or the one Forge has selected)."""
        factory = None
        try:
            factory = self.forge.factory_values(arch) or None
        except Exception as exc:
            logger.warning("Model Profiles: no factory values for %s: %s", arch, exc)
        return Catalog(arch, self.store.profiles(arch), self.suggestions(), factory, self.store.default(arch),
                       global_user=self.store.profiles(GLOBAL_KEY),
                       checkpoint=checkpoint if checkpoint is not None else self.current_checkpoint(),
                       same_checkpoint=self.forge.same_checkpoint)

    def selected(self, arch: str) -> str | None:
        active = self.store.active(arch)
        return active if self.catalog(arch).get(active) else None

    def active_item(self, arch: str) -> dict | None:
        return self.catalog(arch).get(self.store.active(arch))

    # endregion

    # region writing Forge's preset
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
        settings = fields.settings_only(current)
        if settings and (fields.changed(settings, factory) or fields.changed(factory, settings)):
            self.store.add(arch, SNAPSHOT_NAME, current)
            logger.info("Model Profiles: kept the %s preset settings as \"%s\"", arch, SNAPSHOT_NAME)
        self.store.mark_snapshot(arch)

    def _write_preset(self, arch: str, settings: dict, reason: str, model: dict | None = None):
        """Record what the preset holds, back up Forge's config once a day, then write. `model` is only for a preset
        that is not on screen (the one on screen gets its files through apply_model)."""
        if not settings and not model:
            return
        self._snapshot(arch)
        try:
            before = self.forge.preset_values(arch)
        except Exception:
            before = {}
        target = {**({"model": model} if model else {}), **settings}
        if before and fields.changed(target, before):
            active = self.active_item(arch)
            self.history.record(arch, PRESET_ENTRY, active["name"] if active else "Forge preset", before, reason)
        self.forge.backup_config(self.backup_dir)
        if settings:
            self.forge.write_settings(arch, settings)
        if model:
            self.forge.write_model_options(arch, model)

    # endregion

    # region apply
    def visible_settings(self, arch: str, settings: dict) -> dict:
        """The settings the controls should show: only the ones this preset uses (no Shift value for SDXL, for
        example) and without Forge's "keep the current value" zeros, which only go into the preset."""
        keys = self.forge.option_keys(arch)
        return fields.shown_settings({tab: {f: v for f, v in (settings.get(tab) or {}).items()
                                            if fields.option_key(arch, tab, f) in keys} for tab in TABS})

    @staticmethod
    def swap_prompts(prompts: dict | None, old: dict, new: dict) -> dict | None:
        if prompts is None:
            return None
        return {tab: {part: fields.swap_tokens((prompts.get(tab) or {}).get(part, ""), old.get(part, []),
                                               new.get(part, []))
                      for part in TOKEN_PARTS}
                for tab in TABS}

    def _apply_values(self, arch: str, values: dict, reason: str, prompts: dict | None, old_tokens: dict) -> Result:
        values, warnings = self.forge.usable(values)
        settings = fields.settings_only(values)
        self._write_preset(arch, settings, reason)
        shown_model = {}
        if values.get("model"):
            shown_model, model_warnings = self.forge.apply_model(arch, values["model"])
            warnings += model_warnings
        if not shown_model:
            self.forge.save_options()  # apply_model saves only when it changed a model file
        return Result(settings=self.visible_settings(arch, settings), model=shown_model, warnings=warnings,
                      prompts=self.swap_prompts(prompts, old_tokens, values.get("tokens") or {}))

    def apply(self, arch: str, profile_id: str, prompts: dict | None = None) -> Result:
        """Apply a profile to the preset on screen. `prompts` (tab -> prompt/negative text) get the profile's tokens."""
        item = self.catalog(arch).get(profile_id)
        if item is None:
            raise ProfileError("That profile is not available for this preset.")
        old = _tokens(self.active_item(arch))
        result = self._apply_values(arch, item["values"], f"before applying “{item['name']}”", prompts, old)
        self.store.set_active(arch, profile_id)
        result["select"] = profile_id
        return result

    def reapply_settings(self, arch: str, profile_id: str) -> dict:
        """The settings again, when a model extension's panel changed some of them right after the checkpoint switch."""
        item = self.catalog(arch).get(profile_id)
        if item is None:
            return {tab: {} for tab in TABS}
        values, _ = self.forge.usable(item["values"])
        return self.visible_settings(arch, fields.settings_only(values))

    def switch(self, previous: str | None, arch: str, prompts: dict | None = None, restore: bool = True,
               save: bool = True) -> tuple[bool, dict | None]:
        """The user switched UI Preset from `previous` to `arch`: the previous profile's tokens leave the prompts and
        the new one's come in; the previous preset gets its starred profile back. Returns (restored, prompts)."""
        if not previous or previous == arch:
            return False, None  # the page loading, not a switch: the prompts are left as they are
        old = _tokens(self.active_item(previous))
        new = _tokens(self.active_item(arch))
        restored = self.leave(previous, restore=restore, save=save)
        return restored, self.swap_prompts(prompts, old, new)

    def leave(self, arch: str, restore: bool = True, save: bool = True) -> bool:
        """Put the starred profile back into `arch`'s preset (which is no longer on screen). True when it did; with
        `save=False` the caller saves Forge's options."""
        default_id = self.store.default(arch)
        if not restore or not default_id or self.store.active(arch) == default_id:
            return False
        item = self.catalog(arch).get(default_id)
        if item is None:
            return False
        values, warnings = self.forge.usable(item["values"])
        for w in warnings:
            logger.warning("Model Profiles: %s", w)
        self._write_preset(arch, fields.settings_only(values), f"before leaving {arch} (★ “{item['name']}”"
                           " put back)", model=values.get("model"))
        if save:
            self.forge.save_options()
        self.store.set_active(arch, default_id)
        return True

    def linked(self, arch: str, checkpoint: str) -> str | None:
        """The profile to apply when `checkpoint` is chosen in Forge's list, or None."""
        if not checkpoint:
            return None
        candidates = [p for p in self.store.profiles(arch)
                      if p.get("scope") == CHECKPOINT and (p["values"].get("model") or {}).get("checkpoint")
                      and self.forge.same_checkpoint(p["values"]["model"]["checkpoint"], checkpoint)]
        if not candidates:
            return None
        ids = [p["id"] for p in candidates]
        for preferred in (self.store.active(arch), self.store.default(arch)):
            if preferred in ids:
                return preferred
        return ids[0]

    # endregion

    # region editing
    def current_values(self, arch: str, model: dict, settings: dict, tokens: dict | None = None,
                       scope: str = CHECKPOINT) -> dict:
        """Canonical values from what the controls show, kept as `scope` keeps them; `settings` maps
        tab -> field -> value."""
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
        if tokens:
            values["tokens"] = tokens
        for tab in TABS:
            tab_values = {f: v for f, v in (settings.get(tab) or {}).items()
                          if v is not None and fields.option_key(arch, tab, f) in keys}
            if tab_values:
                values[tab] = tab_values
        return self.for_scope(values, scope)

    @staticmethod
    def for_scope(values: dict, scope: str) -> dict:
        """What a profile of `scope` keeps of `values`: the model files only for a checkpoint profile, no Shift /
        Distilled CFG for every preset, never the image size."""
        if scope not in SCOPES:
            raise ProfileError(f"Unknown scope {scope!r}.")
        out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in values.items()}
        if scope == CHECKPOINT:
            if not (out.get("model") or {}).get("checkpoint"):
                raise ProfileError("Choose a checkpoint first: this profile is for one checkpoint.")
        else:
            out.pop("model", None)
        for tab in TABS:
            if out.get(tab):
                out[tab] = {f: v for f, v in out[tab].items() if f not in UNSAVED_FIELDS
                            and not (scope == GLOBAL and f in PRESET_ONLY_FIELDS)}
                if not out[tab]:
                    del out[tab]
        return fields.normalize(out)

    def _make_active(self, arch: str, profile: dict, reason: str):
        settings = fields.settings_only(profile["values"])
        if settings:
            self._write_preset(arch, settings, reason)
            self.forge.save_options()
        self.store.set_active(arch, profile["id"])

    def _keep_version(self, arch: str, profile: dict, reason: str):
        self.history.record(arch, PROFILE, profile["name"], profile["values"], reason, profile_id=profile["id"])

    def command(self, arch: str, selected_id: str | None, op: str, name: str | None = None, current: dict | None = None,
                data=None, screen: dict | None = None) -> dict:
        """Run a menu command; returns {"select": profile to select, "message": for the user, "export": file or None}.
        `screen` is what the controls show ({"model": ..., "settings": ...}); `current` the same already in canonical
        form."""
        opts = data if isinstance(data, dict) else {}

        def from_screen(scope):
            if screen is not None:
                return self.current_values(arch, screen.get("model") or {}, screen.get("settings") or {}, scope=scope)
            return self.for_scope(current or {}, scope)

        cat = self.catalog(arch)
        item = cat.get(selected_id)
        out = {"select": selected_id, "message": "", "export": None}
        if op == "save":
            if item is None or item["kind"] != USER:
                raise ProfileError("Choose a name to save these settings as a new profile.")
            home, profile = self.store.find(item["id"])
            values = from_screen(profile["scope"])
            if profile["values"].get("tokens"):
                values = {**values, "tokens": profile["values"]["tokens"]}
            self._keep_version(arch, profile, f"before saving \u201c{profile['name']}\u201d")
            profile = self.store.update(home, profile["id"], values)
            self._make_active(arch, profile, f"before saving \u201c{profile['name']}\u201d")
            return {**out, "select": profile["id"], "message": f"Saved \"{profile['name']}\"."}
        if op == "save_new":
            scope = opts.get("scope") or PRESET
            values = from_screen(scope)
            # a copy keeps the prompt tokens of the profile it starts from
            if item is not None and item["values"].get("tokens"):
                values = {**values, "tokens": item["values"]["tokens"]}
            profile = self.store.add(arch, name, values, scope=scope)
            self._make_active(arch, profile, f"before saving \u201c{profile['name']}\u201d")
            return {**out, "select": profile["id"],
                    "message": f"Saved \"{profile['name']}\" for {SCOPE_NAMES[scope]}."}
        if op == "export":
            exported = cat.export()
            if not exported["profiles"]:
                raise ProfileError(f"There are no profiles of yours for {arch} to export.")
            return {**out, "export": exported, "message": f"Exported {len(exported['profiles'])} profile(s) of {arch}."}
        if op == "import":
            return {**out, "message": self.import_profiles(data if isinstance(data, str) else None)}
        if item is None:
            raise ProfileError("Select a profile first.")
        if op == "default":
            self.store.set_default(arch, item["id"])
            return {**out, "message": f"\"{item['name']}\" is now the default for {arch}."}
        if op == "undefault":
            self.store.set_default(arch, None)
            return {**out, "message": f"{arch} has no default profile now."}
        if item["kind"] != USER:
            raise ProfileError("Suggestions cannot be changed; save a copy with \"Save as new\".")
        home, profile = self.store.find(item["id"])
        if profile is None:
            raise ProfileError("That profile no longer exists.")
        if op == "rename":
            profile = self.store.rename(home, profile["id"], name)
            return {**out, "select": profile["id"], "message": f"Renamed to \"{profile['name']}\"."}
        if op == "delete":
            self._keep_version(arch, profile, f"before deleting \u201c{profile['name']}\u201d")
            self.store.delete(home, profile["id"])
            return {**out, "select": None, "message": f"Deleted \"{profile['name']}\". It can be restored from History."}
        if op == "scope":
            scope = opts.get("scope")
            values = dict(profile["values"])
            if scope == CHECKPOINT and not (values.get("model") or {}).get("checkpoint"):
                # taking it to one checkpoint: the one on screen, with its VAE / Text Encoder and Low Bits
                values["model"] = from_screen(CHECKPOINT).get("model")
            values = self.for_scope(values, scope)
            self._keep_version(arch, profile, f"before making it a profile for {SCOPE_NAMES.get(scope, scope)}")
            profile = self.store.set_scope(profile["id"], scope, arch, values)
            return {**out, "select": profile["id"],
                    "message": f"\"{profile['name']}\" is now a profile for {SCOPE_NAMES[scope]}."}
        if op == "tokens":
            values = dict(profile["values"])
            tokens = {part: fields.split_tokens(opts.get(part) or "") for part in TOKEN_PARTS}
            tokens = {k: v for k, v in tokens.items() if v}
            if tokens:
                values["tokens"] = tokens
            else:
                values.pop("tokens", None)
            if fields.is_empty(values):
                raise ProfileError("A profile needs at least one setting.")
            self._keep_version(arch, profile, "before changing the prompt tokens")
            self.store.update(home, profile["id"], values)
            listed = ", ".join(tokens.get("prompt", []) + tokens.get("negative", [])) or "none"
            return {**out, "message": f"Prompt tokens of \"{profile['name']}\": {listed}. Apply the profile to add them."}
        raise ProfileError(f"Unknown command {op!r}.")

    def import_profiles(self, text) -> str:
        """Add the profiles of an exported file (or any suggestion file) as the user's own, for their presets."""
        if not isinstance(text, str) or not text.strip():
            raise ProfileError("The file is empty.")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProfileError(f"Not a profiles file: {exc}") from None
        entries = parse_suggestions(raw, "import")
        if not entries:
            raise ProfileError("No usable profiles in the file.")
        counts: dict = {}
        for entry in entries:
            if entry["arch"] == GLOBAL_KEY:
                scope = GLOBAL
            elif entry.get("scope") in (CHECKPOINT, PRESET):
                scope = entry["scope"]
            else:
                scope = CHECKPOINT if (entry["values"].get("model") or {}).get("checkpoint") else PRESET
            try:
                values = self.for_scope(entry["values"], scope)
            except ProfileError:
                scope, values = PRESET, self.for_scope(entry["values"], PRESET)
            self.store.add(entry["arch"], entry["name"], values, scope=scope)
            label = "every preset" if scope == GLOBAL else entry["arch"]
            counts[label] = counts.get(label, 0) + 1
        return "Imported " + ", ".join(f"{n} for {arch}" for arch, n in counts.items()) + "."

    # endregion

    # region history
    def history_entries(self, arch: str) -> list[dict]:
        return self.history.entries(arch)[:HISTORY_SHOWN]

    def restore(self, arch: str, entry_id: str, prompts: dict | None = None) -> Result:
        entry = self.history.get(arch, entry_id)
        if entry is None:
            raise ProfileError("That history entry is gone.")
        when = entry["at"].replace("T", " ")[:16]
        if entry["kind"] == PROFILE:
            home, profile = self.store.find(entry.get("profile_id") or "")
            if profile is not None:
                self._keep_version(arch, profile, f"before restoring the version of {when}")
                self.store.update(home, profile["id"], entry["values"])
            else:
                profile = self.store.add(arch, entry["name"], entry["values"])
            result = self.apply(arch, profile["id"], prompts)
            result["message"] = f"Restored \"{profile['name']}\" as of {when}."
            return result
        old = _tokens(self.active_item(arch))
        result = self._apply_values(arch, entry["values"], f"before restoring the preset of {when}", prompts, old)
        self.store.set_active(arch, None)
        result["select"] = None
        result["message"] = f"Restored the {arch} preset as of {when}."
        return result

    # endregion

    # region infotext
    def infotext_name(self, arch: str, model: dict, current: dict) -> str | None:
        """The "Profile" value for a generation: the active profile, "(modified)" when the settings differ, nothing
        when another checkpoint is in use (then it is not that profile)."""
        item = self.active_item(arch)
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
    def page_state(self, arch: str, export: dict | None = None) -> str:
        """JSON for the page script: the menu entries, the history and, for the selected profile, what each control
        should show (the script compares them to mark the profile as modified)."""
        cat = self.catalog(arch)
        selected = cat.get(self.store.active(arch))
        expect = {}
        sel = None
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
            sel = {"id": selected["id"], "name": selected["name"], "kind": selected["kind"],
                   "editable": selected["kind"] == USER, "factory": selected["kind"] == FACTORY,
                   "default": selected["id"] == cat.default_id, "tokens": values.get("tokens") or {},
                   "scope": selected.get("scope"), "has_checkpoint": bool(model.get("checkpoint"))}
        current = self.current_checkpoint()
        history = [{"id": e["id"], "at": e["at"].replace("T", " ")[:16], "kind": e["kind"], "name": e["name"],
                    "reason": e["reason"], "summary": fields.summary(e["values"], self.forge.dcfg_label(arch) or "Shift")}
                   for e in self.history_entries(arch)]
        if selected is None:
            current = self.current_checkpoint()
        short = (current or "").replace("\\", "/").rsplit("/", 1)[-1].split(" [")[0]
        return json.dumps({"arch": arch, "items": cat.describe(), "selected": sel, "default": cat.default_id,
                           "expect": expect, "history": history, "export": export, "checkpoint": short})

    # endregion
