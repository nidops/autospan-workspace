"""Opt-in integration against the BLE revision resolved from the example catalog."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from aworkspace import engine, plugins
from scripts.check_links import check


def test_reference_revision_is_documented_as_moving():
    root = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((root / "repos.example.yaml").read_text())
    reference = doc["repositories"]["autospan-ble"]
    assert reference["revision"] == "master"
    assert reference["default"] is False
    for name in ("README.md", "docs/GETTING_STARTED.md"):
        text = (root / name).read_text()
        assert "autospan-ble/blob/master/docs/" in text
        assert "becbafcfde49070e202f6caa6d0eda5a7e9b47b2" not in text
    assert "not reproducible" in (root / "repos.example.yaml").read_text()


@pytest.mark.skipif(
    os.environ.get("AUTO_WORKSPACE_BLE_E2E") != "1",
    reason="Reference BLE Git/pip integration requires separate opt-in and repository access",
)
def test_reference_ble_workflow(tmp_path):
    original = Path(__file__).resolve().parents[1]
    root = tmp_path / "workspace"
    root.mkdir()
    for name in ("aworkspace", "scripts", "docs"):
        shutil.copytree(original / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in (
        "README.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "LICENSE",
        "Makefile",
        "Kconfig",
        "requirements-tools.txt",
    ):
        shutil.copy2(original / name, root / name)
    doc = yaml.safe_load((original / "repos.example.yaml").read_text())
    reference = doc["repositories"]["autospan-ble"]
    # An authenticated checkout pins the E2E run to the exact checked-out SHA,
    # even though the public example deliberately tracks the moving master ref.
    expected_sha = None
    if source := os.environ.get("AUTO_WORKSPACE_BLE_SOURCE"):
        path = Path(source).resolve()
        expected_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=path, text=True
        ).strip()
        assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=path, text=True)
        reference["url"] = str(path)
        reference["revision"] = expected_sha
    (root / "repos.local.yaml").write_text(yaml.safe_dump(doc))
    assert plugins.selected_plugin(root) is None
    plugins.bootstrap(root, "ble")
    assert plugins.doctor(root, "ble")
    state = plugins._read_state(root, "autospan-ble")
    assert set(state["heads"]) == {"autospan-ble"}
    if expected_sha is not None:
        assert state["heads"]["autospan-ble"] == expected_sha
    assert state["selected"] == ["autospan-ble"]
    env, checkout, _ = plugins.active(root, "ble")
    python = str(engine.python_path(env))
    probe = (
        "from importlib.metadata import entry_points, distribution, version; "
        "names = {ep.name for ep in entry_points(group='autospan.plugins')}; "
        "assert 'ble' in names and 'ble-acceptance' not in names; "
        "assert all(version(n).split('.')[0] == '1' for n in ('autospan', 'structwire')); "
        "assert all(distribution(n).read_text('direct_url.json') is None "
        "for n in ('autospan', 'structwire'))"
    )
    subprocess.run([python, "-I", "-c", probe], cwd=tmp_path, check=True)
    if shutil.which("make") and os.name == "posix":
        subprocess.run(
            ["make", "test", "PLUGIN=ble", f"PYTHON={sys.executable}"], cwd=root, check=True
        )
    else:
        plugins.test(root, "ble", [], ["-q"])
    plugins.console(root, "ble", None, ["--plain", "--exec", "ble status"])
    plugins.test(root, "ble", [], ["-q"], config="autospan.fake.yaml")
    missing = subprocess.run(
        [
            python,
            "-I",
            "-m",
            "autospan",
            "test",
            "--config",
            str(checkout / "autospan.fake.yaml"),
            "--set",
            "autospan.plugins=[ble, ble-acceptance]",
            "--",
            "--collect-only",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert missing.returncode != 0
    assert "ble-acceptance" in missing.stdout + missing.stderr

    # Software paths belong to checkout pytest, not AutoSpan's installed scenarios.
    plugins.select_plugin(root, "ble", profile="fake")
    plugins.bootstrap(root, "ble")
    if shutil.which("make") and os.name == "posix":
        subprocess.run(
            ["make", "test", "CONFIG=autospan.fake.yaml", f"PYTHON={sys.executable}"],
            cwd=root,
            check=True,
        )
    else:
        plugins.test(root, "ble", [], ["-q"], config="autospan.fake.yaml")

    # Reprepare with the declared acceptance profile, without device access.
    manifest = plugins._load_manifest(checkout)
    lab = manifest["profiles"]["lab"]
    assert lab["hardware"] is True
    assert lab["scenario_packages"] == ["tests"]
    assert lab["plugins"] == ["ble", "ble-acceptance"]
    plugins.select_plugin(root, "ble", profile="lab")
    plugins.bootstrap(root, "ble")
    assert plugins.doctor(root, "ble")
    lab_state = plugins._read_state(root, "autospan-ble")
    assert lab_state["scenario_packages"] == ["tests"]
    assert "autospan-ble-acceptance" in lab_state["distributions"]
    lab_env, _, _ = plugins.active(root, "ble")
    assert lab_env != env
    plugins.test(
        root,
        "ble",
        [],
        ["--hardware", "--collect-only", "-q"],
        config="autospan.fake.yaml",
    )
    assert check({"nidops/autospan-workspace": root, "nidops/autospan-ble": checkout}) > 20
