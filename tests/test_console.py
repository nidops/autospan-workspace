"""Console shortcuts preserve AutoSpan options and the active interpreter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aworkspace import cli


@pytest.mark.parametrize(
    ("arguments", "forwarded"),
    [
        (
            [
                "console",
                "--config",
                "bench config.local.yaml",
                "--",
                "--hardware",
                "--plain",
                "--exec",
                "ble status",
            ],
            [
                "--config",
                "bench config.local.yaml",
                "--hardware",
                "--plain",
                "--exec",
                "ble status",
            ],
        ),
    ],
)
def test_console_runs_autospan_as_an_active_environment_module(
    tmp_path, monkeypatch, arguments, forwarded
):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(cli.engine, "run_command", lambda root, args: calls.append((root, args)))
    cli.main(arguments)
    assert calls == [(tmp_path, ["python", "-m", "autospan", "console", *forwarded])]


def test_global_console_without_config_fails_before_dispatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(cli.engine, "run_command", lambda *args: calls.append(args))
    with pytest.raises(SystemExit) as exc:
        cli.main(["console"])
    assert exc.value.code == 1
    assert not calls
    assert "No plugin selected: pass --config PATH" in capsys.readouterr().err


def test_cli_forwards_bench_config_in_editable_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(
        cli.engine, "run_tests", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    cli.main(["test", "--config", "bench.local.yaml", "--", "--hardware", "-k", "gatt"])
    assert calls == [((tmp_path, [], ["--hardware", "-k", "gatt"]), {"config": "bench.local.yaml"})]


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("make") is None,
    reason="Makefile argument regression requires a POSIX shell and GNU Make",
)
def test_make_console_keeps_config_paths_and_command_arguments_intact(tmp_path):
    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Makefile").write_bytes((original / "Makefile").read_bytes())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ws.py").write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "console",
            f"PYTHON={sys.executable}",
            "CONFIG=bench config.local.yaml",
            "CONSOLE_ARGS=--hardware --plain --exec 'ble status'",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == [
        "console",
        "--config",
        "bench config.local.yaml",
        "--",
        "--hardware",
        "--plain",
        "--exec",
        "ble status",
    ]


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("make") is None,
    reason="Makefile argument regression requires a POSIX shell and GNU Make",
)
@pytest.mark.parametrize("target", ["hw", "hw-list"])
def test_make_hardware_keeps_filter_and_bench_arguments_intact(tmp_path, target):
    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Makefile").write_bytes((original / "Makefile").read_bytes())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ws.py").write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
    for override in (
        ["CONFIG=bench config.local.yaml"],
        ["CONFIG=bench config.local.yaml", "PLUGIN=ble"],
        ["PROFILE=lab"],
        ["PROFILE=lab", "PLUGIN=ble"],
        ["CONFIG=bench config.local.yaml", "PROFILE=lab", "PLUGIN=ble"],
    ):
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                target,
                f"PYTHON={sys.executable}",
                "TEST=gatt and forward",
                "PYTEST_ARGS=-x",
                *override,
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        args = json.loads(result.stdout)
        assert args[:1] == ["test"]
        if "CONFIG=bench config.local.yaml" in override:
            assert args[args.index("--config") + 1] == "bench config.local.yaml"
        else:
            assert "--config" not in args
        if "PROFILE=lab" in override:
            assert args[args.index("--profile") + 1] == "lab"
        else:
            assert "--profile" not in args
        assert ("--plugin" in args) == ("PLUGIN=ble" in override)
        assert args[args.index("--") + 1 :] == [
            "--hardware",
            "-v",
            "-k",
            "gatt and forward",
            *(
                ["--collect-only"]
                if target == "hw-list"
                else [
                    "-p",
                    "metadata",
                    "-p",
                    "no:html",
                    "-p",
                    "pytest_html.plugin",
                    "--html=artifacts/report.html",
                    "--self-contained-html",
                ]
            ),
            "-x",
        ]


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("make") is None,
    reason="GNU Make check requires POSIX",
)
@pytest.mark.parametrize("target", ["hw", "hw-list"])
def test_make_hardware_does_not_require_explicit_config_at_make_layer(tmp_path, target):
    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Makefile").write_bytes((original / "Makefile").read_bytes())
    result = subprocess.run(
        ["make", "--no-print-directory", "-n", target],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert "--hardware" in result.stdout
    assert "-m hardware" not in result.stdout
