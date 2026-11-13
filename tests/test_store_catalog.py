import json

import pytest

from lib_model_profiles.catalog import Catalog, read_suggestions, scan
from lib_model_profiles.fields import ProfileError
from lib_model_profiles.store import Store


def test_store_survives_a_restart(tmp_path):
    path = tmp_path / "model_profiles.json"
    store = Store(str(path))
    p = store.add("qwen21", "  Turbo   8 steps ", {"steps": 8})
    store.set_default("qwen21", p["id"])
    store.set_active("qwen21", p["id"])
    again = Store(str(path))
    assert again.profiles("qwen21")[0]["name"] == "Turbo 8 steps"
    assert again.default("qwen21") == p["id"] and again.active("qwen21") == p["id"]


def test_backups_of_the_profiles_file(tmp_path):
    path = tmp_path / "model_profiles.json"
    store = Store(str(path), backup_dir=str(tmp_path / "backups"))
    store.add("qwen21", "One", {"steps": 8})
    store.add("qwen21", "Two", {"steps": 4})
    previous = json.loads((tmp_path / "model_profiles.json.bak").read_text(encoding="utf-8"))
    assert [p["name"] for p in previous["profiles"]["qwen21"]] == ["One"]
    assert len(list((tmp_path / "backups").glob("model_profiles-*.json"))) == 1  # one a day


def test_daily_copy_keeps_the_newest(tmp_path):
    from lib_model_profiles.history import daily_copy
    src = tmp_path / "config.json"
    src.write_text("{}", encoding="utf-8")
    for day in range(1, 20):
        daily_copy(str(src), str(tmp_path / "b"), "config", keep=14, today=f"202611{day:02d}")
    kept = sorted(p.name for p in (tmp_path / "b").glob("config-*.json"))
    assert len(kept) == 14 and kept[-1] == "config-20261119.json"
    assert daily_copy(str(src), str(tmp_path / "b"), "config", today="20261119") is None


def test_names_stay_unique_per_preset(tmp_path):
    store = Store(str(tmp_path / "p.json"))
    a = store.add("qwen21", "Turbo", {"steps": 8})
    b = store.add("qwen21", "turbo", {"steps": 4})
    c = store.add("xl", "Turbo", {"steps": 6})
    assert (a["name"], b["name"], c["name"]) == ("Turbo", "turbo 2", "Turbo")
    assert store.rename("qwen21", b["id"], "Turbo")["name"] == "Turbo 2"


def test_delete_clears_default_and_active(tmp_path):
    store = Store(str(tmp_path / "p.json"))
    p = store.add("qwen21", "Base", {"steps": 20})
    store.set_default("qwen21", p["id"])
    store.set_active("qwen21", p["id"])
    store.delete("qwen21", p["id"])
    assert store.profiles("qwen21") == [] and store.default("qwen21") is None and store.active("qwen21") is None
    with pytest.raises(ProfileError):
        store.delete("qwen21", p["id"])


def test_a_broken_file_is_kept_aside_not_overwritten(tmp_path):
    path = tmp_path / "model_profiles.json"
    path.write_text("{not json", encoding="utf-8")
    store = Store(str(path))
    assert store.profiles("qwen21") == []
    assert (tmp_path / "model_profiles.json.broken").read_text(encoding="utf-8") == "{not json"


def test_a_bad_saved_profile_is_skipped_the_rest_loads(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"profiles": {"qwen21": [
        {"id": "u:1", "name": "Good", "values": {"txt2img": {"steps": 8}}},
        {"id": "u:2", "name": "Bad", "values": {"txt2img": {"steps": "x"}}},
    ]}}), encoding="utf-8")
    assert [p["name"] for p in Store(str(path)).profiles("qwen21")] == ["Good"]


def _write(folder, data):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model_profiles.json").write_text(json.dumps(data), encoding="utf-8")


def test_suggestions_from_extension_folders(tmp_path):
    _write(tmp_path / "qwen", {"profiles": [
        {"preset": "qwen21", "name": "Base", "values": {"sampler": "Euler", "steps": 20, "cfg": 2.5}},
        {"preset": "qwen21", "name": "Broken", "values": {"steps": "x"}},
        {"preset": "qwen21", "name": "Base", "values": {"steps": 1}},
    ]})
    (tmp_path / "empty").mkdir()
    found = scan([("qwen2.1-forge-neo", str(tmp_path / "qwen")), ("other", str(tmp_path / "empty"))])
    assert [(s["name"], s["source"], s["id"]) for s in found] == [
        ("Base", "qwen2.1-forge-neo", "s:qwen2.1-forge-neo:qwen21:base")]


def test_unreadable_suggestion_file_is_ignored(tmp_path):
    (tmp_path / "model_profiles.json").write_text("[", encoding="utf-8")
    assert read_suggestions(str(tmp_path / "model_profiles.json"), "x") == []


def test_catalog_order_labels_and_star():
    user = [{"id": "u:1", "name": "Turbo", "values": {"txt2img": {"steps": 8}}}]
    sugg = [{"id": "s:q:qwen21:base", "arch": "qwen21", "name": "Base", "kind": "suggestion", "source": "q",
             "values": {"txt2img": {"steps": 20}}},
            {"id": "s:q:xl:pony", "arch": "xl", "name": "Pony", "kind": "suggestion", "source": "q",
             "values": {"txt2img": {"steps": 25}}}]
    cat = Catalog("qwen21", user, sugg, {"txt2img": {"steps": 32}}, default_id="u:1")
    assert cat.choices() == [("★ Turbo", "u:1"), ("Base · q", "s:q:qwen21:base"),
                             ("Forge default · Forge", "f:qwen21")]
    assert cat.find_name("forge DEFAULT")["id"] == "f:qwen21"
