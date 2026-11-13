import json

import pytest
from conftest import FakeForge

from lib_model_profiles import fields
from lib_model_profiles.fields import ProfileError
from lib_model_profiles.service import SNAPSHOT_NAME, Service
from lib_model_profiles.store import Store


@pytest.fixture
def svc(tmp_path, forge):
    return Service(forge, Store(str(tmp_path / "model_profiles.json")))


def ui_values(forge, arch, checkpoint="qwen-turbo.safetensors [abc123]"):
    """What the controls show when they hold the preset's values."""
    preset = forge.preset_values(arch)
    return {"checkpoint": checkpoint, "modules": ["te.gguf"], "dtype": "Automatic"}, {
        tab: dict(preset.get(tab) or {}) for tab in fields.TABS}


def test_apply_writes_the_preset_and_selects_the_model(svc, forge):
    model, settings = ui_values(forge, "qwen21")
    settings["txt2img"].update(steps=8, cfg=1.0, scheduler="Viggle Turbo")
    current = svc.current_values("qwen21", model, settings)
    pid, _ = svc.command("qwen21", None, "save_new", "Turbo", current)

    forge.options["qwen21_t2i_step"] = 50  # the user moves on
    shown_settings, shown_model, warnings = svc.apply("qwen21", pid)

    assert warnings == []
    assert forge.options["qwen21_t2i_step"] == 8 and forge.options["qwen21_t2i_scheduler"] == "Viggle Turbo"
    assert shown_model == {"checkpoint": "qwen-turbo.safetensors [abc123]", "modules": ["te.gguf"]}
    assert shown_settings["txt2img"]["steps"] == 8
    assert svc.store.active("qwen21") == pid


def test_snapshot_keeps_custom_preset_settings_before_the_first_write(svc, forge):
    forge.options["qwen21_t2i_step"] = 24  # changed in Settings > presets before installing the extension
    svc.apply("qwen21", "f:qwen21")
    names = [p["name"] for p in svc.store.profiles("qwen21")]
    assert names == [SNAPSHOT_NAME]
    assert svc.store.profiles("qwen21")[0]["values"]["txt2img"]["steps"] == 24
    assert forge.options["qwen21_t2i_step"] == 32  # factory value applied
    svc.apply("qwen21", "f:qwen21")
    assert len(svc.store.profiles("qwen21")) == 1  # only once


def test_no_snapshot_when_the_preset_is_untouched(svc, forge):
    svc.apply("h3i", "f:h3i")
    assert svc.store.profiles("h3i") == []


def test_partial_suggestion_keeps_what_it_does_not_set(tmp_path):
    folder = tmp_path / "ext"
    folder.mkdir()
    (folder / "model_profiles.json").write_text(json.dumps({"profiles": [
        {"preset": "qwen21", "name": "Turbo (Viggle)", "values": {"scheduler": "Viggle Turbo", "steps": 8, "cfg": 1}}
    ]}), encoding="utf-8")
    forge = FakeForge(ext_dirs=[("qwen2.1-forge-neo", str(folder))])
    svc = Service(forge, Store(str(tmp_path / "p.json")))
    forge.options["qwen21_t2i_width"] = 1328
    settings, model, _ = svc.apply("qwen21", "s:qwen2.1-forge-neo:qwen21:turbo-viggle")
    assert model == {} and forge.model_calls == []
    assert "width" not in settings["txt2img"] and forge.options["qwen21_t2i_width"] == 1328


def test_missing_sampler_and_checkpoint_are_skipped_with_warnings(svc, forge):
    p = svc.store.add("qwen21", "Odd", {"checkpoint": "gone.safetensors", "sampler": "Nope", "steps": 9})
    settings, model, warnings = svc.apply("qwen21", p["id"])
    assert settings["txt2img"] == {"steps": 9}
    assert model == {}
    assert any("Nope" in w for w in warnings) and any("gone.safetensors" in w for w in warnings)


def test_shift_is_only_used_by_presets_that_have_it(svc, forge):
    p = svc.store.add("qwen21", "With shift", {"steps": 8, "shift": 3})
    settings, _, _ = svc.apply("qwen21", p["id"])
    assert "dcfg" not in settings["txt2img"]
    w = svc.store.add("wan", "Wan", {"steps": 6, "shift": 8})
    settings, _, _ = svc.apply("wan", w["id"])
    assert settings["txt2img"]["dcfg"] == 8.0 and forge.options["wan_t2i_dcfg"] == 8.0


def test_leaving_a_preset_restores_its_star(svc, forge):
    turbo = svc.store.add("qwen21", "Turbo", {"checkpoint": "qwen-turbo.safetensors", "steps": 8})
    base = svc.store.add("qwen21", "Base", {"checkpoint": "qwen-base.safetensors", "steps": 20})
    svc.command("qwen21", turbo["id"], "default", None, {})
    svc.apply("qwen21", base["id"])
    assert forge.options["qwen21_t2i_step"] == 20

    assert svc.leave("qwen21") is True
    assert forge.options["qwen21_t2i_step"] == 8
    assert forge.options["forge_checkpoint_qwen21"] == "qwen-turbo.safetensors"
    assert svc.store.active("qwen21") == turbo["id"]
    assert svc.leave("qwen21") is False  # already the default


def test_without_a_star_leaving_changes_nothing(svc, forge):
    base = svc.store.add("qwen21", "Base", {"steps": 20})
    svc.apply("qwen21", base["id"])
    before = dict(forge.options)
    assert svc.leave("qwen21") is False and forge.options == before
    assert svc.leave("qwen21", restore=False) is False


def test_suggestions_are_read_only(svc):
    with pytest.raises(ProfileError):
        svc.command("qwen21", "f:qwen21", "rename", "x", {})
    with pytest.raises(ProfileError):
        svc.command("qwen21", "f:qwen21", "save", None, {"txt2img": {"steps": 1}})
    # but they can be starred
    pid, _ = svc.command("qwen21", "f:qwen21", "default", None, {})
    assert svc.store.default("qwen21") == "f:qwen21" == pid


def test_save_updates_a_user_profile_and_makes_it_the_preset(svc, forge):
    p = svc.store.add("qwen21", "Mine", {"steps": 8})
    svc.command("qwen21", p["id"], "save", None, fields.normalize({"steps": 12, "cfg": 1.5}))
    assert svc.store.get("qwen21", p["id"])["values"]["txt2img"] == {"steps": 12, "cfg": 1.5}
    assert forge.options["qwen21_t2i_step"] == 12 and svc.store.active("qwen21") == p["id"]


def test_delete_and_rename(svc):
    p = svc.store.add("qwen21", "Mine", {"steps": 8})
    pid, _ = svc.command("qwen21", p["id"], "rename", "Turbo", {})
    assert svc.store.get("qwen21", pid)["name"] == "Turbo"
    pid, _ = svc.command("qwen21", pid, "delete", None, {})
    assert pid is None and svc.store.profiles("qwen21") == []


def test_infotext_name(svc, forge):
    p = svc.store.add("qwen21", "Turbo", {"checkpoint": "qwen-turbo.safetensors", "steps": 8, "cfg": 1})
    svc.store.set_active("qwen21", p["id"])
    ck = {"checkpoint": "qwen-turbo.safetensors [abc123]"}
    assert svc.infotext_name("qwen21", ck, {"txt2img": {"steps": 8, "cfg": 1.0}}) == "Turbo"
    assert svc.infotext_name("qwen21", ck, {"txt2img": {"steps": 9, "cfg": 1.0}}) == "Turbo (modified)"
    other = {"checkpoint": "qwen-base.safetensors"}
    assert svc.infotext_name("qwen21", other, {"txt2img": {"steps": 8}}) is None
    assert svc.infotext_name("h3i", ck, {}) is None


def test_page_state_lists_items_and_expected_controls(svc, forge):
    p = svc.store.add("qwen21", "Turbo", {"checkpoint": "qwen-turbo.safetensors", "steps": 8, "shift": 3})
    svc.store.set_active("qwen21", p["id"])
    state = json.loads(svc.page_state("qwen21"))
    assert state["selected"]["editable"] is True
    assert state["expect"]["txt2img_steps"] == 8 and state["expect"]["img2img_steps"] == 8
    assert state["expect"]["setting_sd_model_checkpoint"] == "qwen-turbo.safetensors [abc123]"
    assert "txt2img_distilled_cfg_scale" not in state["expect"]  # qwen21 has no Shift slider
    assert [i["kind"] for i in state["items"]] == ["user", "factory"]
