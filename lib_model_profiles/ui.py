"""The Profile dropdown and its two buttons, placed right after Forge's UI Preset dropdown (created from the
after_component callback, so they sit in Forge's own quicksettings row), and the events that tie them to Forge's
controls. The page script (javascript/model_profiles.js) runs the menu, the prompts for names and the "modified"
mark, and sends commands through hidden components."""

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
PROMPT_SLOTS = [(("txt2img", "prompt"), "txt2img_prompt"), (("txt2img", "negative"), "txt2img_neg_prompt"),
                (("img2img", "prompt"), "img2img_prompt"), (("img2img", "negative"), "img2img_neg_prompt")]
NEEDED = ["forge_ui_preset", *(e for _, e in MODEL_SLOTS), *(e for _, e in SETTING_SLOTS),
          *(e for _, e in PROMPT_SLOTS)]
QUIET = dict(queue=False, show_progress=False)
VERIFY_JS = "() => { if (window.modelProfilesVerify) modelProfilesVerify(); }"
AFTER_COMMAND_JS = "() => { if (window.modelProfilesAfterCommand) modelProfilesAfterCommand(); }"

state = SimpleNamespace(service=None, controls=None, bound=False)


def _opt(name: str, default=True):
    from modules import shared
    return getattr(shared.opts, name, default)


def _in_blocks() -> bool:
    return gr.context.Context.root_block is not None and gr.context.Context.block is not None


def _keep_out_of_config(component):
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
    except Exception:
        logger.exception("Model Profiles: could not set up its controls; Forge works as before")
        state.bound = True  # do not retry on every component


def _build():
    from modules.ui_components import ToolButton

    def hidden_text(elem_id):
        return _keep_out_of_config(gr.Textbox(value="", show_label=False, container=False, elem_id=elem_id,
                                              elem_classes=["model-profiles-hidden"]))

    def hidden_button(elem_id):
        return _keep_out_of_config(gr.Button(elem_id, elem_id=elem_id, elem_classes=["model-profiles-hidden"]))

    c = SimpleNamespace()
    c.profile = _keep_out_of_config(gr.Dropdown(label="Profile", choices=[], value=None, interactive=True,
                                                elem_id="model_profiles_select", elem_classes=["model_profiles"]))
    c.save = _keep_out_of_config(ToolButton(value="\U0001f4be", elem_id="model_profiles_save", tooltip="Save profile"))
    c.more = _keep_out_of_config(ToolButton(value="⋯", elem_id="model_profiles_more", tooltip="Profile options"))
    # the page script talks to the server through these; hidden by CSS (visible=False would not reach the page)
    c.state = hidden_text("model_profiles_state")
    c.cmd = hidden_text("model_profiles_cmd")
    c.run = hidden_button("model_profiles_run")
    c.restore = hidden_button("model_profiles_restore")
    c.reapply = hidden_button("model_profiles_reapply")
    c.arch = gr.State("")
    _register_paste(c.profile)
    return c


def _register_paste(dropdown):
    """Pasted generation parameters ("Read generation parameters", Send to txt2img / img2img, PNG Info) set the
    Profile dropdown from their "Profile" line. Forge builds the tabs (and the scripts' infotext_fields) before this
    row and connects the paste buttons after it, so the dropdown joins the tabs' paste lists here."""
    try:
        from modules import infotext_utils
    except ImportError:
        return
    seen = set()
    for tab in ("txt2img", "img2img", "inpaint"):
        fields_list = (infotext_utils.paste_fields.get(tab) or {}).get("fields")
        if fields_list is None or id(fields_list) in seen:
            continue  # img2img and inpaint share one list
        seen.add(id(fields_list))
        fields_list.append(infotext_utils.PasteField(dropdown, paste_profile))


# region values in and out


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


def _prompts_in(values) -> dict:
    out: dict = {}
    for ((tab, part), _), text in zip(PROMPT_SLOTS, values):
        out.setdefault(tab, {})[part] = text or ""
    return out


def _prompt_updates(before: dict, after: dict | None) -> list:
    updates = []
    for (tab, part), _ in PROMPT_SLOTS:
        new = (after or {}).get(tab, {}).get(part)
        updates.append(gr.update(value=new) if after is not None and new != before[tab][part] else gr.update())
    return updates


def _skip(count: int) -> list:
    return [gr.update() for _ in range(count)]


def _apply_outputs(arch: str, result: dict | None, prompts: dict) -> list:
    """[profile, state, *model, *settings, *prompts]"""
    svc = state.service
    if result is None:
        return [gr.update(), svc.page_state(arch)] + _skip(len(MODEL_SLOTS) + len(SETTING_SLOTS) + len(PROMPT_SLOTS))
    for w in result.get("warnings", []):
        gr.Warning(w)
    if result.get("message"):
        gr.Info(result["message"])
    return ([_dropdown(arch, result.get("select"), keep=True), svc.page_state(arch)] + _model_updates(result["model"])
            + _settings_updates(result["settings"]) + _prompt_updates(prompts, result.get("prompts")))


# endregion

# region handlers


def on_load(arch):
    arch = arch or ""
    return _dropdown(arch), state.service.page_state(arch), arch


def on_preset(arch, previous, *prompt_values):
    svc = state.service
    arch = arch or ""
    prompts = _prompts_in(prompt_values)
    after = None
    try:
        # Forge saves its options for the new preset at the same moment; save ours a little later
        restored, after = svc.switch(previous, arch, prompts, restore=bool(_opt("model_profiles_restore_default")),
                                     save=False)
        if restored:
            threading.Timer(1.5, svc.forge.save_options).start()
    except Exception:
        logger.exception("Model Profiles: could not switch profiles from %s to %s", previous, arch)
    return [_dropdown(arch), svc.page_state(arch), arch] + _prompt_updates(prompts, after)


def on_apply(profile_id, arch, *prompt_values):
    prompts = _prompts_in(prompt_values)
    arch = arch or ""
    if not profile_id or not arch:
        return _apply_outputs(arch, None, prompts)
    try:
        result = state.service.apply(arch, profile_id, prompts)
    except ProfileError as exc:
        gr.Warning(str(exc))
        result = None
    return _apply_outputs(arch, result, prompts)


def on_checkpoint(checkpoint, arch, *prompt_values):
    """Choosing a checkpoint in Forge's list applies the profile linked to it."""
    prompts = _prompts_in(prompt_values)
    arch = arch or ""
    svc = state.service
    profile_id = svc.linked(arch, checkpoint) if _opt("model_profiles_link_checkpoint") else None
    if not profile_id or profile_id == svc.store.active(arch):
        return [gr.update(), gr.update()] + _skip(len(MODEL_SLOTS) + len(SETTING_SLOTS) + len(PROMPT_SLOTS))
    try:
        result = svc.apply(arch, profile_id, prompts)
        result["message"] = f"Applied the profile linked to this checkpoint: {svc.catalog(arch).get(profile_id)['name']}."
    except ProfileError as exc:
        gr.Warning(str(exc))
        result = None
    return _apply_outputs(arch, result, prompts)


def on_restore(raw, arch, *prompt_values):
    prompts = _prompts_in(prompt_values)
    arch = arch or ""
    try:
        cmd = json.loads(raw or "{}")
        result = state.service.restore(arch, str(cmd.get("entry")), prompts)
    except (ProfileError, json.JSONDecodeError) as exc:
        gr.Warning(str(exc))
        result = None
    return _apply_outputs(arch, result, prompts)


def on_reapply(profile_id, arch):
    if not profile_id or not arch:
        return _skip(len(SETTING_SLOTS))
    return _settings_updates(state.service.reapply_settings(arch, profile_id))


def on_command(raw, profile_id, arch, *values):
    svc = state.service
    arch = arch or ""
    export = None
    selected = profile_id
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
        out = svc.command(arch, profile_id, op, cmd.get("name"), current, data=cmd.get("data"))
        selected, export = out["select"], out["export"]
        if out["message"]:
            gr.Info(out["message"])
    except (ProfileError, json.JSONDecodeError) as exc:
        gr.Warning(str(exc))
    return _dropdown(arch, selected, keep=True), svc.page_state(arch, export=export)


def paste_profile(params: dict):
    """The Profile dropdown's value for pasted generation parameters (a function in the script's infotext_fields).
    The pasted values are already in the controls: only the dropdown follows, nothing is applied."""
    svc = state.service
    name = str((params or {}).get("Profile") or "").strip()
    if not name or svc is None:
        return None
    if name.endswith("(modified)"):
        name = name[: -len("(modified)")].strip()
    from . import forge
    arch = forge.current_arch()
    item = svc.catalog(arch).find_name(name)
    if item is None:
        return None
    svc.store.set_active(arch, item["id"])
    return _dropdown(arch, item["id"], keep=True)


def on_profile_shown(arch):
    """The dropdown changed (by the user, a paste or a command): refresh what the page script compares against."""
    return state.service.page_state(arch or "")


# endregion


def _bind():
    c = state.controls
    if c is None or not _in_blocks() or any(e not in COMPONENTS for e in NEEDED):
        return
    preset = COMPONENTS["forge_ui_preset"]
    checkpoint = COMPONENTS[fields.MODEL_ELEM_IDS["checkpoint"]]
    model = [COMPONENTS[e] for _, e in MODEL_SLOTS]
    settings = [COMPONENTS[e] for _, e in SETTING_SLOTS]
    prompts = [COMPONENTS[e] for _, e in PROMPT_SLOTS]
    applied = [c.profile, c.state, *model, *settings, *prompts]

    # .input: only the user's own choice applies a profile, never a value set by code
    c.profile.input(on_apply, inputs=[c.profile, c.arch, *prompts], outputs=applied, **QUIET).then(
        fn=None, js=VERIFY_JS, **QUIET)
    checkpoint.input(on_checkpoint, inputs=[checkpoint, c.arch, *prompts], outputs=applied, **QUIET).then(
        fn=None, js=VERIFY_JS, **QUIET)
    c.restore.click(on_restore, inputs=[c.cmd, c.arch, *prompts], outputs=applied, **QUIET).then(
        fn=None, js=VERIFY_JS, **QUIET)
    c.reapply.click(on_reapply, inputs=[c.profile, c.arch], outputs=settings, **QUIET)
    c.run.click(on_command, inputs=[c.cmd, c.profile, c.arch, *model, *settings], outputs=[c.profile, c.state],
                **QUIET).then(fn=None, js=AFTER_COMMAND_JS, **QUIET)
    preset.change(on_preset, inputs=[preset, c.arch, *prompts], outputs=[c.profile, c.state, c.arch, *prompts],
                  **QUIET)
    gr.context.Context.root_block.load(on_load, inputs=[preset], outputs=[c.profile, c.state, c.arch], **QUIET)
    c.profile.change(on_profile_shown, inputs=[c.arch], outputs=[c.state], **QUIET)
    state.bound = True
    logger.info("Model Profiles: connected to the UI Preset, model, sampler and prompt controls")
