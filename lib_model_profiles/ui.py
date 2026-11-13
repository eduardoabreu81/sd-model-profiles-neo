"""The Profile dropdown and its two buttons, placed right after Forge's UI Preset dropdown (created from the
after_component callback, so they sit in Forge's own quicksettings row), and the events that tie them to Forge's
controls. The page script (javascript/model_profiles.js) runs the menu, the save prompts and the "modified" mark."""

from __future__ import annotations

import json
import logging
import threading
from types import SimpleNamespace

import gradio as gr

from . import fields
from .fields import ProfileError

logger = logging.getLogger("model_profiles")

COMPONENTS: dict = {}
SETTING_SLOTS = list(fields.ELEM_IDS.items())  # [((tab, field), elem_id)] in a fixed order
MODEL_SLOTS = list(fields.MODEL_ELEM_IDS.items())  # [(field, elem_id)]
NEEDED = ["forge_ui_preset", *(e for _, e in MODEL_SLOTS), *(e for _, e in SETTING_SLOTS)]
PASTE_IDS = ("model_profiles_paste_txt2img", "model_profiles_paste_img2img")

state = SimpleNamespace(service=None, controls=None, bound=False, pasted=set())


def _in_blocks() -> bool:
    return gr.context.Context.root_block is not None and gr.context.Context.block is not None


def _hide(component):
    component.do_not_save_to_config = True
    return component


def capture(component, **kwargs):
    elem_id = getattr(component, "elem_id", None)
    if not elem_id or state.service is None:
        return
    COMPONENTS[elem_id] = component
    try:
        if elem_id == "forge_ui_preset" and state.controls is None and _in_blocks():
            state.controls = _build()
        if not state.bound:
            _bind()
        if state.bound:
            _bind_paste()
    except Exception:
        logger.exception("Model Profiles: could not set up its controls; Forge works as before")
        state.bound = True  # do not retry on every component


def _build():
    from modules.ui_components import ToolButton

    c = SimpleNamespace()
    c.profile = _hide(gr.Dropdown(label="Profile", choices=[], value=None, interactive=True,
                                  elem_id="model_profiles_select", elem_classes=["model_profiles"]))
    c.save = _hide(ToolButton(value="\U0001f4be", elem_id="model_profiles_save", tooltip="Save profile"))
    c.more = _hide(ToolButton(value="⋯", elem_id="model_profiles_more", tooltip="Profile options"))
    # the page script talks to the server through these; hidden by CSS (visible=False would not reach the page)
    c.state = _hide(gr.Textbox(value="{}", show_label=False, container=False, elem_id="model_profiles_state",
                               elem_classes=["model-profiles-hidden"]))
    c.cmd = _hide(gr.Textbox(value="", show_label=False, container=False, elem_id="model_profiles_cmd",
                             elem_classes=["model-profiles-hidden"]))
    c.run = _hide(gr.Button("run", elem_id="model_profiles_run", elem_classes=["model-profiles-hidden"]))
    c.reapply = _hide(gr.Button("reapply", elem_id="model_profiles_reapply", elem_classes=["model-profiles-hidden"]))
    c.arch = gr.State("")
    return c


def _dropdown(arch: str, selected: str | None = None, keep: bool = False):
    svc = state.service
    cat = svc.catalog(arch)
    value = selected if keep else svc.selected(arch)
    if value is not None and cat.get(value) is None:
        value = None
    return gr.update(choices=cat.choices(), value=value)


def _settings_updates(settings: dict) -> list:
    return [gr.update(value=settings[tab][field]) if field in (settings.get(tab) or {}) else gr.update()
            for (tab, field), _ in SETTING_SLOTS]


def _model_updates(shown: dict) -> list:
    return [gr.update(value=shown[field]) if field in shown else gr.update() for field, _ in MODEL_SLOTS]


def _no_change(count: int) -> list:
    return [gr.update() for _ in range(count)]


# region handlers


def on_load(arch):
    arch = arch or ""
    return _dropdown(arch), state.service.page_state(arch), arch


def on_preset(arch, previous):
    svc = state.service
    arch = arch or ""
    if previous and previous != arch:
        from modules import shared
        restore = bool(getattr(shared.opts, "model_profiles_restore_default", True))
        try:
            # Forge saves its options for the new preset at the same moment; save ours a little later
            if svc.leave(previous, restore=restore, save=False):
                threading.Timer(1.5, svc.forge.save_options).start()
        except Exception:
            logger.exception("Model Profiles: could not restore the default profile of %s", previous)
    return _dropdown(arch), svc.page_state(arch), arch


def on_apply(profile_id, arch):
    svc = state.service
    outputs = len(MODEL_SLOTS) + len(SETTING_SLOTS)
    if not profile_id or not arch:
        return [svc.page_state(arch or "")] + _no_change(outputs)
    try:
        settings, shown, warnings = svc.apply(arch, profile_id)
    except ProfileError as exc:
        gr.Warning(str(exc))
        return [svc.page_state(arch)] + _no_change(outputs)
    for w in warnings:
        gr.Warning(w)
    return [svc.page_state(arch)] + _model_updates(shown) + _settings_updates(settings)


def on_reapply(profile_id, arch):
    if not profile_id or not arch:
        return _no_change(len(SETTING_SLOTS))
    return _settings_updates(state.service.reapply_settings(arch, profile_id))


def on_command(raw, profile_id, arch, *values):
    svc = state.service
    arch = arch or ""
    try:
        cmd = json.loads(raw or "{}")
        op = cmd.get("op")
        current = {}
        if op in ("save", "save_new"):
            model = {field: values[n] for n, (field, _) in enumerate(MODEL_SLOTS)}
            settings: dict = {}
            for n, ((tab, field), _) in enumerate(SETTING_SLOTS):
                settings.setdefault(tab, {})[field] = values[len(MODEL_SLOTS) + n]
            current = svc.current_values(arch, model, settings)
        selected, message = svc.command(arch, profile_id, op, cmd.get("name"), current)
        gr.Info(message)
    except (ProfileError, json.JSONDecodeError) as exc:
        gr.Warning(str(exc))
        selected = profile_id
    return _dropdown(arch, selected, keep=True), svc.page_state(arch)


def on_paste(text, arch):
    svc = state.service
    arch = arch or ""
    name = (text or "").strip()
    if name.endswith("(modified)"):
        name = name[: -len("(modified)")].strip()
    item = svc.catalog(arch).find_name(name) if name else None
    if item is None:
        return gr.update(), gr.update()
    svc.store.set_active(arch, item["id"])
    return _dropdown(arch, item["id"], keep=True), svc.page_state(arch)


# endregion


def _bind():
    c = state.controls
    if c is None or not _in_blocks() or any(e not in COMPONENTS for e in NEEDED):
        return
    preset = COMPONENTS["forge_ui_preset"]
    model = [COMPONENTS[e] for _, e in MODEL_SLOTS]
    settings = [COMPONENTS[e] for _, e in SETTING_SLOTS]
    quiet = dict(queue=False, show_progress=False)

    c.profile.input(on_apply, inputs=[c.profile, c.arch], outputs=[c.state, *model, *settings], **quiet).then(
        fn=None, js="() => { if (window.modelProfilesVerify) modelProfilesVerify(); }", **quiet)
    c.reapply.click(on_reapply, inputs=[c.profile, c.arch], outputs=settings, **quiet)
    c.run.click(on_command, inputs=[c.cmd, c.profile, c.arch, *model, *settings], outputs=[c.profile, c.state],
                **quiet)
    preset.change(on_preset, inputs=[preset, c.arch], outputs=[c.profile, c.state, c.arch], **quiet)
    gr.context.Context.root_block.load(on_load, inputs=[preset], outputs=[c.profile, c.state, c.arch], **quiet)
    state.bound = True
    logger.info("Model Profiles: connected to the UI Preset, model and sampler controls")


def _bind_paste():
    c = state.controls
    if c is None:
        return
    for elem_id in PASTE_IDS:
        if elem_id in COMPONENTS and elem_id not in state.pasted and _in_blocks():
            COMPONENTS[elem_id].change(on_paste, inputs=[COMPONENTS[elem_id], c.arch], outputs=[c.profile, c.state],
                                       queue=False, show_progress=False)
            state.pasted.add(elem_id)
