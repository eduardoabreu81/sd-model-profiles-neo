"""Model Profiles for Forge Neo: several named settings per UI Preset (Base, Turbo, ...), on top of Forge's own presets.

This file only wires the extension into Forge; the rules live in lib_model_profiles. It never touches generation: the
script below adds the "Profile" line to the generation parameters and reads it back from pasted parameters.
"""

import logging
import os
import sys

from modules import script_callbacks, scripts, shared

EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if EXT_DIR not in sys.path:
    sys.path.insert(0, EXT_DIR)

from lib_model_profiles import forge, ui  # noqa: E402
from lib_model_profiles.fields import TABS  # noqa: E402
from lib_model_profiles.service import Service  # noqa: E402

logger = logging.getLogger("model_profiles")
INFOTEXT = "Profile"

PROBLEM = forge.compat()
if PROBLEM:
    logger.warning("Model Profiles is off: %s", PROBLEM)
else:
    try:
        ui.state.service = Service(forge)
    except Exception:
        logger.exception("Model Profiles is off: it could not start")
        PROBLEM = "could not start"


def on_ui_settings():
    from modules.options import OptionInfo, options_section

    # in Settings next to Forge's own per-preset pages
    options = options_section(("model_profiles", "Model Profiles", "presets"), {
        "model_profiles_restore_default": OptionInfo(
            True, "Leaving a UI Preset puts its ★ default profile back into it"),
        "model_profiles_link_checkpoint": OptionInfo(
            True, "Choosing a checkpoint in the list applies its checkpoint profile"),
        "model_profiles_infotext": OptionInfo(
            True, "Write the profile name to the generation parameters (\"Profile: ...\")"),
    })
    for key, info in options.items():
        shared.opts.add_option(key, info)


if not PROBLEM:
    script_callbacks.on_after_component(ui.capture, name="model_profiles")
    script_callbacks.on_ui_settings(on_ui_settings, name="model_profiles")


def _current(p, is_img2img) -> tuple[dict, dict]:
    tab = TABS[1] if is_img2img else TABS[0]
    values = {"sampler": getattr(p, "sampler_name", None), "scheduler": getattr(p, "scheduler", None),
              "steps": getattr(p, "steps", None), "cfg": getattr(p, "cfg_scale", None),
              "dcfg": getattr(p, "distilled_cfg_scale", None), "width": getattr(p, "width", None),
              "height": getattr(p, "height", None)}
    if not is_img2img and getattr(p, "enable_hr", False):
        values.update(hr_steps=getattr(p, "hr_second_pass_steps", None), hr_cfg=getattr(p, "hr_cfg", None),
                      hr_dcfg=getattr(p, "hr_distilled_cfg", None))
    current = {tab: {k: v for k, v in values.items() if v is not None}}
    model = {"checkpoint": getattr(shared.opts, "sd_model_checkpoint", None)}
    return model, current


class Script(scripts.Script):
    def title(self):
        return "Model Profiles"

    def show(self, is_img2img):
        return scripts.AlwaysVisible if not PROBLEM else False

    def ui(self, is_img2img):
        # nothing in the tabs: the Profile dropdown lives next to UI Preset, and pasted parameters reach it through
        # Forge's paste lists (lib_model_profiles.ui._register_paste)
        return []

    def process(self, p, *args):
        if PROBLEM or not getattr(shared.opts, "model_profiles_infotext", True):
            return
        try:
            model, current = _current(p, self.is_img2img)
            name = ui.state.service.infotext_name(forge.current_arch(), model, current)
            if name:
                p.extra_generation_params[INFOTEXT] = name
        except Exception as exc:  # never in the way of a generation
            logger.warning("Model Profiles: no infotext for this generation: %s", exc)
