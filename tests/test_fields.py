import pytest

from lib_model_profiles import fields
from lib_model_profiles.fields import ProfileError


def test_shorthand_applies_to_both_tabs_and_tab_objects_override():
    values = fields.normalize({"sampler": "Euler", "steps": 8, "cfg": 1, "hr_steps": 4, "img2img": {"steps": 12}})
    assert values["txt2img"] == {"sampler": "Euler", "steps": 8, "cfg": 1.0, "hr_steps": 4}
    # img2img has no Hires. fix, so hr_steps is dropped there, and its own steps win
    assert values["img2img"] == {"sampler": "Euler", "steps": 12, "cfg": 1.0}


def test_aliases_and_model_fields():
    values = fields.normalize({"shift": 3, "schedule": "Beta", "checkpoint": "a/b.safetensors",
                               "modules": ["x\\te.gguf", "vae.safetensors"], "dtype": "Automatic"})
    assert values["txt2img"]["dcfg"] == 3.0 and values["txt2img"]["scheduler"] == "Beta"
    assert values["model"] == {"checkpoint": "a/b.safetensors", "modules": ["te.gguf", "vae.safetensors"],
                               "dtype": "Automatic"}


@pytest.mark.parametrize("bad", [{"steps": "eight"}, {"cfg": True}, {"bogus": 1}, {}, {"sampler": " "},
                                 {"txt2img": []}])
def test_rejects_bad_values(bad):
    with pytest.raises(ProfileError):
        fields.normalize(bad)


def test_canonical_form_round_trips():
    values = fields.normalize({"model": {"checkpoint": "c"}, "txt2img": {"steps": 8}, "img2img": {"cfg": 2}})
    assert fields.normalize(values) == values


def test_options_round_trip_skips_forges_keep_values():
    keys = {"qwen21_t2i_step", "qwen21_t2i_cfg", "qwen21_t2i_width", "qwen21_i2i_sampler"}
    options = {"qwen21_t2i_step": 8, "qwen21_t2i_cfg": 1.0, "qwen21_t2i_width": 0, "qwen21_i2i_sampler": "Euler",
               "forge_checkpoint_qwen21": "turbo.safetensors [abc]"}
    values = fields.from_options("qwen21", options, keys)
    assert values == {"model": {"checkpoint": "turbo.safetensors [abc]"},
                      "txt2img": {"steps": 8, "cfg": 1.0}, "img2img": {"sampler": "Euler"}}
    # only keys that exist for the preset are written, model fields never
    out = fields.to_options("qwen21", {**values, "txt2img": {"steps": 8, "cfg": 1.0, "dcfg": 3.0}}, keys)
    assert out == {"qwen21_t2i_step": 8, "qwen21_t2i_cfg": 1.0, "qwen21_i2i_sampler": "Euler"}


def test_differences_only_look_at_what_the_profile_sets():
    profile = {"model": {"checkpoint": "a"}, "txt2img": {"steps": 8, "cfg": 1.0}}
    current = {"model": {"checkpoint": "a [hash]"}, "txt2img": {"steps": 8, "cfg": 1.0000001, "width": 640}}
    assert fields.differences(profile, current, same_checkpoint=lambda x, y: x.split(" [")[0] == y.split(" [")[0]) == []
    assert fields.differences(profile, {"txt2img": {"steps": 9}}) == ["txt2img.steps"]


def test_summary_names_the_third_slider():
    values = fields.normalize({"checkpoint": "dir/qwen-turbo.safetensors", "sampler": "Euler", "steps": 8, "cfg": 1,
                               "shift": 3, "width": 1328, "height": 1328})
    assert fields.summary(values) == "qwen-turbo · Euler · 8 steps · CFG 1 · Shift 3 · 1328x1328"
    assert "Distilled CFG 3" in fields.summary(values, dcfg_label="Distilled CFG")
