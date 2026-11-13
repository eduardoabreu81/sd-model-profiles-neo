"""A stand-in for lib_model_profiles.forge: Forge's preset options in a dict, two presets like Forge's own
(qwen21 without a Shift slider, an "h3i" added by a model extension with its own sampler and 2048 sizes)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib_model_profiles import fields  # noqa: E402
from lib_model_profiles.fields import TAB_FIELDS, TABS  # noqa: E402

FACTORY = {
    "qwen21": {"sampler": "Euler", "scheduler": "Simple", "steps": 32, "hr_steps": 32, "cfg": 2.5, "hr_cfg": 2.5,
               "width": 0, "height": 0},
    "h3i": {"sampler": "H3-Image LCM", "scheduler": "Simple", "steps": 6, "hr_steps": 6, "cfg": 1.0, "hr_cfg": 1.0,
            "width": 2048, "height": 2048},
    "wan": {"sampler": "Euler", "scheduler": "Simple", "steps": 4, "hr_steps": 4, "cfg": 1.0, "hr_cfg": 1.0,
            "dcfg": 5.0, "hr_dcfg": 5.0, "width": 0, "height": 0},
}


class Info:
    def __init__(self, name):
        self.name = name
        self.filename = f"/models/{name}"
        self.title = f"{name} [abc123]"
        self.short_title = name.rsplit(".", 1)[0]


class FakeForge:
    def __init__(self, ext_dirs=()):
        self.options = {}
        self.ext_dirs = list(ext_dirs)
        self.saves = 0
        self.model_calls = []
        self.samplers = {"Euler", "Euler a", "H3-Image LCM", "LCM"}
        self.schedulers = {"Simple", "Beta", "Karras", "Viggle Turbo"}
        self.checkpoints = {"qwen-base.safetensors", "qwen-turbo.safetensors"}
        self.modules = {"vae.safetensors", "te.gguf"}
        for arch, values in FACTORY.items():
            for tab in TABS:
                for field in TAB_FIELDS[tab]:
                    if field in values:
                        self.options[fields.option_key(arch, tab, field)] = values[field]

    # presets
    def option_keys(self, arch):
        return {k for k in self.options if k.startswith(f"{arch}_")}

    def factory_values(self, arch):
        keys = self.option_keys(arch)
        defaults = {}
        for tab in TABS:
            for field in TAB_FIELDS[tab]:
                if field in FACTORY[arch]:
                    defaults[fields.option_key(arch, tab, field)] = FACTORY[arch][field]
        return fields.settings_only(fields.from_options(arch, defaults, keys))

    def preset_values(self, arch):
        return fields.from_options(arch, self.options, self.option_keys(arch))

    def write_settings(self, arch, values):
        for key, value in fields.to_options(arch, values, self.option_keys(arch)).items():
            self.options[key] = value

    def write_model_options(self, arch, model):
        if "checkpoint" in model:
            self.options[fields.model_option_key(arch, "checkpoint")] = model["checkpoint"]

    def save_options(self):
        self.saves += 1

    # model files
    def resolve_checkpoint(self, name):
        base = name.split(" [")[0]
        for c in self.checkpoints:
            if base in (c, c.rsplit(".", 1)[0]):
                return Info(c)
        return None

    def checkpoint_title(self, info):
        return info.title

    def checkpoint_key(self, name):
        info = self.resolve_checkpoint(name)
        return info.name if info else name

    def same_checkpoint(self, a, b):
        ia, ib = self.resolve_checkpoint(a), self.resolve_checkpoint(b)
        return a == b or (ia is not None and ib is not None and ia.filename == ib.filename)

    def apply_model(self, arch, model):
        shown, warnings = {}, []
        if "checkpoint" in model:
            info = self.resolve_checkpoint(model["checkpoint"])
            if info is None:
                warnings.append(f"Checkpoint not found: {model['checkpoint']}")
            else:
                shown["checkpoint"] = info.title
                self.model_calls.append(("checkpoint", info.name))
        if "modules" in model:
            if all(m in self.modules for m in model["modules"]):
                shown["modules"] = model["modules"]
                self.model_calls.append(("modules", tuple(model["modules"])))
            else:
                warnings.append("VAE / Text Encoder not found")
        if shown:
            self.saves += 1
        return shown, warnings

    def usable(self, values):
        out = {k: dict(v) for k, v in values.items()}
        warnings = []
        for tab in TABS:
            t = out.get(tab) or {}
            if "sampler" in t and t["sampler"] not in self.samplers:
                warnings.append(f"Sampler \"{t.pop('sampler')}\" is not available")
            if "scheduler" in t and t["scheduler"] not in self.schedulers:
                warnings.append(f"Schedule type \"{t.pop('scheduler')}\" is not available")
        return out, warnings

    def extension_dirs(self):
        return self.ext_dirs

    def data_file(self):
        raise AssertionError("tests pass their own Store")


@pytest.fixture
def forge():
    return FakeForge()
