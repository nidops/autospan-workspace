"""Offline integration checks for plugin-scoped worktrees and environments."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from aworkspace import catalog, cli, engine, plugins


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def origin(root: Path, name: str, *, manifest: bool = False) -> Path:
    path = root / "origins" / name
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "README.md").write_text(name)
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.0.1"\n[project.optional-dependencies]\ntest=[]\n'
    )
    (path / "tests").mkdir()
    (path / "tests/test_smoke.py").write_text("def test_smoke(): assert True\n")
    if manifest:
        (path / "autospan-workspace.yaml").write_text(
            "schema: 1\ntests: [tests]\nconsole:\n  config: bench/fake.yaml\n"
            "profiles:\n  fake: {tests: [tests], console: {config: bench/fake.yaml}}\n"
            "  hardware: {hardware: true, console: {config: bench/hardware.yaml}}\n"
        )
        (path / "bench").mkdir()
        (path / "bench/fake.yaml").write_text("components: {}\n")
        (path / "bench/hardware.yaml").write_text("components: {}\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "initial")
    return path


@pytest.fixture
def lab(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "requirements-tools.txt").write_text("fixture tools\n")
    core = origin(tmp_path, "autospan")
    ble = origin(tmp_path, "autospan-ble", manifest=True)
    enterprise = origin(tmp_path, "autospan-enterprise-x")
    doc = {
        "schema": 1,
        "repositories": {
            "autospan": {
                "url": str(core),
                "revision": "main",
                "python": {"distribution": "autospan"},
            },
            "autospan-ble": {
                "url": str(ble),
                "revision": "main",
                "depends": ["autospan"],
                "python": {"distribution": "autospan-ble"},
                "tests": ["tests"],
            },
            "autospan-enterprise-x": {
                "url": str(enterprise),
                "revision": "main",
                "depends": ["autospan"],
                "python": {"distribution": "autospan-enterprise-x"},
                "tests": ["tests"],
            },
        },
    }
    (root / "repos.local.yaml").write_text(yaml.safe_dump(doc))
    env_calls = []

    def fake_environment(_root, sources, flags, heads, *, checkouts, base):
        key = engine.env_fingerprint(sources, heads, flags, _root, checkouts=checkouts)
        path = base / key
        path.mkdir(parents=True, exist_ok=True)
        (path / ".ready").write_text(key)
        env_calls.append((base.name, list(checkouts)))
        return key, path

    monkeypatch.setattr(engine, "environment", fake_environment)
    monkeypatch.setattr(engine, "check_installed", lambda *_: None)
    return root, doc, env_calls


def test_discover_plugin_aliases_and_dependency_closure(lab):
    root, _, _ = lab
    doc = plugins.read_catalog(root)
    assert plugins.plugin_names(doc) == ["autospan-ble", "autospan-enterprise-x"]
    assert plugins.resolve_plugin(doc, "ble") == "autospan-ble"
    assert [p["name"] for p in plugins.plugin_sources(doc, "autospan-ble")] == [
        "autospan",
        "autospan-ble",
    ]
    with pytest.raises(catalog.WorkspaceError, match="Unknown plugin"):
        plugins.resolve_plugin(doc, "structwire")


def test_plugin_catalog_can_mark_custom_name(lab):
    root, doc, _ = lab
    doc["repositories"]["autospan-ble"]["plugin"] = False
    doc["repositories"]["autospan"]["plugin"] = True
    (root / "repos.local.yaml").write_text(yaml.safe_dump(doc))
    assert plugins.plugin_names(plugins.read_catalog(root)) == ["autospan", "autospan-enterprise-x"]


def test_plugin_bootstrap_keeps_all_repos_inside_workspace_and_separate_envs(lab):
    root, _, calls = lab
    plugins.bootstrap(root, "ble")
    ble = json.loads(plugins.state_path(root, "autospan-ble").read_text())
    assert ble["selected"] == ["autospan", "autospan-ble"]
    assert ble["env"].startswith(str(root / ".envs" / "autospan-ble"))
    assert (root / "repos/autospan/.git").is_dir()
    assert (root / "repos/autospan-ble/.git").is_dir()
    assert (root / ble["checkouts"]["autospan-ble"] / ".git").exists()
    assert plugins.doctor(root, "ble")

    plugins.bootstrap(root, "enterprise-x")
    enterprise = json.loads(plugins.state_path(root, "autospan-enterprise-x").read_text())
    assert enterprise["selected"] == ["autospan", "autospan-enterprise-x"]
    assert enterprise["env"] != ble["env"]
    assert plugins.doctor(root, "ble")
    assert plugins.doctor(root, "enterprise-x")
    assert calls == [
        ("autospan-ble", ["autospan", "autospan-ble"]),
        ("autospan-enterprise-x", ["autospan", "autospan-enterprise-x"]),
    ]
    assert not (root.parent / "autospan").exists()


def test_bootstrap_does_not_move_dirty_developer_checkout(lab):
    root, doc, _ = lab
    plugins.bootstrap(root, "ble")
    developer = root / "repos" / "autospan"
    old_head = engine.git_head(developer)
    (developer / "README.md").write_text("uncommitted developer work")
    upstream = Path(doc["repositories"]["autospan"]["url"])
    (upstream / "README.md").write_text("upstream update")
    git(upstream, "add", ".")
    git(upstream, "commit", "-qm", "upstream update")
    plugins.bootstrap(root, "enterprise-x")
    assert engine.git_head(developer) == old_head
    assert (developer / "README.md").read_text() == "uncommitted developer work"
    assert plugins.doctor(root, "ble")
    assert plugins.doctor(root, "enterprise-x")
    assert (
        plugins._read_state(root, "autospan-ble")["heads"]["autospan"]
        != plugins._read_state(root, "autospan-enterprise-x")["heads"]["autospan"]
    )


def test_plugin_install_never_fetches_and_refuses_tampered_worktree(lab, monkeypatch):
    root, _, _ = lab
    plugins.bootstrap(root, "ble")
    monkeypatch.setattr(plugins, "_resolve_heads", lambda *_: pytest.fail("unexpected fetch"))
    plugins.install(root, "ble")
    state = plugins._read_state(root, "autospan-ble")
    pinned = root / state["checkouts"]["autospan-ble"]
    (pinned / "README.md").write_text("changed")
    assert not plugins.doctor(root, "ble")
    with pytest.raises(catalog.WorkspaceError, match="dirty"):
        plugins.install(root, "ble")


def test_plugin_failed_install_does_not_activate_failed_state(lab, monkeypatch):
    root, doc, _ = lab
    plugins.bootstrap(root, "ble")
    previous = plugins._read_state(root, "autospan-ble")
    source = Path(doc["repositories"]["autospan-ble"]["url"])
    (source / "README.md").write_text("new version")
    git(source, "add", ".")
    git(source, "commit", "-qm", "new version")

    def fail(*_args, **_kwargs):
        raise catalog.WorkspaceError("pip failure")

    monkeypatch.setattr(engine, "environment", fail)
    with pytest.raises(catalog.WorkspaceError, match="pip failure"):
        plugins.bootstrap(root, "ble")
    assert plugins._read_state(root, "autospan-ble") == previous
    assert plugins._state_checkouts(
        root,
        "autospan-ble",
        plugins.plugin_sources(plugins.read_catalog(root), "autospan-ble"),
        previous,
    )


def test_plugin_manifest_drives_tests_and_console(lab, monkeypatch):
    root, _, _ = lab
    plugins.bootstrap(root, "ble")
    commands = []
    original_run = engine.run

    def record(args, **kw):
        if "-m" in args and ("pytest" in args or "autospan" in args):
            commands.append((args, kw))
            return ""
        return original_run(args, **kw)

    monkeypatch.setattr(engine, "run", record)
    plugins.test(root, "ble", [], ["-q"])
    args, kw = commands[-1]
    assert args[-2:] == ["tests", "-q"]
    assert kw["cwd"] == (
        root / plugins._read_state(root, "autospan-ble")["checkouts"]["autospan-ble"]
    )
    plugins.console(root, "ble", None, ["--plain"])
    args, _ = commands[-1]
    assert args[-2:] == [str(kw["cwd"] / "bench/fake.yaml"), "--plain"]
    with pytest.raises(catalog.WorkspaceError, match="missing"):
        plugins.console(root, "ble", "does-not-exist.yaml", [])


def test_plugin_catalog_change_requires_bootstrap(lab):
    root, doc, _ = lab
    plugins.bootstrap(root, "ble")
    doc["repositories"]["autospan-ble"]["revision"] = "different-ref"
    (root / "repos.local.yaml").write_text(yaml.safe_dump(doc))
    assert any("catalog changed" in e for e in plugins.inspect(root, "ble"))


def test_plugin_selection_persists_without_mutating_environments(lab):
    root, _, _ = lab
    plugins.select_plugin(root, "ble")
    assert plugins.selected_plugin(root) == "autospan-ble"
    assert not (root / ".envs").exists()
    plugins.select_plugin(root, "enterprise-x")
    assert plugins.selected_plugin(root) == "autospan-enterprise-x"
    assert not (root / "repos").exists()


def test_plugin_symlink_root_rejected(lab):
    root, _, _ = lab
    external = root.parent / "external"
    external.mkdir()
    (root / "repos").symlink_to(external, target_is_directory=True)
    with pytest.raises(catalog.WorkspaceError, match="symlink"):
        plugins.bootstrap(root, "ble")
    assert not list(external.iterdir())


def test_cli_explicit_plugin_overrides_default(lab, monkeypatch):
    root, _, _ = lab
    monkeypatch.setattr(cli, "ROOT", root)
    plugins.select_plugin(root, "ble")
    seen = []
    monkeypatch.setattr(plugins, "bootstrap", lambda root, name: seen.append(name))
    cli.main(["bootstrap", "--plugin", "enterprise-x"])
    cli.main(["bootstrap"])
    assert seen == ["enterprise-x", "autospan-ble"]


def test_manifest_validation_rejects_unsafe_paths(lab):
    root, _, _ = lab
    plugins.bootstrap(root, "ble")
    state = plugins._read_state(root, "autospan-ble")
    pinned = root / state["checkouts"]["autospan-ble"]
    # The source itself is immutable; validate directly to exercise bad metadata.
    (pinned / "autospan-workspace.yaml").write_text("schema: 1\ntests: [../escape]\n")
    with pytest.raises(catalog.WorkspaceError, match="Unsafe checkout path"):
        plugins._load_manifest(pinned)


def test_profile_hardware_gate_and_override(lab, monkeypatch):
    root, _, _ = lab
    plugins.bootstrap(root, "ble")
    calls = []
    original_run = engine.run

    def record(args, **kw):
        if "-m" in args and ("pytest" in args or "autospan" in args):
            calls.append(args)
            return ""
        return original_run(args, **kw)

    monkeypatch.setattr(engine, "run", record)
    monkeypatch.setattr(engine, "run_hosted_tests", lambda args, **kw: calls.append(args))
    plugins.console(root, "ble", None, [], profile="hardware")
    assert calls[-1][-1] == "--hardware"
    with pytest.raises(catalog.WorkspaceError, match="Hardware profile"):
        plugins.test(root, "ble", [], [], profile="hardware")
    plugins.console(root, "ble", None, ["--hardware"], profile="hardware")
    assert Path(calls[-1][-2]).parts[-2:] == ("bench", "hardware.yaml")
    plugins.test(root, "ble", [], ["--hardware"], profile="hardware")
    assert calls[-1][-1] == "--hardware"
    assert calls[-1][2:5] == ["autospan", "test", "--config"]
    assert Path(calls[-1][5]).parts[-2:] == ("bench", "hardware.yaml")
    assert calls[-1][6] == "--"
    with pytest.raises(catalog.WorkspaceError, match="Unknown plugin profile"):
        plugins.console(root, "ble", None, [], profile="does-not-exist")


def test_make_forwards_plugin_and_profile(tmp_path):
    import os
    import shutil
    import sys

    if os.name != "posix" or shutil.which("make") is None:
        pytest.skip("GNU Make test requires POSIX")
    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Makefile").write_text((original / "Makefile").read_text())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ws.py").write_text("import json,sys; print(json.dumps(sys.argv[1:]))\n")
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "test",
            f"PYTHON={sys.executable}",
            "PLUGIN=ble",
            "PROFILE=fake",
            "CONFIG=private.local.yaml",
            "TEST_PATH=tests/unit",
            "PYTEST_ARGS=-q",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [
        "test",
        "--plugin",
        "ble",
        "--profile",
        "fake",
        "--config",
        "private.local.yaml",
        "tests/unit",
        "--",
        "-p",
        "metadata",
        "-p",
        "no:html",
        "-p",
        "pytest_html.plugin",
        "--html=artifacts/report.html",
        "--self-contained-html",
        "-q",
    ]


def test_console_accepts_explicit_local_developer_bench(lab, monkeypatch):
    root, _, _ = lab
    plugins.bootstrap(root, "ble")
    local_config = root / "repos/autospan-ble/private.local.yaml"
    local_config.write_text("components: {}\n")
    calls = []
    original_run = engine.run

    def record(args, **kw):
        if "-m" in args and "autospan" in args:
            calls.append(args)
            return ""
        return original_run(args, **kw)

    monkeypatch.setattr(engine, "run", record)
    plugins.console(root, "ble", "private.local.yaml", ["--plain"])
    assert calls[-1][-2:] == [str(local_config), "--plain"]
    assert plugins.doctor(root, "ble")


def test_two_plugins_pin_different_core_commits_from_same_catalog(lab):
    root, doc, _ = lab
    core = Path(doc["repositories"]["autospan"]["url"])
    old_sha = git(core, "rev-parse", "HEAD")
    (core / "README.md").write_text("core v2")
    git(core, "add", ".")
    git(core, "commit", "-qm", "core v2")
    new_sha = git(core, "rev-parse", "HEAD")

    ble = Path(doc["repositories"]["autospan-ble"]["url"])
    manifest = ble / "autospan-workspace.yaml"
    manifest.write_text(manifest.read_text() + f"revisions:\n  autospan: {old_sha}\n")
    git(ble, "add", ".")
    git(ble, "commit", "-qm", "pin core v1 for BLE")

    plugins.bootstrap(root, "ble")
    plugins.bootstrap(root, "enterprise-x")
    ble_state = plugins._read_state(root, "autospan-ble")
    enterprise_state = plugins._read_state(root, "autospan-enterprise-x")
    assert ble_state["heads"]["autospan"] == old_sha
    assert enterprise_state["heads"]["autospan"] == new_sha
    assert plugins.doctor(root, "ble")
    assert plugins.doctor(root, "enterprise-x")
    assert (root / "repos/autospan/README.md").read_text() == "core v2"


def test_plugin_revision_override_must_reference_dependency(lab):
    root, doc, _ = lab
    ble = Path(doc["repositories"]["autospan-ble"]["url"])
    manifest = ble / "autospan-workspace.yaml"
    manifest.write_text(manifest.read_text() + "revisions:\n  autospan-ble: main\n")
    git(ble, "add", ".")
    git(ble, "commit", "-qm", "invalid override")
    with pytest.raises(catalog.WorkspaceError, match="non-dependency"):
        plugins.bootstrap(root, "ble")
    assert not plugins.state_path(root, "autospan-ble").exists()


def test_symlinked_generated_state_root_rejected(lab):
    root, _, _ = lab
    external = root.parent / "external-generated"
    external.mkdir()
    (root / ".generated").symlink_to(external, target_is_directory=True)
    with pytest.raises(catalog.WorkspaceError, match=".generated/"):
        plugins.select_plugin(root, "ble")
    assert not list(external.iterdir())


@pytest.mark.parametrize("paths", [[], ["project_scenarios/test_smoke.py"]])
@pytest.mark.parametrize("checkout_suites", [[], ["missing-checkout-tests"]])
def test_configured_tests_use_host_and_private_workspace_config(
    lab, monkeypatch, paths, checkout_suites
):
    root, _, _ = lab
    plugins.bootstrap(root, "ble")
    env, checkout, manifest = plugins.active(root, "ble")
    manifest["tests"] = checkout_suites
    monkeypatch.setattr(plugins, "active", lambda *_: (env, checkout, manifest))
    config = root / "bench.local.yaml"
    config.write_text("autospan: {}\n")
    calls = []
    monkeypatch.setattr(
        engine, "run_hosted_tests", lambda args, **kwargs: calls.append((args, kwargs))
    )
    plugins.test(root, "ble", paths, ["--hardware", "-q"], config="bench.local.yaml")
    args, kwargs = calls[-1]
    assert args[1:] == [
        "-m",
        "autospan",
        "test",
        "--config",
        str(config),
        "--",
        *paths,
        "--hardware",
        "-q",
    ]
    assert kwargs["cwd"].is_relative_to(root / "repos/.isolated")
    assert len(calls) == 2
    assert calls[0] == ([*args, "--collect-only"], kwargs)
    calls.clear()
    with pytest.raises(catalog.WorkspaceError, match="configuration missing"):
        plugins.test(root, "ble", [], [], config="missing.local.yaml")
    assert not calls
