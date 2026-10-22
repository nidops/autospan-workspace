from __future__ import annotations

import pytest
import yaml

from aworkspace import catalog, engine


def simple(name="autospan", *, path=None, depend=None, package=True):
    d = {"url": "/tmp/fake.git", "revision": "main", "default": True}
    if path:
        d["path"] = path
    if depend:
        d["depends"] = depend
    if package:
        d["python"] = {"distribution": name}
    return d


def write_doc(tmp_path, repos, schema=1):
    p = tmp_path / "repos.local.yaml"
    p.write_text(yaml.safe_dump({"schema": schema, "repositories": repos}))
    return p


def load(tmp_path, repos):
    return catalog.read_catalog(write_doc(tmp_path, repos), tmp_path)


def test_dynamic_custom_plugins_need_no_code_edits(tmp_path):
    doc = load(
        tmp_path,
        {
            "autospan": simple(),
            "sample-plugin": simple("sample-plugin", depend=["autospan"]),
        },
    )
    output = catalog.kconfig_text(doc)
    assert "config COMP_SAMPLE_PLUGIN" in output
    assert "depends on COMP_AUTOSPAN" in output
    projects = catalog.selected(doc, {"COMP_AUTOSPAN": True, "COMP_SAMPLE_PLUGIN": True})
    assert [p["name"] for p in projects] == ["autospan", "sample-plugin"]


def test_features_map_to_real_extra_names(tmp_path):
    d = simple()
    d["python"]["features"] = {
        "blueio": {"extra": "blueio", "default": True},
        "raytac": {"extra": "raytac", "default": False},
    }
    doc = load(tmp_path, {"autospan": d})
    projects = catalog.selected(doc, {"COMP_AUTOSPAN": True, "FEAT_AUTOSPAN_BLUEIO": True})
    assert projects[0]["extras"] == ["blueio"]
    assert "config FEAT_AUTOSPAN_RAYTAC" in catalog.kconfig_text(doc)


def test_placeholder_rejected_only_if_selected(tmp_path):
    d = simple()
    d["url"] = "https://github.com/REPLACE-ME/autospan.git"
    doc = load(tmp_path, {"autospan": d})
    assert catalog.selected(doc, {}) == []
    with pytest.raises(catalog.WorkspaceError, match="placeholder"):
        catalog.selected(doc, {"COMP_AUTOSPAN": True})


@pytest.mark.parametrize(
    "value", ["../evil", "./a", "a/../b", "/abs", "a//b", "a\\b", "a:b", ".west", "a/.secret"]
)
def test_invalid_paths(tmp_path, value):
    with pytest.raises(catalog.WorkspaceError):
        load(tmp_path, {"autospan": simple(path=value)})


def test_prevent_nested_path_collisions(tmp_path):
    with pytest.raises(catalog.WorkspaceError, match="overlapping"):
        load(
            tmp_path, {"a": simple("a", path="projects/a"), "b": simple("b", path="projects/a/sub")}
        )


def test_prevent_name_symbol_collision(tmp_path):
    with pytest.raises(catalog.WorkspaceError, match="symbol collision"):
        load(tmp_path, {"some-thing": simple("some-thing"), "some_thing": simple("some_thing")})


def test_duplicate_yaml_rejected(tmp_path):
    p = tmp_path / "repos.local.yaml"
    p.write_text(
        "schema: 1\nrepositories:\n  x: {url: /tmp/a, revision: main}\n"
        "  x: {url: /tmp/b, revision: main}\n"
    )
    with pytest.raises(catalog.WorkspaceError, match="Duplicate"):
        catalog.read_catalog(p, tmp_path)


def test_missing_dependency_rejected(tmp_path):
    with pytest.raises(catalog.WorkspaceError, match="unknown dependencies"):
        load(tmp_path, {"a": simple("a", depend=["missing"])})


def test_cycle_rejected(tmp_path):
    with pytest.raises(catalog.WorkspaceError, match="Dependency cycle"):
        load(tmp_path, {"a": simple("a", depend=["b"]), "b": simple("b", depend=["a"])})


def test_yaml_validation_blocks_unknown_fields(tmp_path):
    d = simple()
    d["frobnicate"] = 3
    with pytest.raises(catalog.WorkspaceError, match="unknown fields"):
        load(tmp_path, {"a": d})


def test_selected_source_paths_are_relative_to_repos(tmp_path):
    root = tmp_path / "any-valid-name"
    root.mkdir()
    doc = {"schema": 1, "repositories": {"a": simple("a")}}
    chosen = catalog.selected(doc, {"COMP_A": True})
    output = engine.selection_text(chosen)
    result = yaml.safe_load(output)
    assert result["repositories"][0]["name"] == "a"
    assert engine.checkout_path(root, chosen[0]) == root / "repos" / "a"


def test_check_missing_python_metadata_fails(tmp_path):
    p = {"name": "ble", "python": {"distribution": "ble"}, "extras": []}
    with pytest.raises(catalog.WorkspaceError, match="expected a Python package"):
        engine.check_package_metadata(tmp_path, p)


def test_missing_selected_extra_fails(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="ble"\nversion="0.1"\n')
    p = {"name": "ble", "python": {"distribution": "ble"}, "extras": ["blueio"]}
    with pytest.raises(catalog.WorkspaceError, match="configured extras"):
        engine.check_package_metadata(tmp_path, p)


def test_extras_declared_in_package_pass(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        """[project]\nname="ble"\nversion="0.1"\n[project.optional-dependencies]\nblueio=[]\n"""
    )
    p = {"name": "ble", "python": {"distribution": "ble"}, "extras": ["blueio"]}
    engine.check_package_metadata(tmp_path, p)


def test_kconfig_menuconfig_receives_object(monkeypatch, tmp_path):
    from aworkspace import cli

    write_doc(tmp_path, {"a": simple("a")})
    seen = {}

    class Kconfig:
        def __init__(self, name):
            seen["filename"] = name

    class Menu:
        @staticmethod
        def menuconfig(kc):
            seen["kconfig"] = kc

    import sys

    monkeypatch.setitem(sys.modules, "kconfiglib", type("KLib", (), {"Kconfig": Kconfig}))
    monkeypatch.setitem(sys.modules, "menuconfig", Menu)
    cli.menuconfig(tmp_path)
    assert isinstance(seen["kconfig"], Kconfig)
    assert seen["filename"] == "Kconfig"


def test_doctor_fails_for_empty_workspace(tmp_path):
    assert not engine.doctor(tmp_path)
    assert engine.inspect(tmp_path)


def test_duplicate_python_distribution_normalization_rejected(tmp_path):
    one = simple("a")
    two = simple("b")
    one["python"]["distribution"] = "auto.span"
    two["python"]["distribution"] = "auto_span"
    with pytest.raises(catalog.WorkspaceError, match="duplicate Python distribution"):
        load(tmp_path, {"a": one, "b": two})


def test_pinned_tools_are_noop_after_successful_install(tmp_path, capsys):
    import hashlib
    import sys

    (tmp_path / "requirements-tools.txt").write_text("test-deps==1.0\n")
    ready = tmp_path / ".tools" / ".ready"
    ready.parent.mkdir()
    exe = engine.python_path(tmp_path / ".tools")
    exe.parent.mkdir(exist_ok=True)
    exe.touch()
    reqs = (tmp_path / "requirements-tools.txt").read_bytes()
    ready.write_text(hashlib.sha256(reqs + sys.version.encode()).hexdigest())
    engine.tools(tmp_path)
    assert "already installed" in capsys.readouterr().out


def test_package_distribution_mismatch_is_rejected(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="different-package"\nversion="0.1"\n')
    project = {"name": "core", "python": {"distribution": "autospan"}, "extras": []}
    with pytest.raises(catalog.WorkspaceError, match="distribution"):
        engine.check_package_metadata(tmp_path, project)


def test_tools_delegation_uses_environment_prefix(monkeypatch, tmp_path):
    import sys

    from aworkspace import cli

    executable = engine.python_path(tmp_path / ".tools")
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "outside-env"))
    calls = []
    monkeypatch.setattr(engine, "run", lambda args, **kw: calls.append(args) or "")
    with pytest.raises(SystemExit) as exc:
        cli._delegate_to_tools("doctor", ["doctor"])
    assert exc.value.code == 0
    assert calls[0][0] == str(executable)


@pytest.mark.parametrize("metadata", ["name=123", 'name="autospan"\noptional-dependencies=[]'])
def test_malformed_python_metadata_is_actionable(tmp_path, metadata):
    (tmp_path / "pyproject.toml").write_text("[project]\n" + metadata + "\n")
    project = {"name": "core", "python": {"distribution": "autospan"}, "extras": []}
    with pytest.raises(catalog.WorkspaceError):
        engine.check_package_metadata(tmp_path, project)


def test_public_example_uses_https_and_private_ssh_still_valid(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    doc = catalog.read_catalog(root / "repos.example.yaml", root)
    assert all(
        spec["url"].startswith("https://github.com/") for spec in doc["repositories"].values()
    )
    private = simple("private-plugin")
    private["url"] = "git@github.com:private-org/private-plugin.git"
    assert (
        load(tmp_path, {"private-plugin": private})["repositories"]["private-plugin"]["url"]
        == private["url"]
    )


def test_generated_menu_rejects_symlink_without_writing_outside_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".generated").symlink_to(outside, target_is_directory=True)
    with pytest.raises(catalog.WorkspaceError, match="symlink"):
        catalog.write_generated_menu(root, {"repositories": {"a": simple("a")}})
    assert not list(outside.iterdir())


def test_null_python_metadata_is_rejected(tmp_path):
    spec = simple()
    spec["python"] = None
    with pytest.raises(catalog.WorkspaceError, match="python must be a mapping"):
        load(tmp_path, {"autospan": spec})
