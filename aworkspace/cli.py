"""Commands for configuring, preparing and using a development workspace."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import catalog, engine, plugins

ROOT = Path(__file__).resolve().parent.parent

MAKE_HELP = """AutoSpan Workspace

Plugin workflow (all managed Git repositories stay inside repos/)
  cp repos.example.yaml repos.local.yaml
  make plugins                      List plugin projects
  make select PLUGIN=NAME PROFILE=NAME  Save a plugin and optional profile
  make deselect                     Use global editable component selection
  make bootstrap PLUGIN=NAME        Prepare pinned plugin sources and environment
  make doctor PLUGIN=NAME           Validate plugin installation
  make test PLUGIN=NAME             Run that plugin's own tests
  make hw-list                      Collect the saved hardware profile (no acquisition)
  make hw-list TEST=case_name       Collect one selected case
  make hw                           Run the saved hardware profile
  make hw TEST=case_name            Select one case, including unmarked scenarios
  make console PLUGIN=NAME          Open console using plugin-owned bench config
  make exec PLUGIN=NAME COMMAND="autospan plugins"
  make sync PLUGIN=NAME             Fetch and prepare catalog revisions
  make install PLUGIN=NAME          Prepare existing pinned sources (no Git fetch)
  make freeze PLUGIN=NAME           Write audit snapshot (not a lockfile)

Without PLUGIN=, commands use the locally selected plugin, if one exists.
Explicit PLUGIN= always overrides the local default. CI should be explicit.
The canonical cross-platform interface is python scripts/ws.py ... --plugin NAME.

Editable development (global Kconfig selection)
  make tools
  make menuconfig
  make config
  make bootstrap                    With no plugin selected: global selection
  make test                         With no plugin selected: global suites

Workspace maintenance
  make test-workspace               Run the workspace manager tests
  make format                       Format workspace Python code with Ruff
  make lint                         Check workspace code style and syntax
  make pre-commit                   Run all pre-commit hooks on tracked files
  make pre-commit-install           Install the Git pre-commit hook

Options
  PLUGIN=NAME                       Plugin name or short alias from the catalog
  CONFIG=path/to/bench.yaml         Bench configuration for test or console
  PROFILE=NAME                      Save with select; override for test/hw/console
  CONSOLE_ARGS="--plain"            Additional AutoSpan console arguments
  TEST_PATH=path                    Checkout test path or hosted scenario selector
  TEST="gatt and forward"           Pytest -k name/expression filter
  PYTEST_ARGS="-q -k smoke"         Extra pytest options
  REPORT=artifacts/report.html      Self-contained HTML report for make test/hw
  COMMAND="..."                     Command for make exec

Development checkouts live in repos/<component>/.
Pinned per-plugin worktrees live in repos/.isolated/<plugin>/.
Per-plugin environments live in .envs/<plugin>/.
Hardware shortcuts reuse a selected prepared profile and its bench.
Without one, supply CONFIG=path/to/bench.yaml explicitly.
Hosted runs require installed scenario packages enabled by the bench configuration.
They enable --hardware only. Firmware maintenance needs separate authorization.
"""


def menuconfig(root: Path) -> None:
    doc = catalog.read_catalog(root / "repos.local.yaml", root)
    catalog.write_generated_menu(root, doc)
    try:
        import kconfiglib
        import menuconfig as menu_module
    except ImportError as exc:
        raise catalog.WorkspaceError("Install Kconfiglib using `make tools`") from exc
    previous = Path.cwd()
    try:
        os.chdir(root)
        menu_module.menuconfig(kconfiglib.Kconfig("Kconfig"))
    finally:
        os.chdir(previous)


def _delegate_to_tools(command: str, args: list[str]) -> None:
    """A venv may symlink its interpreter: compare prefixes, not resolved binaries."""
    if command in {"tools", "help"}:
        return
    executable = engine.python_path(ROOT / ".tools")
    if executable.exists() and Path(sys.prefix).resolve() != (ROOT / ".tools").resolve():
        try:
            engine.run([str(executable), str(ROOT / "scripts/ws.py"), *args], cwd=ROOT)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(exc.returncode) from exc
        raise SystemExit(0)


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for cmd in (
        "help",
        "tools",
        "menuconfig",
        "config",
        "bootstrap",
        "sync",
        "install",
        "doctor",
        "status",
        "freeze",
        "plugins",
        "select",
        "deselect",
    ):
        sub = commands.add_parser(cmd)
        if cmd in {"bootstrap", "sync", "install", "doctor", "status", "freeze", "select"}:
            sub.add_argument("--plugin", default=None)
        if cmd == "select":
            sub.add_argument("--profile", default=None)
    commands.add_parser("preset").add_argument("profile", choices=["minimal", "ble"])
    test = commands.add_parser("test", help="Run selected suites; pytest options follow --")
    test.add_argument("--plugin", default=None)
    test.add_argument("--profile", default=None)
    test.add_argument("--config", default=None)
    test.add_argument("paths", nargs=argparse.REMAINDER)
    execute = commands.add_parser("exec", help="Run a command in the active environment")
    execute.add_argument("--plugin", default=None)
    execute.add_argument("arguments", nargs=argparse.REMAINDER)
    console = commands.add_parser(
        "console", help="Open AutoSpan's console; additional options follow --"
    )
    console.add_argument("--plugin", default=None)
    console.add_argument("--profile", default=None)
    console.add_argument("--config", default=None)
    console.add_argument("arguments", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(args)
    try:
        _delegate_to_tools(parsed.command, args)
        if parsed.command == "help":
            print(MAKE_HELP, end="")
        elif parsed.command == "tools":
            engine.tools(ROOT)
        elif parsed.command == "plugins":
            plugins.show_plugins(ROOT)
        elif parsed.command == "select":
            if parsed.profile is None:
                plugins.select_plugin(ROOT, parsed.plugin)
            else:
                plugins.select_plugin(ROOT, parsed.plugin, profile=parsed.profile)
        elif parsed.command == "deselect":
            plugins.deselect_plugin(ROOT)
        elif parsed.command == "menuconfig":
            with engine.operation_lock(ROOT):
                menuconfig(ROOT)
        elif parsed.command == "config":
            with engine.operation_lock(ROOT):
                engine.load_selection(ROOT, persist=True)
            print("Wrote .config")
        elif parsed.command == "preset":
            source = ROOT / "configs" / (parsed.profile + ".config")
            if not source.is_file():
                raise catalog.WorkspaceError(f"Preset missing: {source}")
            with engine.operation_lock(ROOT):
                engine.load_selection(ROOT, persist=True, config_path=source)
            print(f"Loaded {parsed.profile} preset")
        else:
            plugin = parsed.plugin or plugins.selected_plugin(ROOT)
            if plugin:
                if parsed.command in ("bootstrap", "sync"):
                    plugins.bootstrap(ROOT, plugin)
                elif parsed.command == "install":
                    plugins.install(ROOT, plugin)
                elif parsed.command == "doctor":
                    if not plugins.doctor(ROOT, plugin):
                        raise SystemExit(1)
                elif parsed.command == "status":
                    plugins.status(ROOT, plugin)
                elif parsed.command == "freeze":
                    plugins.freeze(ROOT, plugin)
                elif parsed.command == "test":
                    paths, options = _split_args(parsed.paths)
                    plugins.test(
                        ROOT, plugin, paths, options, profile=parsed.profile, config=parsed.config
                    )
                elif parsed.command == "exec":
                    plugins.exec_command(ROOT, plugin, _strip_separator(parsed.arguments))
                elif parsed.command == "console":
                    plugins.console(
                        ROOT,
                        plugin,
                        parsed.config,
                        _strip_separator(parsed.arguments),
                        profile=parsed.profile,
                    )
            elif parsed.command in ("bootstrap", "sync"):
                engine.bootstrap(ROOT)
            elif parsed.command == "install":
                engine.install(ROOT)
            elif parsed.command == "doctor":
                if not engine.doctor(ROOT):
                    raise SystemExit(1)
            elif parsed.command == "status":
                engine.status(ROOT)
            elif parsed.command == "freeze":
                with engine.operation_lock(ROOT):
                    engine.freeze(ROOT)
            elif parsed.command == "test":
                paths, options = _split_args(parsed.paths)
                if parsed.profile is not None:
                    raise catalog.WorkspaceError("--profile requires a selected plugin")
                if "--hardware" in options and parsed.config is None:
                    raise catalog.WorkspaceError(
                        "Missing hardware bench configuration: select a prepared hardware profile "
                        "or supply CONFIG=bench.local.yaml"
                    )
                if parsed.config is not None:
                    engine.run_tests(ROOT, paths, options, config=parsed.config)
                else:
                    engine.run_tests(ROOT, paths, options)
            elif parsed.command == "exec":
                engine.run_command(ROOT, _strip_separator(parsed.arguments))
            elif parsed.command == "console":
                if parsed.profile is not None:
                    raise catalog.WorkspaceError("--profile requires a selected plugin")
                if parsed.config is None:
                    raise catalog.WorkspaceError(
                        "No plugin selected: pass --config PATH for the AutoSpan console"
                    )
                engine.run_command(
                    ROOT,
                    [
                        "python",
                        "-m",
                        "autospan",
                        "console",
                        "--config",
                        parsed.config,
                        *_strip_separator(parsed.arguments),
                    ],
                )
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (catalog.WorkspaceError, subprocess.CalledProcessError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _strip_separator(arguments: list[str]) -> list[str]:
    return arguments[1:] if arguments[:1] == ["--"] else arguments


def _split_args(arguments: list[str]) -> tuple[list[str], list[str]]:
    if "--" in arguments:
        index = arguments.index("--")
        return arguments[:index], arguments[index + 1 :]
    return arguments, []
