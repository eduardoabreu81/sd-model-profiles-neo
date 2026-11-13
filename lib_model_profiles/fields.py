"""What a profile holds and how it maps to Forge Neo's own preset options.

A profile is the same set of values Forge keeps for each UI Preset (modules_forge/presets.py), in a canonical form:

    {"model": {"checkpoint": str, "modules": [str], "dtype": str},
     "txt2img": {"sampler": str, "scheduler": str, "steps": int, ...},
     "img2img": {...}}

Every field is optional: a missing field leaves the current value alone, as Forge's own presets do with 0. Nothing here
imports Forge or Gradio, so the tests run on their own.
"""

from __future__ import annotations

import math
from typing import Any

TABS = ("txt2img", "img2img")
TAB_KEYS = {"txt2img": "t2i", "img2img": "i2i"}

# field -> suffix of Forge's preset option ("{arch}_{t2i|i2i}_{suffix}")
OPTION_SUFFIX = {
    "sampler": "sampler",
    "scheduler": "scheduler",
    "steps": "step",
    "hr_steps": "hr_step",
    "cfg": "cfg",
    "hr_cfg": "hr_cfg",
    "dcfg": "dcfg",
    "hr_dcfg": "hr_dcfg",
    "width": "width",
    "height": "height",
}

# the fields each tab has in Forge's presets (img2img has no Hires. fix)
TAB_FIELDS = {
    "txt2img": ("sampler", "scheduler", "steps", "hr_steps", "cfg", "hr_cfg", "dcfg", "hr_dcfg", "width", "height"),
    "img2img": ("sampler", "scheduler", "steps", "cfg", "dcfg", "width", "height"),
}

# Forge's elem_id for each field's component
ELEM_IDS = {
    ("txt2img", "sampler"): "txt2img_sampling",
    ("txt2img", "scheduler"): "txt2img_scheduler",
    ("txt2img", "steps"): "txt2img_steps",
    ("txt2img", "hr_steps"): "txt2img_hires_steps",
    ("txt2img", "cfg"): "txt2img_cfg_scale",
    ("txt2img", "hr_cfg"): "txt2img_hr_cfg",
    ("txt2img", "dcfg"): "txt2img_distilled_cfg_scale",
    ("txt2img", "hr_dcfg"): "txt2img_hr_distilled_cfg",
    ("txt2img", "width"): "txt2img_width",
    ("txt2img", "height"): "txt2img_height",
    ("img2img", "sampler"): "img2img_sampling",
    ("img2img", "scheduler"): "img2img_scheduler",
    ("img2img", "steps"): "img2img_steps",
    ("img2img", "cfg"): "img2img_cfg_scale",
    ("img2img", "dcfg"): "img2img_distilled_cfg_scale",
    ("img2img", "width"): "img2img_width",
    ("img2img", "height"): "img2img_height",
}

MODEL_ELEM_IDS = {"checkpoint": "setting_sd_model_checkpoint", "modules": "setting_sd_modules", "dtype": "forge_ui_dtype"}
MODEL_OPTION = {"checkpoint": "forge_checkpoint_{arch}", "modules": "forge_additional_modules_{arch}",
                "dtype": "forge_unet_storage_dtype_{arch}"}

INT_FIELDS = {"steps", "hr_steps", "width", "height"}
FLOAT_FIELDS = {"cfg", "hr_cfg", "dcfg", "hr_dcfg"}
STR_FIELDS = {"sampler", "scheduler"}

# names a suggestion file may use for a field, besides the canonical one
ALIASES = {"shift": "dcfg", "distilled_cfg": "dcfg", "hr_shift": "hr_dcfg", "hr_distilled_cfg": "hr_dcfg",
           "schedule": "scheduler", "cfg_scale": "cfg", "hires_steps": "hr_steps"}


class ProfileError(ValueError):
    pass


def option_key(arch: str, tab: str, field: str) -> str:
    return f"{arch}_{TAB_KEYS[tab]}_{OPTION_SUFFIX[field]}"


def model_option_key(arch: str, field: str) -> str:
    return MODEL_OPTION[field].format(arch=arch)


def _coerce(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in INT_FIELDS:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ProfileError(f"{field} must be a number, got {value!r}")
        return int(round(value))
    if field in FLOAT_FIELDS:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ProfileError(f"{field} must be a number, got {value!r}")
        return float(value)
    if field in STR_FIELDS:
        if not isinstance(value, str) or not value.strip():
            raise ProfileError(f"{field} must be a name, got {value!r}")
        return value.strip()
    raise ProfileError(f"unknown field {field!r}")


def _tab_values(raw: dict, tab: str, source: str) -> dict:
    out = {}
    for key, value in raw.items():
        field = ALIASES.get(key, key)
        if field not in TAB_FIELDS[tab]:
            if field in OPTION_SUFFIX:
                continue  # a txt2img-only field given as shorthand is simply not used by img2img
            raise ProfileError(f"{source}: unknown setting {key!r}")
        coerced = _coerce(field, value)
        if coerced is not None:
            out[field] = coerced
    return out


def normalize(values: dict, source: str = "profile") -> dict:
    """Canonical form of a profile's values. Accepts the canonical form or the shorthand of suggestion files, where
    top-level settings apply to both tabs and "txt2img"/"img2img" objects override them."""
    if not isinstance(values, dict):
        raise ProfileError(f"{source}: values must be an object")
    model_raw = dict(values.get("model") or {})
    for key in ("checkpoint", "modules", "dtype"):
        if key in values:
            model_raw[key] = values[key]
    model = {}
    if model_raw.get("checkpoint"):
        if not isinstance(model_raw["checkpoint"], str):
            raise ProfileError(f"{source}: checkpoint must be a name")
        model["checkpoint"] = model_raw["checkpoint"].strip()
    if model_raw.get("modules") is not None:
        mods = model_raw["modules"]
        if isinstance(mods, str):
            mods = [mods]
        if not isinstance(mods, list) or not all(isinstance(m, str) for m in mods):
            raise ProfileError(f"{source}: modules must be a list of file names")
        model["modules"] = sorted({m.replace("\\", "/").rsplit("/", 1)[-1] for m in mods if m.strip()})
    if model_raw.get("dtype"):
        if not isinstance(model_raw["dtype"], str):
            raise ProfileError(f"{source}: dtype must be a name")
        model["dtype"] = model_raw["dtype"].strip()

    shared = {k: v for k, v in values.items() if k not in ("model", "checkpoint", "modules", "dtype", *TABS)}
    out: dict = {}
    if model:
        out["model"] = model
    for tab in TABS:
        merged = dict(shared)
        override = values.get(tab) or {}
        if not isinstance(override, dict):
            raise ProfileError(f"{source}: {tab} must be an object")
        merged.update(override)
        tab_values = _tab_values(merged, tab, source)
        if tab_values:
            out[tab] = tab_values
    if not out:
        raise ProfileError(f"{source}: no settings")
    return out


def is_empty(values: dict) -> bool:
    return not any(values.get(part) for part in ("model", *TABS))


def from_options(arch: str, options: dict, keys: set | None = None) -> dict:
    """Profile values read from Forge's preset options for `arch` (`options` maps option key to value). Values Forge
    treats as "keep the current one" (0 for steps, CFG and sizes, empty names) are left out."""
    out: dict = {}
    model = {}
    ckpt = options.get(model_option_key(arch, "checkpoint"))
    if isinstance(ckpt, str) and ckpt.strip():
        model["checkpoint"] = ckpt.strip()
    mods = options.get(model_option_key(arch, "modules"))
    if isinstance(mods, list) and mods:
        model["modules"] = sorted({str(m).replace("\\", "/").rsplit("/", 1)[-1] for m in mods})
    dtype = options.get(model_option_key(arch, "dtype"))
    if isinstance(dtype, str) and dtype:
        model["dtype"] = dtype
    if model:
        out["model"] = model
    for tab in TABS:
        tab_values = {}
        for field in TAB_FIELDS[tab]:
            key = option_key(arch, tab, field)
            if keys is not None and key not in keys:
                continue
            if key not in options:
                continue
            value = options[key]
            if field in STR_FIELDS:
                if isinstance(value, str) and value.strip():
                    tab_values[field] = value.strip()
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                tab_values[field] = _coerce(field, value)
        if tab_values:
            out[tab] = tab_values
    return out


def to_options(arch: str, values: dict, keys: set) -> dict:
    """Forge preset options to write for `values`, limited to the option keys that exist for `arch` (`keys`). Model
    fields are left out: they go through Forge's own checkpoint_change / modules_change / dtype_change."""
    out = {}
    for tab in TABS:
        for field, value in (values.get(tab) or {}).items():
            key = option_key(arch, tab, field)
            if key in keys:
                out[key] = value
    return out


def settings_only(values: dict) -> dict:
    return {tab: dict(values[tab]) for tab in TABS if values.get(tab)}


def _same(field: str, a: Any, b: Any) -> bool:
    if field in FLOAT_FIELDS:
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False
    if field in INT_FIELDS:
        try:
            return int(round(float(a))) == int(round(float(b)))
        except (TypeError, ValueError):
            return False
    return a == b


def differences(profile: dict, current: dict, same_checkpoint=None) -> list[str]:
    """Fields the profile sets that `current` (same canonical shape) does not match. `same_checkpoint(a, b)` compares
    checkpoint names (Forge accepts several spellings of one file); it defaults to equality."""
    diffs = []
    model = profile.get("model") or {}
    cur_model = current.get("model") or {}
    if "checkpoint" in model and "checkpoint" in cur_model:
        same = same_checkpoint or (lambda a, b: a == b)
        if not same(model["checkpoint"], cur_model["checkpoint"]):
            diffs.append("checkpoint")
    if "modules" in model and "modules" in cur_model and sorted(model["modules"]) != sorted(cur_model["modules"]):
        diffs.append("modules")
    if "dtype" in model and "dtype" in cur_model and model["dtype"] != cur_model["dtype"]:
        diffs.append("dtype")
    for tab in TABS:
        cur_tab = current.get(tab) or {}
        for field, value in (profile.get(tab) or {}).items():
            if field in cur_tab and not _same(field, value, cur_tab[field]):
                diffs.append(f"{tab}.{field}")
    return diffs


def summary(values: dict, dcfg_label: str = "Shift") -> str:
    """One line for menus and logs: the txt2img settings and the checkpoint. `dcfg_label` names Forge's third slider
    for the arch ("Shift" or "Distilled CFG")."""
    parts = []
    ckpt = (values.get("model") or {}).get("checkpoint")
    if ckpt:
        name = ckpt.replace("\\", "/").rsplit("/", 1)[-1]
        parts.append(name.split(" [")[0].rsplit(".", 1)[0] if name.endswith((".safetensors", ".gguf", ".ckpt", ".sft"))
                     or " [" in name else name)
    tab = values.get("txt2img") or values.get("img2img") or {}
    for field in ("sampler", "scheduler"):
        if field in tab:
            parts.append(tab[field])
    if "steps" in tab:
        parts.append(f"{tab['steps']} steps")
    if "cfg" in tab:
        parts.append(f"CFG {tab['cfg']:g}")
    if "dcfg" in tab:
        parts.append(f"{dcfg_label} {tab['dcfg']:g}")
    if "width" in tab and "height" in tab:
        parts.append(f"{tab['width']}x{tab['height']}")
    return " · ".join(parts)
