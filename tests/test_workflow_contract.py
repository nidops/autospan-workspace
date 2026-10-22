"""Profile persistence, hardware shortcuts and scenario metadata contracts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aworkspace import catalog, engine, plugins


def _catalog(root: Path) -> None:
    (root / "repos.local.yaml").write_text(
        "schema: 1\nrepositories:\n"
        "  demo-plugin:\n"
        "    plugin: true\n"
        "    url: /tmp/demo-plugin\n"
        "    revision: main\n"
        "    python: {distribution: demo-plugin}\n"
    )


def test_selected_profile_persists_and_can_be_cleared(tmp_path):
    _catalog(tmp_path)
    plugins.select_plugin(tmp_path, "demo-plugin", profile="lab")
    assert plugins.selected_plugin(tmp_path) == "demo-plugin"
    assert plugins.selected_profile(tmp_path, "demo-plugin") == "lab"
    data = json.loads((tmp_path / ".generated/active-plugin.json").read_text())
    assert data == {"plugin": "demo-plugin", "profile": "lab", "schema": 1}
    plugins.deselect_plugin(tmp_path)
    assert plugins.selected_plugin(tmp_path) is None


def test_corrupt_saved_profile_is_actionable(tmp_path):
    _catalog(tmp_path)
    plugins.select_plugin(tmp_path, "demo-plugin")
    state = tmp_path / ".generated/active-plugin.json"
    state.write_text(json.dumps({"schema": 1, "plugin": "demo-plugin", "profile": "../../bad"}))
    with pytest.raises(catalog.WorkspaceError, match="Invalid saved plugin profile"):
        plugins.selected_profile(tmp_path, "demo-plugin")


def test_scenario_distribution_metadata_is_validated(tmp_path):
    package = tmp_path / "plugin"
    (package / "tests").mkdir(parents=True)
    (package / "tests/pyproject.toml").write_text(
        '[project]\nname = "demo-acceptance"\nversion = "0.1.0"\n'
    )
    (package / "autospan-workspace.yaml").write_text(
        "schema: 1\nprofiles:\n"
        "  lab:\n    hardware: true\n"
        "    scenario_packages: [tests]\n"
        "    plugins: [demo, demo-acceptance]\n"
        "    console: {config: bench.local.yaml}\n"
    )
    manifest = plugins._load_manifest(package)
    assert manifest["profiles"]["lab"]["scenario_packages"] == ["tests"]
    (package / "tests/pyproject.toml").unlink()
    with pytest.raises(catalog.WorkspaceError, match="missing scenario package"):
        plugins._load_manifest(package)


@pytest.mark.skipif(os.name != "posix" or not shutil.which("make"), reason="GNU Make required")
@pytest.mark.parametrize("target", ["hw", "hw-list"])
def test_hardware_make_preserves_explicit_scenario_selection(tmp_path, target):
    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Makefile").write_bytes((original / "Makefile").read_bytes())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ws.py").write_text("import json,sys; print(json.dumps(sys.argv[1:]))\n")
    result = subprocess.run(
        ["make", "--no-print-directory", target, f"PYTHON={sys.executable}", "TEST=ordinary_case"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    args = json.loads(result.stdout)
    assert "--hardware" in args
    assert "-m" not in args
    assert args[args.index("-k") + 1] == "ordinary_case"
    assert ("--collect-only" in args) is (target == "hw-list")
    assert "--config" not in args


def test_global_hardware_without_config_fails_before_execution(tmp_path, monkeypatch, capsys):
    from aworkspace import cli

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(cli.engine, "run_tests", lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["test", "--", "--hardware", "--collect-only"])
    assert stopped.value.code == 1
    assert not calls
    assert "Missing hardware bench configuration" in capsys.readouterr().err


def test_missing_profile_bench_is_actionable(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    with pytest.raises(catalog.WorkspaceError, match="Missing hardware bench configuration"):
        plugins.config_path(tmp_path, "demo-plugin", plugin, {}, {}, None, require_explicit=True)


def test_report_output_has_permanent_uri_and_no_temporary_link(tmp_path, capsys):
    from aworkspace import engine

    script = tmp_path / "fake_pytest.py"
    script.write_text(
        "import pathlib,sys\n"
        "path=pathlib.Path([a for a in sys.argv[1:] if a.startswith('--html=')][-1].split('=',1)[1])\n"
        "path.write_text('<html>complete</html>')\n"
        "print('HTML report: ' + str(path))\n"
    )
    final = tmp_path / "final.html"
    engine.run_pytest(
        [sys.executable, str(script), "--html=final.html"],
        cwd=tmp_path,
        env=os.environ.copy(),
    )
    assert final.read_text() == "<html>complete</html>"
    out = capsys.readouterr().out
    assert final.as_uri() in out
    assert ".report-" not in out
    assert "[report pending finalization]" in out


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    """Exercise profile activation and inspection with only the source transport stubbed."""
    import yaml

    _catalog(tmp_path)
    (tmp_path / engine.TOOLS_REQUIREMENTS).write_text("fixture requirements\n")
    source = plugins.plugin_sources(plugins.read_catalog(tmp_path), "demo-plugin")
    heads = {"demo-plugin": "a" * 40}
    checkout = plugins._worktree_path(tmp_path, "demo-plugin", "demo-plugin", "a" * 40)
    (checkout / "tests").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text('[project]\nname="demo-plugin"\n')
    (checkout / "tests/pyproject.toml").write_text('[project]\nname="demo-acceptance"\n')
    (checkout / "autospan.fake.yaml").write_text("fake: true\n")
    lab = {
        "hardware": True,
        "scenario_packages": ["tests"],
        "plugins": ["demo", "demo-acceptance"],
        "console": {"config": "bench.local.yaml"},
    }
    (checkout / "autospan-workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": 1,
                "profiles": {
                    "lab": lab,
                    "other-lab": lab,
                    "fake": {"console": {"config": "autospan.fake.yaml"}},
                },
            }
        )
    )
    installed = []

    def environment(root, sources, flags, pinned, **kwargs):
        assert kwargs["checkouts"] == {"demo-plugin": checkout}
        assert kwargs["extra_packages"] == [checkout / "tests"]
        key = engine.env_fingerprint(
            sources,
            pinned,
            flags,
            root,
            checkouts=kwargs["checkouts"],
            extra_packages=kwargs["extra_packages"],
        )
        env = kwargs["base"] / key
        env.mkdir(parents=True, exist_ok=True)
        (env / ".ready").write_text(key)
        return key, env

    monkeypatch.setattr(engine, "environment", environment)
    monkeypatch.setattr(engine, "check_installed", lambda env, names: installed.append(names))
    monkeypatch.setattr(engine, "check_plugins", lambda *args: None)
    # Keep real _current / state-path validation; bypass only Git worktree inspection.
    monkeypatch.setattr(plugins, "_verify_worktree", lambda *args: None)
    plugins.select_plugin(tmp_path, "demo-plugin", profile="lab")
    plugins._activate(tmp_path, "demo-plugin", source, heads, {"demo-plugin": checkout})
    return tmp_path, checkout, installed


def test_activation_records_nested_distribution_and_is_ready(prepared):
    root, _, installed = prepared
    state = plugins._read_state(root, "demo-plugin")
    assert state["profile"] == "lab"
    assert state["scenario_packages"] == ["tests"]
    assert state["distributions"] == ["demo-plugin", "demo-acceptance"]
    assert plugins.inspect(root, "demo-plugin") == []
    assert installed[-1] == state["distributions"]


@pytest.mark.parametrize("metadata", ["pyproject.toml", "setup.cfg", "setup.py"])
def test_modified_scenario_metadata_invalidates_prepared_environment(prepared, metadata):
    root, checkout, _ = prepared
    path = checkout / "tests" / metadata
    path.write_text((path.read_text() if path.exists() else "") + "\n# package changed\n")
    assert "fingerprint changed" in plugins.inspect(root, "demo-plugin")[0]


def test_saved_profile_change_requires_preparation_but_explicit_override_works(
    prepared, monkeypatch
):
    root, _, _ = prepared
    (root / "bench.local.yaml").write_text("configured bench\n")
    plugins.select_plugin(root, "demo-plugin", profile="fake")
    assert "Saved profile changed" in plugins.inspect(root, "demo-plugin")[0]
    calls = []
    monkeypatch.setattr(engine, "run_hosted_tests", lambda args, **kw: calls.append(args))
    plugins.test(root, "demo-plugin", [], ["--hardware", "--co"], profile="lab")
    assert len(calls) == 1
    assert plugins.selected_profile(root, "demo-plugin") == "fake"


@pytest.mark.parametrize("command", ["test", "console"])
def test_profile_override_cannot_use_unprepared_scenario_packages(prepared, monkeypatch, command):
    root, _, _ = prepared
    monkeypatch.setattr(engine, "run", lambda *a, **kw: pytest.fail("dispatched"))
    with pytest.raises(catalog.WorkspaceError, match="different scenario packages"):
        if command == "test":
            plugins.test(root, "demo-plugin", [], [], profile="fake")
        else:
            plugins.console(root, "demo-plugin", None, [], profile="fake")


def test_failed_profile_preparation_preserves_previous_state(prepared, monkeypatch):
    root, checkout, _ = prepared
    before = plugins.state_path(root, "demo-plugin").read_bytes()
    sources = plugins.plugin_sources(plugins.read_catalog(root), "demo-plugin")
    monkeypatch.setattr(
        engine,
        "check_plugins",
        lambda *a: (_ for _ in ()).throw(catalog.WorkspaceError("missing entry point")),
    )
    with pytest.raises(catalog.WorkspaceError, match="missing entry point"):
        plugins._activate(
            root, "demo-plugin", sources, {"demo-plugin": "a" * 40}, {"demo-plugin": checkout}
        )
    assert plugins.state_path(root, "demo-plugin").read_bytes() == before


@pytest.mark.parametrize("command", ["test", "console"])
def test_unknown_profile_is_actionable(prepared, command):
    root, _, _ = prepared
    with pytest.raises(catalog.WorkspaceError, match="Unknown plugin profile"):
        if command == "test":
            plugins.test(root, "demo-plugin", [], [], profile="absent")
        else:
            plugins.console(root, "demo-plugin", None, [], profile="absent")


def test_profile_does_not_follow_another_plugin_override(tmp_path):
    import yaml

    _catalog(tmp_path)
    path = tmp_path / "repos.local.yaml"
    raw = yaml.safe_load(path.read_text())
    raw["repositories"]["other"] = {"plugin": True, "url": "/tmp/other", "revision": "main"}
    path.write_text(yaml.safe_dump(raw))
    plugins.select_plugin(tmp_path, "demo-plugin", profile="lab")
    assert plugins.selected_profile(tmp_path, "other") is None
    plugins.select_plugin(tmp_path, "other")
    assert plugins.selected_profile(tmp_path, "other") is None
    plugins.select_plugin(tmp_path, "demo-plugin")
    assert plugins.selected_profile(tmp_path, "demo-plugin") is None


@pytest.mark.parametrize("collect", [False, True])
def test_saved_profile_collects_exact_execution_selection(prepared, monkeypatch, collect):
    root, checkout, _ = prepared
    bench = root / "private.local.yaml"
    bench.write_text("configured bench\n")
    calls = []
    monkeypatch.setattr(engine, "run_hosted_tests", lambda args, **kw: calls.append((args, kw)))
    options = ["--hardware", "-k", "ordinary_case", *(["--collect-only"] if collect else [])]
    plugins.test(root, "demo-plugin", ["ordinary.py"], options, config=str(bench))
    assert len(calls) == (1 if collect else 2)
    command, kwargs = calls[-1]
    assert command[command.index("--") + 1 :] == ["ordinary.py", *options]
    assert json.loads(command[command.index("--set") + 1].split("=", 1)[1]) == [
        "demo",
        "demo-acceptance",
    ]
    assert command[command.index("--config") + 1] == str(bench)
    assert kwargs["cwd"] == checkout
    if not collect:
        assert calls[0][0] == [*command, "--collect-only"]
    assert "--maintenance" not in command and "--destructive" not in command


def test_collection_failure_prevents_hardware_execution(prepared, monkeypatch):
    root, _, _ = prepared
    (root / "bench.local.yaml").write_text("configured bench\n")
    calls = []

    def fail(args, **kw):
        calls.append(args)
        raise subprocess.CalledProcessError(5, args)

    monkeypatch.setattr(engine, "run_hosted_tests", fail)
    with pytest.raises(subprocess.CalledProcessError):
        plugins.test(root, "demo-plugin", [], ["--hardware"])
    assert len(calls) == 1 and calls[0][-1] == "--collect-only"


@pytest.mark.parametrize("collect", [False, True])
def test_missing_bench_prevents_test_dispatch(prepared, monkeypatch, collect):
    root, _, _ = prepared
    monkeypatch.setattr(engine, "run_hosted_tests", lambda *a, **kw: pytest.fail("dispatched"))
    with pytest.raises(catalog.WorkspaceError, match="Bench configuration missing"):
        plugins.test(root, "demo-plugin", [], ["--hardware", *(["--co"] if collect else [])])


@pytest.mark.parametrize("command", ["test", "console"])
def test_fake_profile_cannot_supply_implicit_hardware_bench(prepared, monkeypatch, command):
    root, checkout, _ = prepared
    manifest = plugins._load_manifest(checkout)
    monkeypatch.setattr(plugins, "active", lambda *a, **kw: (root / "env", checkout, manifest))
    monkeypatch.setattr(plugins, "_read_state", lambda *a: {"scenario_packages": []})
    with pytest.raises(catalog.WorkspaceError, match="hardware profile"):
        if command == "test":
            plugins.test(root, "demo-plugin", [], ["--hardware", "--co"], profile="fake")
        else:
            plugins.console(root, "demo-plugin", None, ["--hardware"], profile="fake")


def test_console_uses_saved_plugins_and_hardware_gate(prepared, monkeypatch):
    root, _, _ = prepared
    bench = root / "bench.local.yaml"
    bench.write_text("configured bench\n")
    calls = []
    monkeypatch.setattr(engine, "run", lambda args, **kw: calls.append(args))
    plugins.console(root, "demo-plugin", None, ["--plain"])
    command = calls[-1]
    assert command[command.index("--config") + 1] == str(bench)
    assert command[-2:] == ["--hardware", "--plain"]
    assert "demo-acceptance" in command[command.index("--set") + 1]
    assert "--maintenance" not in command and "--destructive" not in command


@pytest.mark.parametrize("exitcode", [0, 1])
def test_report_hides_uri_and_relative_temporary_names_on_failure(tmp_path, capsys, exitcode):
    directory = tmp_path / "with spaces"
    directory.mkdir()
    script = (
        "from pathlib import Path; import sys; "
        "p=Path(sys.argv[-1].split('=',1)[1]); p.write_text('<html>done</html>'); "
        "print(p.as_uri()); print(p.name); sys.exit(" + str(exitcode) + ")"
    )
    args = [sys.executable, "-c", script, "--html=final.html"]
    if exitcode:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            engine.run_pytest(args, cwd=directory, env=os.environ.copy())
        assert ".report-" not in str(exc.value)
    else:
        engine.run_pytest(args, cwd=directory, env=os.environ.copy())
    captured = capsys.readouterr()
    assert ".report-" not in captured.out + captured.err
    assert (directory / "final.html").as_uri() in captured.out


@pytest.mark.skipif(os.name != "posix", reason="Process-group signals require POSIX")
@pytest.mark.parametrize("second_interrupt", [False, True])
def test_report_interrupt_drains_cleanup_and_second_interrupt_stops_child(
    tmp_path, second_interrupt
):
    import signal
    import time

    child = (
        "from pathlib import Path; import sys,time\n"
        "root=Path.cwd()\n"
        "(root/'ready').touch()\n"
        "try: time.sleep(60)\n"
        "finally:\n"
        " (root/'cleaning').touch()\n"
        + (
            " time.sleep(60)\n"
            if second_interrupt
            else " print('cleanup log\\n'*100000,flush=True)\n"
        )
        + " Path(sys.argv[-1].split('=',1)[1]).write_text('<html>interrupted</html>')\n"
        " (root/'closed').touch()\n"
    )
    code = (
        "import os,sys; from pathlib import Path; from aworkspace import engine\n"
        "try:\n"
        f" engine.run_pytest([sys.executable,'-c',{child!r},'--html=final.html'],"
        "cwd=Path.cwd(),env=os.environ.copy())\n"
        "except KeyboardInterrupt: raise SystemExit(130)\n"
    )
    original = Path(__file__).resolve().parents[1]
    with (tmp_path / "output.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(original)},
            start_new_session=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

        def wait_for(name):
            deadline = time.monotonic() + 5
            while not (tmp_path / name).exists():
                assert process.poll() is None
                assert time.monotonic() < deadline, f"No {name} marker"
                time.sleep(0.01)

        try:
            wait_for("ready")
            os.killpg(process.pid, signal.SIGINT)
            if second_interrupt:
                wait_for("cleaning")
                os.kill(process.pid, signal.SIGINT)
            assert process.wait(timeout=10) == 130
            assert (tmp_path / "closed").exists() is (not second_interrupt)
            assert (tmp_path / "final.html").is_file()
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    output = (tmp_path / "output.log").read_text()
    assert (tmp_path / "final.html").as_uri() in output
    assert ".report-" not in output


@pytest.mark.parametrize("metadata", ["[project", "project=[]", '[project]\nname="bad/name"'])
def test_invalid_scenario_metadata_is_actionable(tmp_path, metadata):
    (tmp_path / "pyproject.toml").write_text(metadata)
    with pytest.raises(catalog.WorkspaceError, match="[Ss]cenario package"):
        engine.package_name(tmp_path)


def test_stale_scenario_environment_is_replaced_without_deleting_previous(tmp_path, monkeypatch):
    (tmp_path / engine.TOOLS_REQUIREMENTS).write_text("requirements\n")
    package = tmp_path / "scenarios"
    package.mkdir()
    (package / "pyproject.toml").write_text('[project]\nname="demo-acceptance"\n')
    flags = {"AUTOSPAN_EDITABLE": False}
    key = engine.env_fingerprint([], {}, flags, tmp_path, extra_packages=[package])
    old = tmp_path / ".envs" / key
    engine.python_path(old).parent.mkdir(parents=True)
    engine.python_path(old).touch()
    (old / ".ready").write_text(key)

    def check(env, names):
        assert names == ["demo-acceptance"]
        if env == old:
            raise catalog.WorkspaceError("missing acceptance distribution")

    monkeypatch.setattr(engine, "check_installed", check)
    monkeypatch.setattr(engine.venv.EnvBuilder, "create", lambda *a: None)
    calls = []
    monkeypatch.setattr(engine, "run", lambda args, **kw: calls.append(args))
    actual_key, fresh = engine.environment(tmp_path, [], flags, {}, extra_packages=[package])
    assert actual_key == key and fresh != old
    assert calls[0][-1] == str(package)
    assert "-e" not in calls[0]
    assert (old / ".ready").read_text() == key
    assert (fresh / ".ready").read_text() == key


def test_interactive_report_restores_pytest_colors(tmp_path, monkeypatch):
    from aworkspace import engine

    class Terminal:
        def isatty(self):
            return True

        def write(self, text):
            return len(text)

        def flush(self):
            pass

    commands = []

    def run_child(args, *, cwd, env, hidden):
        commands.append(args)
        Path(hidden).write_text("<html>complete</html>")

    monkeypatch.setattr(engine.sys, "stdout", Terminal())
    monkeypatch.setattr(engine, "_run_report_child", run_child)
    monkeypatch.delenv("NO_COLOR", raising=False)

    env = os.environ.copy()
    env.pop("PY_COLORS", None)

    engine.run_pytest(
        ["python", "-m", "pytest", "--html=report.html"],
        cwd=tmp_path,
        env=env,
    )

    assert "--color=yes" in commands[0]
    assert (tmp_path / "report.html").is_file()
