import json

import pytest
from conftest import FakeForge

from lib_model_profiles import fields
from lib_model_profiles.fields import ProfileError
from lib_model_profiles.history import History
from lib_model_profiles.service import SNAPSHOT_NAME, Service
from lib_model_profiles.store import Store


def make(tmp_path, forge):
    return Service(forge, Store(str(tmp_path / "model_profiles.json")),
                   History(str(tmp_path / "model_profiles_history.json")), backup_dir=str(tmp_path / "backups"))


@pytest.fixture
def svc(tmp_path, forge):
    return make(tmp_path, forge)


def prompts(t2i="a cat", t2i_neg="blurry", i2i="a dog", i2i_neg=""):
    return {"txt2img": {"prompt": t2i, "negative": t2i_neg}, "img2img": {"prompt": i2i, "negative": i2i_neg}}


def ui_values(forge, arch, checkpoint="qwen-turbo.safetensors [abc123]"):
    """What the controls show when they hold the preset's values."""
    preset = forge.preset_values(arch)
    return {"checkpoint": checkpoint, "modules": ["te.gguf"], "dtype": "Automatic"}, {
        tab: dict(preset.get(tab) or {}) for tab in fields.TABS}


# region apply


def test_apply_writes_the_preset_and_selects_the_model(svc, forge):
    model, settings = ui_values(forge, "qwen21")
    settings["txt2img"].update(steps=8, cfg=1.0, scheduler="Viggle Turbo")
    current = svc.current_values("qwen21", model, settings)
    pid = svc.command("qwen21", None, "save_new", "Turbo", current, data={"scope": "checkpoint"})["select"]

    forge.options["qwen21_t2i_step"] = 50  # the user moves on
    result = svc.apply("qwen21", pid)

    assert result["warnings"] == [] and result["prompts"] is None
    assert forge.options["qwen21_t2i_step"] == 8 and forge.options["qwen21_t2i_scheduler"] == "Viggle Turbo"
    assert result["model"] == {"checkpoint": "qwen-turbo.safetensors [abc123]", "modules": ["te.gguf"]}
    assert result["settings"]["txt2img"]["steps"] == 8
    assert svc.store.active("qwen21") == pid
    assert forge.backups >= 1  # Forge's config is backed up before the write


def test_snapshot_keeps_custom_preset_settings_before_the_first_write(svc, forge):
    forge.options["qwen21_t2i_step"] = 24  # changed in Settings > presets before installing the extension
    svc.apply("qwen21", "f:qwen21")
    assert [p["name"] for p in svc.store.profiles("qwen21")] == [SNAPSHOT_NAME]
    assert svc.store.profiles("qwen21")[0]["values"]["txt2img"]["steps"] == 24
    assert forge.options["qwen21_t2i_step"] == 32  # factory value applied
    svc.apply("qwen21", "f:qwen21")
    assert len(svc.store.profiles("qwen21")) == 1  # only once


def test_no_snapshot_when_the_preset_is_untouched(svc):
    svc.apply("h3i", "f:h3i")
    assert svc.store.profiles("h3i") == []


def test_partial_suggestion_keeps_what_it_does_not_set(tmp_path):
    folder = tmp_path / "ext"
    folder.mkdir()
    (folder / "model_profiles.json").write_text(json.dumps({"profiles": [
        {"preset": "qwen21", "name": "Turbo (Viggle)", "values": {"scheduler": "Viggle Turbo", "steps": 8, "cfg": 1}}
    ]}), encoding="utf-8")
    forge = FakeForge(ext_dirs=[("qwen2.1-forge-neo", str(folder))])
    svc = make(tmp_path, forge)
    forge.options["qwen21_t2i_width"] = 1328
    result = svc.apply("qwen21", "s:qwen2.1-forge-neo:qwen21:turbo-viggle")
    assert result["model"] == {} and forge.model_calls == []
    assert "width" not in result["settings"]["txt2img"] and forge.options["qwen21_t2i_width"] == 1328


def test_missing_sampler_and_checkpoint_are_skipped_with_warnings(svc):
    p = svc.store.add("qwen21", "Odd", {"checkpoint": "gone.safetensors", "sampler": "Nope", "steps": 9})
    result = svc.apply("qwen21", p["id"])
    assert result["settings"]["txt2img"] == {"steps": 9}
    assert result["model"] == {}
    assert any("Nope" in w for w in result["warnings"]) and any("gone.safetensors" in w for w in result["warnings"])


def test_shift_is_only_used_by_presets_that_have_it(svc, forge):
    p = svc.store.add("qwen21", "With shift", {"steps": 8, "shift": 3})
    assert "dcfg" not in svc.apply("qwen21", p["id"])["settings"]["txt2img"]
    w = svc.store.add("wan", "Wan", {"steps": 6, "shift": 8})
    assert svc.apply("wan", w["id"])["settings"]["txt2img"]["dcfg"] == 8.0 and forge.options["wan_t2i_dcfg"] == 8.0


# endregion

# region star


def test_leaving_a_preset_restores_its_star(svc, forge):
    turbo = svc.store.add("qwen21", "Turbo", {"checkpoint": "qwen-turbo.safetensors", "steps": 8})
    base = svc.store.add("qwen21", "Base", {"checkpoint": "qwen-base.safetensors", "steps": 20})
    svc.command("qwen21", turbo["id"], "default")
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


# endregion

# region prompt tokens


def test_tokens_follow_the_profile(svc):
    turbo = svc.store.add("qwen21", "Turbo", {"steps": 8, "prompt": "<lora:qwen-lightning:1>",
                                              "negative_prompt": "lowres"})
    base = svc.store.add("qwen21", "Base", {"steps": 20})
    r = svc.apply("qwen21", turbo["id"], prompts())
    assert r["prompts"]["txt2img"] == {"prompt": "a cat, <lora:qwen-lightning:1>", "negative": "blurry, lowres"}
    assert r["prompts"]["img2img"] == {"prompt": "a dog, <lora:qwen-lightning:1>", "negative": "lowres"}
    # switching to Base removes exactly what Turbo added
    r = svc.apply("qwen21", base["id"], r["prompts"])
    assert r["prompts"] == prompts()


def test_a_token_the_user_edited_is_left_alone(svc):
    turbo = svc.store.add("qwen21", "Turbo", {"steps": 8, "prompt": "<lora:qwen-lightning:1>"})
    base = svc.store.add("qwen21", "Base", {"steps": 20})
    svc.apply("qwen21", turbo["id"], prompts())
    r = svc.apply("qwen21", base["id"], prompts(t2i="a cat, <lora:qwen-lightning:0.6>"))
    assert r["prompts"]["txt2img"]["prompt"] == "a cat, <lora:qwen-lightning:0.6>"


def test_switching_preset_moves_the_tokens(svc):
    turbo = svc.store.add("qwen21", "Turbo", {"steps": 8, "prompt": "<lora:qwen-lightning:1>"})
    svc.apply("qwen21", turbo["id"], prompts())
    restored, after = svc.switch("qwen21", "h3i", prompts(t2i="a cat, <lora:qwen-lightning:1>"))
    assert restored is False and after["txt2img"]["prompt"] == "a cat"
    # the page loading is not a switch
    assert svc.switch("", "qwen21", prompts()) == (False, None)


def test_tokens_command_and_save_keeps_them(svc, forge):
    p = svc.store.add("qwen21", "Turbo", {"steps": 8})
    svc.command("qwen21", p["id"], "tokens", data={"prompt": "<lora:a:1>, <lora:b:0.5>", "negative": ""})
    assert svc.store.get("qwen21", p["id"])["values"]["tokens"] == {"prompt": ["<lora:a:1>", "<lora:b:0.5>"]}
    svc.command("qwen21", p["id"], "save", current=fields.normalize({"steps": 9}))
    assert svc.store.get("qwen21", p["id"])["values"]["tokens"] == {"prompt": ["<lora:a:1>", "<lora:b:0.5>"]}
    copy = svc.command("qwen21", p["id"], "save_new", "Turbo copy", fields.normalize({"steps": 9}))["select"]
    assert svc.store.get("qwen21", copy)["values"]["tokens"]["prompt"] == ["<lora:a:1>", "<lora:b:0.5>"]


# endregion

# region checkpoint link


def test_checkpoint_profiles_are_applied_with_their_checkpoint(svc):
    turbo = svc.store.add("qwen21", "Turbo", {"checkpoint": "qwen-turbo.safetensors", "steps": 8}, scope="checkpoint")
    svc.store.add("qwen21", "Preset one", {"steps": 4})
    assert svc.linked("qwen21", "qwen-turbo.safetensors [abc123]") == turbo["id"]
    assert svc.linked("qwen21", "qwen-base.safetensors") is None
    svc.command("qwen21", turbo["id"], "scope", data={"scope": "preset"})
    assert svc.linked("qwen21", "qwen-turbo.safetensors") is None
    assert "model" not in svc.store.get("qwen21", turbo["id"])["values"]  # a preset profile keeps no files


def test_a_checkpoint_profile_needs_a_checkpoint(svc):
    with pytest.raises(ProfileError):
        svc.command("qwen21", None, "save_new", "No file", fields.normalize({"steps": 8}), data={"scope": "checkpoint"})


def test_scopes_keep_what_they_should(svc, forge):
    screen = {"model": {"checkpoint": "qwen-turbo.safetensors [abc123]", "modules": ["te.gguf"]},
              "settings": {"txt2img": {"steps": 8, "cfg": 1.0, "dcfg": 3.0, "width": 1328}}}
    forge.options["wan_t2i_dcfg"] = 5.0
    ck = svc.command("wan", None, "save_new", "Ck", data={"scope": "checkpoint"}, screen=screen)["select"]
    pr = svc.command("wan", None, "save_new", "Pr", data={"scope": "preset"}, screen=screen)["select"]
    gl = svc.command("wan", None, "save_new", "Gl", data={"scope": "global"}, screen=screen)["select"]
    ck_v = svc.store.get("wan", ck)["values"]
    pr_v = svc.store.get("wan", pr)["values"]
    gl_v = svc.store.get("*", gl)["values"]
    assert ck_v["model"]["checkpoint"] == "qwen-turbo.safetensors" and ck_v["txt2img"]["dcfg"] == 3.0
    assert "model" not in pr_v and pr_v["txt2img"]["dcfg"] == 3.0
    assert "model" not in gl_v and "dcfg" not in gl_v["txt2img"]  # Shift means something else elsewhere
    assert all("width" not in v["txt2img"] for v in (ck_v, pr_v, gl_v))


def test_global_profiles_show_in_every_preset_and_can_be_starred(svc, forge):
    g = svc.store.add("qwen21", "Draft 4 steps", {"steps": 4, "cfg": 1}, scope="global")
    assert svc.store.get("*", g["id"]) is not None
    for arch in ("qwen21", "h3i"):
        cat = svc.catalog(arch)
        assert cat.get(g["id"])["group"] == "global"
        assert ("Draft 4 steps · all presets", g["id"]) in cat.choices()
    svc.command("h3i", g["id"], "default")
    svc.command("qwen21", g["id"], "default")
    assert svc.apply("h3i", g["id"])["settings"]["txt2img"]["steps"] == 4 and forge.options["h3i_t2i_step"] == 4
    svc.command("qwen21", g["id"], "rename", "Draft")
    assert svc.store.get("*", g["id"])["name"] == "Draft"
    svc.command("qwen21", g["id"], "delete")
    assert svc.store.default("qwen21") is None and svc.store.default("h3i") is None


def test_list_order_follows_the_checkpoint_in_use(svc, forge):
    other = svc.store.add("qwen21", "Base file", {"checkpoint": "qwen-base.safetensors", "steps": 20}, scope="checkpoint")
    preset = svc.store.add("qwen21", "Normal", {"steps": 20})
    mine = svc.store.add("qwen21", "Turbo file", {"checkpoint": "qwen-turbo.safetensors", "steps": 8}, scope="checkpoint")
    every = svc.store.add("qwen21", "Draft", {"steps": 4}, scope="global")
    forge.checkpoint = "qwen-turbo.safetensors [abc123]"
    order = [i["id"] for i in svc.catalog("qwen21").items]
    assert order[:4] == [mine["id"], preset["id"], every["id"], other["id"]]
    labels = dict((v, k) for k, v in svc.catalog("qwen21").choices())
    assert labels[other["id"]] == "Base file · qwen-base" and labels[mine["id"]] == "Turbo file"
    # the checkpoint on screen can be passed in (the dropdown's own value)
    order = [i["id"] for i in svc.catalog("qwen21", checkpoint="qwen-base.safetensors").items]
    assert order[0] == other["id"]


def test_scope_change_to_checkpoint_takes_the_one_on_screen(svc):
    p = svc.store.add("qwen21", "Normal", {"steps": 20})
    screen = {"model": {"checkpoint": "qwen-base.safetensors"}, "settings": {}}
    svc.command("qwen21", p["id"], "scope", data={"scope": "checkpoint"}, screen=screen)
    moved = svc.store.get("qwen21", p["id"])
    assert moved["scope"] == "checkpoint" and moved["values"]["model"]["checkpoint"] == "qwen-base.safetensors"
    svc.command("qwen21", p["id"], "scope", data={"scope": "global"}, screen=screen)
    assert svc.store.get("qwen21", p["id"]) is None and svc.store.get("*", p["id"])["scope"] == "global"


# endregion

# region history


def test_overwrites_are_recorded_and_restorable(svc, forge):
    base = svc.store.add("qwen21", "Base", {"steps": 20, "cfg": 2.5})
    turbo = svc.store.add("qwen21", "Turbo", {"steps": 8, "cfg": 1})
    svc.apply("qwen21", base["id"])
    svc.apply("qwen21", turbo["id"])
    entries = svc.history.entries("qwen21")
    assert entries[0]["kind"] == "preset" and entries[0]["name"] == "Base"
    assert entries[0]["values"]["txt2img"]["steps"] == 20

    r = svc.restore("qwen21", entries[0]["id"])
    assert forge.options["qwen21_t2i_step"] == 20 and r["select"] is None
    assert svc.store.active("qwen21") is None


def test_saved_over_and_deleted_profiles_come_back(svc):
    p = svc.store.add("qwen21", "Mine", {"steps": 8})
    svc.command("qwen21", p["id"], "save", current=fields.normalize({"steps": 12}))
    old = svc.history.entries("qwen21")
    version = next(e for e in old if e["kind"] == "profile")
    assert version["values"]["txt2img"]["steps"] == 8
    svc.restore("qwen21", version["id"])
    assert svc.store.get("qwen21", p["id"])["values"]["txt2img"]["steps"] == 8

    svc.command("qwen21", p["id"], "delete")
    gone = next(e for e in svc.history.entries("qwen21") if e["reason"].startswith("before deleting"))
    r = svc.restore("qwen21", gone["id"])
    assert [x["name"] for x in svc.store.profiles("qwen21")] == ["Mine"] and r["select"] is not None


def test_history_is_bounded_and_skips_repeats(tmp_path):
    h = History(str(tmp_path / "h.json"))
    for n in range(40):
        h.record("qwen21", "preset", "x", {"txt2img": {"steps": n + 1}}, "r")
    h.record("qwen21", "preset", "x", {"txt2img": {"steps": 40}}, "r")
    assert len(h.entries("qwen21")) == 30
    assert History(str(tmp_path / "h.json")).entries("qwen21")[0]["values"]["txt2img"]["steps"] == 40


# endregion

# region export / import, editing


def test_export_then_import(svc, tmp_path, forge):
    svc.store.add("qwen21", "Turbo", {"steps": 8, "prompt": "<lora:a:1>"})
    exported = svc.command("qwen21", None, "export")["export"]
    assert exported["profiles"][0]["preset"] == "qwen21"
    other = make(tmp_path / "other", forge)
    msg = other.command("xl", None, "import", data=json.dumps(exported))["message"]
    assert "1 for qwen21" in msg
    assert other.store.profiles("qwen21")[0]["values"]["tokens"] == {"prompt": ["<lora:a:1>"]}
    with pytest.raises(ProfileError):
        other.command("xl", None, "import", data="not json")


def test_suggestions_are_read_only_but_can_be_starred(svc):
    with pytest.raises(ProfileError):
        svc.command("qwen21", "f:qwen21", "rename", "x")
    with pytest.raises(ProfileError):
        svc.command("qwen21", "f:qwen21", "save", current={"txt2img": {"steps": 1}})
    assert svc.command("qwen21", "f:qwen21", "default")["select"] == "f:qwen21" == svc.store.default("qwen21")


def test_save_updates_a_user_profile_and_makes_it_the_preset(svc, forge):
    p = svc.store.add("qwen21", "Mine", {"steps": 8})
    svc.command("qwen21", p["id"], "save", current=fields.normalize({"steps": 12, "cfg": 1.5}))
    assert svc.store.get("qwen21", p["id"])["values"]["txt2img"] == {"steps": 12, "cfg": 1.5}
    assert forge.options["qwen21_t2i_step"] == 12 and svc.store.active("qwen21") == p["id"]


def test_rename_and_delete(svc):
    p = svc.store.add("qwen21", "Mine", {"steps": 8})
    pid = svc.command("qwen21", p["id"], "rename", "Turbo")["select"]
    assert svc.store.get("qwen21", pid)["name"] == "Turbo"
    assert svc.command("qwen21", pid, "delete")["select"] is None and svc.store.profiles("qwen21") == []


# endregion

# region infotext, page state


def test_infotext_name(svc):
    p = svc.store.add("qwen21", "Turbo", {"checkpoint": "qwen-turbo.safetensors", "steps": 8, "cfg": 1})
    svc.store.set_active("qwen21", p["id"])
    ck = {"checkpoint": "qwen-turbo.safetensors [abc123]"}
    assert svc.infotext_name("qwen21", ck, {"txt2img": {"steps": 8, "cfg": 1.0}}) == "Turbo"
    assert svc.infotext_name("qwen21", ck, {"txt2img": {"steps": 9, "cfg": 1.0}}) == "Turbo (modified)"
    assert svc.infotext_name("qwen21", {"checkpoint": "qwen-base.safetensors"}, {"txt2img": {"steps": 8}}) is None
    assert svc.infotext_name("h3i", ck, {}) is None


def test_page_state(svc):
    p = svc.store.add("qwen21", "Turbo", {"checkpoint": "qwen-turbo.safetensors", "steps": 8, "shift": 3,
                                          "prompt": "<lora:a:1>"})
    svc.apply("qwen21", p["id"])
    state = json.loads(svc.page_state("qwen21", export={"x": 1}))
    sel = state["selected"]
    assert sel["editable"] and sel["has_checkpoint"] and sel["scope"] == "preset"
    assert sel["tokens"] == {"prompt": ["<lora:a:1>"]}
    assert state["expect"]["txt2img_steps"] == 8 and state["expect"]["img2img_steps"] == 8
    assert state["expect"]["setting_sd_model_checkpoint"] == "qwen-turbo.safetensors [abc123]"
    assert "txt2img_distilled_cfg_scale" not in state["expect"]  # qwen21 has no Shift slider
    assert [i["kind"] for i in state["items"]] == ["user", "factory"]
    assert state["export"] == {"x": 1} and isinstance(state["history"], list)


# endregion


def test_forge_keep_zeros_survive_history_and_factory(svc, forge):
    """Sizes at 0 mean "keep the current size" in Forge's presets; restoring must bring the 0 back, and no control
    is ever set to 0 (found on desktop-casa: the sizes stayed at 1024 after the cleanup)."""
    assert forge.options["qwen21_t2i_width"] == 0
    p = svc.store.add("qwen21", "Sized", {"steps": 8, "width": 1024, "height": 1024})
    svc.apply("qwen21", p["id"])
    assert forge.options["qwen21_t2i_width"] == 1024

    before = next(e for e in svc.history.entries("qwen21") if e["kind"] == "preset")
    assert before["values"]["txt2img"]["width"] == 0
    r = svc.restore("qwen21", before["id"])
    assert forge.options["qwen21_t2i_width"] == 0 and "width" not in r["settings"]["txt2img"]

    svc.apply("qwen21", p["id"])
    r = svc.apply("qwen21", "f:qwen21")
    assert forge.options["qwen21_t2i_width"] == 0 and "width" not in r["settings"]["txt2img"]
    state = json.loads(svc.page_state("qwen21"))
    assert "txt2img_width" not in state["expect"]


def test_snapshot_notices_a_size_set_to_keep(tmp_path):
    forge = FakeForge()
    forge.options["h3i_t2i_width"] = 0  # the factory value is 2048
    svc = make(tmp_path, forge)
    svc.apply("h3i", "f:h3i")
    snap = svc.store.profiles("h3i")
    assert [p["name"] for p in snap] == [SNAPSHOT_NAME] and snap[0]["values"]["txt2img"]["width"] == 0


def test_saving_from_the_controls_leaves_the_size_out(svc, forge):
    model, settings = ui_values(forge, "qwen21")
    settings["txt2img"].update(steps=8, width=1328, height=1328)
    current = svc.current_values("qwen21", model, settings)
    assert "width" not in current["txt2img"] and "height" not in current["txt2img"]
    pid = svc.command("qwen21", None, "save_new", "Turbo", current)["select"]
    svc.apply("qwen21", pid)
    assert forge.options["qwen21_t2i_width"] == 0  # the preset keeps "keep the current size"
