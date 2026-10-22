"""Offline integration tests with real local Git clone/fetch/checkout.

Only pip environment installation and Kconfig selection are simulated here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from aworkspace import catalog, engine


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def fixture_repo(root: Path, name: str, *, python: bool = True) -> Path:
    repo = root / "origins" / name
    repo.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "fixture@example.invalid", cwd=repo)
    git("config", "user.name", "Fixture", cwd=repo)
    (repo / "README.md").write_text(name)
    if python:
        (repo / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.0.1"\n'
            "[project.optional-dependencies]\nblueio=[]\nraytac=[]\n"
        )
    git("add", ".", cwd=repo)
    git("commit", "-qm", "fixture", cwd=repo)
    return repo


@pytest.fixture
def rig(tmp_path, monkeypatch):
    base = tmp_path / "lab"
    base.mkdir()
    root = base / "arbitrary-workspace-name"
    root.mkdir()
    (root / "requirements-tools.txt").write_text("fixture-tools\n")
    tools_python = engine.python_path(root / ".tools")
    tools_python.parent.mkdir(parents=True)
    tools_python.touch()
    core = fixture_repo(tmp_path, "autospan")
    ble = fixture_repo(tmp_path, "autospan-ble")
    custom = fixture_repo(tmp_path, "sample-plugin")
    repos = {
        "autospan": {
            "url": str(core),
            "revision": "main",
            "path": "autospan",
            "default": True,
            "python": {"distribution": "autospan"},
        },
        "autospan-ble": {
            "url": str(ble),
            "revision": "main",
            "path": "autospan-ble",
            "default": False,
            "depends": ["autospan"],
            "python": {
                "distribution": "autospan-ble",
                "features": {
                    "blueio": {"extra": "blueio", "default": True},
                },
            },
        },
        "sample-plugin": {
            "url": str(custom),
            "revision": "main",
            "path": "sample-plugin",
            "default": False,
            "depends": ["autospan"],
            "python": {"distribution": "sample-plugin"},
            "tests": ["tests"],
        },
    }
    doc = {"schema": 1, "repositories": repos}
    (root / "repos.local.yaml").write_text(yaml.safe_dump(doc))
    flags = {
        "COMP_AUTOSPAN": True,
        "COMP_AUTOSPAN_BLE": False,
        "COMP_SAMPLE_PLUGIN": False,
        "AUTOSPAN_EDITABLE": True,
        "FEAT_AUTOSPAN_BLE_BLUEIO": False,
    }

    def fake_selection(_root, persist=False):
        catalog.write_generated_menu(root, doc)
        return doc, dict(flags), catalog.selected(doc, flags)

    calls = []

    def fake_environment(_root, projects, _flags, heads):
        key = engine.env_fingerprint(projects, heads, _flags, root)
        env = root / ".envs" / key
        env.mkdir(parents=True, exist_ok=True)
        (env / ".ready").write_text(key)
        calls.append([p["name"] for p in projects])
        return key, env

    monkeypatch.setattr(engine, "load_selection", fake_selection)
    monkeypatch.setattr(engine, "environment", fake_environment)
    monkeypatch.setattr(engine, "check_installed", lambda *_: None)
    return root, doc, flags, calls


def test_bootstrap_from_git_repositories(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    state = engine.get_state(root)
    assert state["selected"] == ["autospan"]
    assert len(state["heads"]["autospan"]) == 40
    assert (root / "repos" / "autospan" / ".git").exists()
    assert engine.inspect(root) == []


def test_enable_then_disable_ble_reconciles_active_env(rig):
    root, _, flags, calls = rig
    engine.bootstrap(root)
    old = engine.get_state(root)["env_key"]
    flags["COMP_AUTOSPAN_BLE"] = True
    flags["FEAT_AUTOSPAN_BLE_BLUEIO"] = True
    engine.bootstrap(root)
    intermediate = engine.get_state(root)["env_key"]
    assert intermediate != old
    assert engine.get_state(root)["selected"] == ["autospan", "autospan-ble"]
    assert engine.inspect(root) == []
    flags["COMP_AUTOSPAN_BLE"] = False
    flags["FEAT_AUTOSPAN_BLE_BLUEIO"] = False
    engine.bootstrap(root)
    assert engine.get_state(root)["env_key"] == old
    assert engine.get_state(root)["selected"] == ["autospan"]
    assert (root / "repos" / "autospan-ble").exists()  # Sources stay, plugin is not active.
    assert calls == [["autospan"], ["autospan", "autospan-ble"], ["autospan"]]


def test_configured_customer_plugin_downloads_without_code_changes(rig):
    root, _, flags, _ = rig
    flags["COMP_SAMPLE_PLUGIN"] = True
    engine.bootstrap(root)
    assert (root / "repos" / "sample-plugin" / ".git").exists()
    assert engine.get_state(root)["selected"] == ["autospan", "sample-plugin"]


def test_dirty_checkout_is_preserved_and_blocks_sync(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    head = engine.get_state(root)
    readme = root / "repos" / "autospan" / "README.md"
    readme.write_text("valuable local work")
    with pytest.raises(catalog.WorkspaceError, match="uncommitted changes"):
        engine.bootstrap(root)
    assert readme.read_text() == "valuable local work"
    assert engine.get_state(root) == head


def test_failed_package_install_preserves_old_active_env(rig, monkeypatch):
    root, _, flags, _ = rig
    engine.bootstrap(root)
    previous = engine.get_state(root)
    flags["COMP_AUTOSPAN_BLE"] = True

    def failure(*args):
        raise catalog.WorkspaceError("pip install failed")

    monkeypatch.setattr(engine, "environment", failure)
    with pytest.raises(catalog.WorkspaceError, match="pip install failed"):
        engine.bootstrap(root)
    assert engine.get_state(root) == previous
    assert engine.inspect(root)  # Selections now differ from last successful install.


def test_revision_change_diagnosed(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    dst = root / "repos" / "autospan"
    git("config", "user.email", "fixture@example.invalid", cwd=dst)
    git("config", "user.name", "Fixture", cwd=dst)
    (dst / "README.md").write_text("new commit")
    git("add", ".", cwd=dst)
    git("commit", "-qm", "advance", cwd=dst)
    assert any("differs from active" in e for e in engine.inspect(root))


def test_doctor_succeeds_only_for_consistent_install(rig, capsys):
    root, _, flags, _ = rig
    assert not engine.doctor(root)
    engine.bootstrap(root)
    assert engine.doctor(root)
    flags["COMP_AUTOSPAN_BLE"] = True
    assert not engine.doctor(root)


def test_source_only_project_supported(rig):
    root, doc, flags, _ = rig
    origin = fixture_repo(root.parent.parent, "source-only", python=False)
    doc["repositories"]["source-only"] = {
        "url": str(origin),
        "revision": "main",
        "default": True,
        "path": "source-only",
    }
    flags["COMP_SOURCE_ONLY"] = True
    engine.bootstrap(root)
    assert "source-only" in engine.get_state(root)["selected"]
    assert (root / "repos" / "source-only" / "README.md").exists()


def test_freeze_creates_audit_files_and_explicit_not_lock(rig, monkeypatch):
    root, _, _, _ = rig
    engine.bootstrap(root)
    real_run = engine.run
    monkeypatch.setattr(
        engine,
        "run",
        lambda args, **kw: (
            "-e /workspace/autospan" if "pip" in args and "freeze" in args else real_run(args, **kw)
        ),
    )
    target = engine.freeze(root)
    assert (target / "sources-frozen.yaml").exists()
    assert (target / "requirements-frozen.txt").exists()
    assert (target / "state.json").exists()


def test_origin_mismatch_is_refused(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    checkout = root / "repos" / "autospan"
    git("remote", "set-url", "origin", "/tmp/not-the-configured-origin", cwd=checkout)
    with pytest.raises(catalog.WorkspaceError, match="checkout origin"):
        engine.bootstrap(root)


def test_checkout_symlink_refused(rig):
    root, _, _, _ = rig
    outside = root.parent / "outside"
    outside.mkdir()
    (root / "repos").mkdir()
    (root / "repos" / "autospan").symlink_to(outside, target_is_directory=True)
    with pytest.raises(catalog.WorkspaceError, match="symlink"):
        engine.bootstrap(root)


def test_feature_only_change_requires_sync(rig):
    root, _, flags, _ = rig
    flags["COMP_AUTOSPAN_BLE"] = True
    engine.bootstrap(root)
    flags["FEAT_AUTOSPAN_BLE_BLUEIO"] = True
    assert any("features changed" in err for err in engine.inspect(root))


def test_test_command_rejects_undefined_tests(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    with pytest.raises(catalog.WorkspaceError, match="No tests configured"):
        engine.run_tests(root, [])


def test_test_command_uses_active_env_interpreter(rig, monkeypatch):
    root, _, _, _ = rig
    engine.bootstrap(root)
    calls = []
    original_run = engine.run
    monkeypatch.setattr(
        engine,
        "run",
        lambda argv, **kwargs: (
            (calls.append(argv) or "")
            if argv[1:3] == ["-m", "pytest"]
            else original_run(argv, **kwargs)
        ),
    )
    engine.run_tests(root, ["repos/autospan/tests/test_smoke.py"])
    assert calls
    assert calls[-1][0] == str(engine.python_path(Path(engine.get_state(root)["env"])))
    assert calls[-1][1:3] == ["-m", "pytest"]
    assert "repos/autospan/tests/test_smoke.py" in calls[-1]
    assert "--import-mode=importlib" in calls[-1]


@pytest.mark.parametrize("custom", [[], ["project_scenarios/test_smoke.py"]])
def test_editable_hardware_tests_delegate_collection_to_host(rig, monkeypatch, custom):
    root, _, _, _ = rig
    engine.bootstrap(root)
    config = root / "bench config.local.yaml"
    config.write_text("autospan: {}\n")
    calls = []
    original_run = engine.run

    def record(args, **kwargs):
        if args[1:3] == ["-m", "autospan"]:
            calls.append((args, kwargs))
            return ""
        return original_run(args, **kwargs)

    monkeypatch.setattr(engine, "run", record)
    engine.run_tests(root, custom, ["--hardware", "-k", "gatt and forward"], config=config.name)
    args, kwargs = calls[-1]
    assert args == [
        str(engine.python_path(Path(engine.get_state(root)["env"]))),
        "-m",
        "autospan",
        "test",
        "--config",
        str(config),
        "--",
        *custom,
        "--hardware",
        "-k",
        "gatt and forward",
    ]
    assert kwargs["cwd"] == root
    with pytest.raises(catalog.WorkspaceError, match="configuration missing"):
        engine.run_tests(root, custom, config="missing.local.yaml")
    assert len(calls) == 1


def test_workspace_parent_never_receives_managed_checkouts(rig):
    root, _, flags, _ = rig
    flags["COMP_AUTOSPAN_BLE"] = True
    engine.bootstrap(root)
    assert (root / "repos" / "autospan" / ".git").is_dir()
    assert (root / "repos" / "autospan-ble" / ".git").is_dir()
    assert not (root.parent / "autospan").exists()
    assert not (root.parent / "autospan-ble").exists()
    assert not (root.parent / ".west").exists()


def test_clean_remote_revision_update_moves_checkout_without_reset(rig):
    root, doc, _, _ = rig
    engine.bootstrap(root)
    old = engine.get_state(root)["heads"]["autospan"]
    source = Path(doc["repositories"]["autospan"]["url"])
    (source / "README.md").write_text("upstream update")
    git("add", "README.md", cwd=source)
    git("commit", "-qm", "upstream change", cwd=source)
    engine.bootstrap(root)
    assert engine.get_state(root)["heads"]["autospan"] != old
    assert (root / "repos" / "autospan" / "README.md").read_text() == "upstream update"


def test_repos_root_symlink_is_rejected(rig):
    root, _, _, _ = rig
    external = root.parent / "external"
    external.mkdir()
    (root / "repos").symlink_to(external, target_is_directory=True)
    with pytest.raises(catalog.WorkspaceError, match="repos/.*symlink"):
        engine.bootstrap(root)
    assert list(external.iterdir()) == []


def test_gitignore_excludes_all_managed_repositories(rig):
    root, _, _, _ = rig
    original = Path(__file__).resolve().parents[1]
    (root / ".gitignore").write_text((original / ".gitignore").read_text())
    git("init", "-q", cwd=root)
    engine.bootstrap(root)
    ignored = git("check-ignore", "repos/", cwd=root)
    assert ignored == "repos/"
    git("add", "-A", cwd=root)
    staged = git("diff", "--cached", "--name-only", cwd=root).splitlines()
    assert not any(name.startswith("repos/") for name in staged)


def test_failed_environment_repair_preserves_active_files(tmp_path, monkeypatch):
    (tmp_path / "requirements-tools.txt").write_text("fixture-tools\n")
    key = engine.env_fingerprint([], {}, {}, tmp_path)
    active = tmp_path / ".envs" / key
    executable = engine.python_path(active)
    executable.parent.mkdir(parents=True)
    executable.touch()
    (active / ".ready").write_text(key)
    sentinel = active / "developer-file"
    sentinel.write_text("keep the previous environment")

    def corrupted(*args):
        raise catalog.WorkspaceError("broken installed dependencies")

    class Builder:
        def __init__(self, **kwargs):
            pass

        def create(self, path):
            exe = engine.python_path(path)
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.touch()

    def pip_failure(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(engine, "check_installed", corrupted)
    monkeypatch.setattr(engine.venv, "EnvBuilder", Builder)
    monkeypatch.setattr(engine, "run", pip_failure)
    with pytest.raises(subprocess.CalledProcessError):
        engine.environment(tmp_path, [], {}, {})
    assert sentinel.read_text() == "keep the previous environment"
    assert list((tmp_path / ".envs").iterdir()) == [active]


def test_bad_revision_can_be_corrected_without_deleting_checkout(rig):
    root, doc, flags, _ = rig
    flags["COMP_SAMPLE_PLUGIN"] = True
    doc["repositories"]["sample-plugin"]["revision"] = "not-a-real-branch"
    with pytest.raises(catalog.WorkspaceError, match="revision"):
        engine.bootstrap(root)
    doc["repositories"]["sample-plugin"]["revision"] = "main"
    engine.bootstrap(root)
    assert engine.inspect(root) == []


def test_all_revisions_resolve_before_existing_worktrees_change(rig):
    root, doc, flags, _ = rig
    engine.bootstrap(root)
    original = engine.get_state(root)["heads"]["autospan"]
    upstream = Path(doc["repositories"]["autospan"]["url"])
    (upstream / "README.md").write_text("new upstream revision")
    git("add", ".", cwd=upstream)
    git("commit", "-qm", "advance", cwd=upstream)
    flags["COMP_SAMPLE_PLUGIN"] = True
    doc["repositories"]["sample-plugin"]["revision"] = "missing"
    with pytest.raises(catalog.WorkspaceError, match="revision"):
        engine.bootstrap(root)
    assert engine.git_head(root / "repos/autospan") == original


def test_corrupt_state_reports_failure_instead_of_crashing(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    engine.atomic_json(engine.state_file(root), {"schema": 1, "heads": [], "env_key": "../escape"})
    assert not engine.doctor(root)


def test_doctor_rejects_changed_checkout_origin(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    git("remote", "set-url", "origin", "/tmp/wrong-origin", cwd=root / "repos/autospan")
    assert not engine.doctor(root)


def test_doctor_accepts_developer_edits_but_sync_protects_them(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    (root / "repos/autospan/README.md").write_text("work in progress")
    assert engine.doctor(root)
    with pytest.raises(catalog.WorkspaceError, match="uncommitted"):
        engine.bootstrap(root)


def test_concurrent_workspace_mutation_is_rejected(tmp_path):
    import sys

    code = (
        "from pathlib import Path; from aworkspace.engine import operation_lock; "
        "import sys; lock=operation_lock(Path(sys.argv[1])); lock.__enter__()"
    )
    with engine.operation_lock(tmp_path):
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Another workspace operation" in result.stderr
    with engine.operation_lock(tmp_path):
        pass


def test_install_adopts_local_commit_without_fetching_or_detaching(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    checkout = root / "repos/autospan"
    git("switch", "-c", "my-feature", cwd=checkout)
    git("config", "user.name", "Developer", cwd=checkout)
    git("config", "user.email", "developer@example.invalid", cwd=checkout)
    (checkout / "README.md").write_text("my committed change")
    git("add", ".", cwd=checkout)
    git("commit", "-qm", "local work", cwd=checkout)
    local_head = engine.git_head(checkout)
    assert not engine.doctor(root)
    engine.install(root)
    assert engine.get_state(root)["heads"]["autospan"] == local_head
    assert git("branch", "--show-current", cwd=checkout) == "my-feature"
    assert engine.doctor(root)


def test_install_reconciles_dirty_package_metadata(rig):
    root, _, _, _ = rig
    engine.bootstrap(root)
    old = engine.get_state(root)["env_key"]
    metadata = root / "repos/autospan/pyproject.toml"
    metadata.write_text(
        metadata.read_text().replace("[project]\n", '[project]\ndescription = "edited packaging"\n')
    )
    assert not engine.doctor(root)
    engine.install(root)
    assert engine.get_state(root)["env_key"] != old
    assert engine.doctor(root)
    assert git("status", "--porcelain", cwd=root / "repos/autospan")


def test_project_tests_use_active_environment_without_ambient_pythonpath(rig, monkeypatch):
    root, _, _, _ = rig
    engine.bootstrap(root)
    monkeypatch.setenv("PYTHONPATH", "/some/other/source/tree")
    calls = []
    original = engine.run

    def record(args, **kwargs):
        if args[1:3] == ["-m", "pytest"]:
            calls.append(kwargs["env"])
            return ""
        return original(args, **kwargs)

    monkeypatch.setattr(engine, "run", record)
    engine.run_tests(root, ["repos/autospan/tests"])
    active = Path(engine.get_state(root)["env"])
    assert calls[0]["VIRTUAL_ENV"] == str(active)
    assert calls[0]["PATH"].startswith(str(engine.python_path(active).parent))
    assert "PYTHONPATH" not in calls[0]


def test_exec_resolves_command_in_active_environment(rig, monkeypatch):
    root, _, _, _ = rig
    engine.bootstrap(root)
    active = Path(engine.get_state(root)["env"])
    expected = str(engine.python_path(active))
    lookups = []
    calls = []

    def which(command, *, path=None):
        lookups.append((command, path))
        return expected

    monkeypatch.setattr(engine.shutil, "which", which)
    monkeypatch.setattr(engine, "active_environment", lambda _root: active)
    monkeypatch.setattr(engine, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    engine.run_command(root, ["python", "-c", "print('active')"])
    assert lookups == [("python", engine.execution_environment(active)["PATH"])]
    assert calls[0][0] == [expected, "-c", "print('active')"]
    assert calls[0][1]["env"]["VIRTUAL_ENV"] == str(active)


def test_operation_lock_rejects_symlink_without_modifying_target(tmp_path):
    target = tmp_path / "valuable"
    target.touch()
    (tmp_path / ".generated").mkdir()
    (tmp_path / ".generated/operation.lock").symlink_to(target)
    with pytest.raises(catalog.WorkspaceError, match="symlink"):
        with engine.operation_lock(tmp_path):
            pytest.fail("unsafe lock entered")
    assert target.read_bytes() == b""


def test_interrupt_waits_for_delegated_child_cleanup(tmp_path):
    import os
    import signal
    import sys
    import time

    if os.name != "posix":
        pytest.skip("Process-group SIGINT regression requires POSIX")
    child = (
        "from pathlib import Path; import sys,time\n"
        "root=Path(sys.argv[1])\n"
        "(root/'ready').touch()\n"
        "try: time.sleep(60)\n"
        "finally:\n"
        " time.sleep(0.8)\n"
        " (root/'closed').touch()\n"
    )
    executable = engine.python_path(tmp_path / ".tools")
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ws.py").write_text(
        "from pathlib import Path\nimport sys\nfrom aworkspace import engine\n"
        f"root=Path({str(tmp_path)!r})\n"
        "try:\n"
        f" engine.run([sys.executable,'-c',{child!r},str(root)],cwd=root)\n"
        "except KeyboardInterrupt: raise SystemExit(130)\n"
    )
    original = Path(__file__).resolve().parents[1]
    code = (
        "from pathlib import Path; import sys; from aworkspace import cli; "
        "cli.ROOT=Path(sys.argv[1]); cli.main(['status'])"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=original,
        env={**os.environ, "PYTHONPATH": str(original)},
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "ready").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline, "Child did not start"
            time.sleep(0.02)
        os.killpg(process.pid, signal.SIGINT)
        assert process.wait(timeout=5) == 130
        assert (tmp_path / "closed").is_file()
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
