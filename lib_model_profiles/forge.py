"""Everything that touches Forge Neo. Read-only toward Forge's code: settings go through shared.opts, model files through
Forge's own checkpoint_change / modules_change / dtype_change, exactly as the UI Preset switch does (_load_presets in
modules_forge/main_entry.py). Nothing in Forge's folders is changed."""

from __future__ import annotations

import logging
import os

from . import fields
from .fields import TAB_FIELDS, TABS

logger = logging.getLogger("model_profiles")


def compat() -> str | None:
    """None when this Forge has what the extension relies on, otherwise the reason it does not."""
    try:
        from modules import shared  # noqa: F401
        from modules_forge import main_entry, presets
    except ImportError as exc:
        return f"not a Forge Neo install ({exc})"
    for name in ("checkpoint_change", "modules_change", "dtype_change", "refresh_model_loading_parameters",
                 "module_list", "module_map", "forge_unet_storage_dtype_options"):
        if not hasattr(main_entry, name):
            return f"modules_forge.main_entry has no {name}"
    for name in ("PresetArch", "register"):
        if not hasattr(presets, name):
            return f"modules_forge.presets has no {name}"
    return None


def _opts():
    from modules import shared
    return shared.opts


def save_options():
    from modules import shared
    shared.opts.save(shared.config_filename)


# region presets


def archs() -> list[str]:
    """The UI Presets right now, with the ones model extensions added at runtime (they load in any order, so this is
    never cached at import)."""
    from modules import shared
    from modules_forge.presets import PresetArch
    disabled = set(getattr(shared.cmd_opts, "disable_preset", None) or [])
    return [name for name in PresetArch.choices() if name not in disabled]


def current_checkpoint() -> str | None:
    return getattr(_opts(), "sd_model_checkpoint", None)


def current_arch() -> str:
    return getattr(_opts(), "forge_preset", "") or ""


def option_keys(arch: str) -> set[str]:
    labels = _opts().data_labels
    keys = {fields.option_key(arch, tab, field) for tab in TABS for field in TAB_FIELDS[tab]}
    return {k for k in keys if k in labels}


def dcfg_label(arch: str) -> str | None:
    from modules_forge import presets
    try:
        if presets.use_shift(arch):
            return "Shift"
        if presets.use_distill(arch):
            return "Distilled CFG"
    except Exception:
        pass
    return None


def factory_values(arch: str) -> dict:
    """The preset's factory settings (no model files): the defaults of its options, whoever registered them."""
    labels = _opts().data_labels
    keys = option_keys(arch)
    defaults = {k: labels[k].default for k in keys}
    return fields.settings_only(fields.from_options(arch, defaults, keys, keep_zero=True))


def preset_values(arch: str) -> dict:
    """What Forge's preset holds now for `arch`, model files included."""
    opts = _opts()
    keys = option_keys(arch)
    data = {k: getattr(opts, k, None) for k in keys}
    for field in ("checkpoint", "modules", "dtype"):
        key = fields.model_option_key(arch, field)
        data[key] = getattr(opts, key, None)
    return fields.from_options(arch, data, keys, keep_zero=True)


def write_settings(arch: str, values: dict) -> bool:
    """Write the settings of `values` into the arch's preset options; True when something changed."""
    opts = _opts()
    changed = False
    for key, value in fields.to_options(arch, values, option_keys(arch)).items():
        if getattr(opts, key, None) != value:
            opts.set(key, value)
            changed = True
    return changed


def write_model_options(arch: str, model: dict):
    """Point the preset's remembered model files at `model` without loading anything (for a preset that is not on
    screen); the values are stored the way Forge's own dropdowns store them."""
    opts = _opts()
    if "checkpoint" in model:
        info = resolve_checkpoint(model["checkpoint"])
        if info is not None:
            opts.set(fields.model_option_key(arch, "checkpoint"), checkpoint_title(info))
    if "modules" in model:
        paths = module_paths(model["modules"])
        if paths is not None:
            opts.set(fields.model_option_key(arch, "modules"), paths)
    if "dtype" in model and model["dtype"] in dtype_options():
        opts.set(fields.model_option_key(arch, "dtype"), model["dtype"])


# endregion

# region model files


def resolve_checkpoint(name: str):
    from modules import sd_models
    if not name:
        return None
    return sd_models.get_closet_checkpoint_match(name)


def checkpoint_title(info) -> str:
    """The checkpoint as Forge's dropdown lists it (sd_models.checkpoint_tiles): the short title or the file name,
    never the title with the hash, which is not among the choices."""
    return info.short_title if getattr(_opts(), "sd_checkpoint_dropdown_use_short", False) else info.name


def checkpoint_key(name: str) -> str:
    """How a profile stores a checkpoint: its file name relative to the models folder, which survives renames of
    the hash part and both dropdown spellings."""
    info = resolve_checkpoint(name)
    return info.name if info is not None else name


def same_checkpoint(a: str, b: str) -> bool:
    if a == b:
        return True
    ia, ib = resolve_checkpoint(a), resolve_checkpoint(b)
    return ia is not None and ib is not None and ia.filename == ib.filename


def _module_list() -> dict:
    from modules_forge import main_entry
    if not main_entry.module_list:
        try:
            main_entry.refresh_models()
        except Exception as exc:
            logger.warning("Model Profiles: could not list VAE / Text Encoder files: %s", exc)
    return main_entry.module_list


def module_paths(names: list[str]) -> list[str] | None:
    """Full paths of the module files, or None when any is missing (a partial set would break the model)."""
    available = _module_list()
    paths = []
    for name in names:
        if name not in available:
            return None
        paths.append(available[name])
    return sorted(paths)


def module_display(names: list[str]) -> list[str]:
    from modules_forge import main_entry
    if getattr(_opts(), "sd_modules_dropdown_use_short", True):
        return list(names)
    return [main_entry.module_map.get(n, n) for n in names]


def dtype_options() -> list[str]:
    from modules_forge import main_entry
    return list(main_entry.forge_unet_storage_dtype_options.keys())


def apply_model(arch: str, model: dict) -> tuple[dict, list[str]]:
    """Select the profile's model files through Forge's own functions, in _load_presets' order. Returns the values the
    three dropdowns should show (only for what changed) and warnings for what was skipped."""
    from modules_forge import main_entry

    shown, warnings, steps = {}, [], []
    if "dtype" in model:
        if model["dtype"] in dtype_options():
            steps.append(lambda save, refresh: main_entry.dtype_change(model["dtype"], arch, save=save, refresh=refresh))
            shown["dtype"] = model["dtype"]
        else:
            warnings.append(f"Diffusion in Low Bits \"{model['dtype']}\" does not exist here; kept the current one.")
    if "modules" in model:
        paths = module_paths(model["modules"])
        if paths is None:
            missing = [n for n in model["modules"] if n not in _module_list()]
            warnings.append(f"VAE / Text Encoder not found: {', '.join(missing)}; kept the current selection.")
        else:
            steps.append(lambda save, refresh: main_entry.modules_change(paths, arch, save=save, refresh=refresh))
            shown["modules"] = module_display(model["modules"])
    if "checkpoint" in model:
        info = resolve_checkpoint(model["checkpoint"])
        if info is None:
            warnings.append(f"Checkpoint not found: {model['checkpoint']}; kept the current one.")
        else:
            title = checkpoint_title(info)
            steps.append(lambda save, refresh: main_entry.checkpoint_change(title, arch, save=save, refresh=refresh))
            shown["checkpoint"] = title
    for n, step in enumerate(steps):
        last = n == len(steps) - 1
        step(last, last)
    return shown, warnings


# endregion

# region samplers


def sampler_names() -> set[str]:
    from modules.shared_items import list_samplers
    names = set()
    for s in list_samplers():
        names.add(s.name)
        names.update(getattr(s, "aliases", None) or [])
    return names


def scheduler_names() -> set[str]:
    from modules.shared_items import list_schedulers
    return set(list_schedulers())


def usable(values: dict) -> tuple[dict, list[str]]:
    """The values with samplers and schedules that exist here (one from an extension that is off is skipped)."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in values.items()}
    warnings = []
    try:
        samplers, schedulers = sampler_names(), scheduler_names()
    except Exception:
        return out, warnings
    for tab in TABS:
        tab_values = out.get(tab)
        if not tab_values:
            continue
        if "sampler" in tab_values and tab_values["sampler"] not in samplers:
            warnings.append(f"Sampler \"{tab_values.pop('sampler')}\" is not available; kept the current one.")
        if "scheduler" in tab_values and tab_values["scheduler"] not in schedulers:
            warnings.append(f"Schedule type \"{tab_values.pop('scheduler')}\" is not available; kept the current one.")
    return out, list(dict.fromkeys(warnings))


# endregion


def extension_dirs() -> list[tuple[str, str]]:
    from modules import extensions
    return [(ext.name, ext.path) for ext in extensions.active()]


def data_file() -> str:
    from modules import paths_internal
    return os.path.join(paths_internal.data_path, "model_profiles.json")


def backup_config(folder: str | None):
    """A copy of Forge's config.json a day, before the extension writes to it."""
    from modules import shared

    from .history import daily_copy
    daily_copy(shared.config_filename, folder, "config")
