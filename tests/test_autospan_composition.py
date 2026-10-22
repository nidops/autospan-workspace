"""Exercise real AutoSpan scaffolds and source composition without hardware."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from aworkspace import engine, plugins

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTO_WORKSPACE_E2E") != "1",
    reason="Scaffold and pip integration requires explicit opt-in",
)


def test_two_scaffolded_plugins_share_runtime_and_cleanup_after_failure(tmp_path):
    framework = tmp_path / "framework"
    subprocess.run([sys.executable, "-m", "venv", str(framework)], check=True)
    python = str(engine.python_path(framework))
    subprocess.run([python, "-m", "pip", "install", "autospan[test]==1.0.0"], check=True)
    repositories = {}
    for name in ("primary", "companion"):
        subprocess.run([python, "-m", "autospan", "new", name, "--dir", str(tmp_path)], check=True)
        source = tmp_path / f"autospan-{name}"
        plugin = source / "src" / name / "plugin.py"
        code = plugin.read_text()
        code = code.replace(
            "from typing import Any", "from typing import Any\nfrom pathlib import Path"
        )
        code = code.replace(
            '            yield Rig(str(config["name"]))',
            '            try:\n                yield Rig(str(config["name"]))\n'
            f"            finally:\n                Path({str(tmp_path / (name + '.closed'))!r})"
            '.write_text("closed")',
        )
        plugin.write_text(code)
        (source / ".gitignore").write_text("*.egg-info/\n__pycache__/\nbuild/\nartifacts/\n")
        if name == "primary":
            (source / "autospan.yaml").write_text(
                "autospan:\n  plugins: [primary, companion]\n"
                "primary: {name: bench-1}\ncompanion: {name: bench-1}\n"
            )
            (source / "src/primary/scenarios/test_composition.py").write_text(
                "from primary.plugin import Rig as Primary\n"
                "from companion.plugin import Rig as Companion\n"
                "def test_both_contributions(runtime):\n"
                "    assert runtime.require('primary.rig', Primary).name == 'bench-1'\n"
                "    assert runtime.require('companion.rig', Companion).name == 'bench-1'\n"
                "def test_deliberate_failure(runtime):\n"
                "    assert runtime.require('companion.rig', Companion).name == 'bench-1'\n"
                "    assert False, 'intentional composition failure'\n"
            )
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "."],
            [
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "scaffold",
            ],
        ):
            subprocess.run(["git", *args], cwd=source, check=True)
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        repositories[f"autospan-{name}"] = {
            "url": str(source),
            "revision": sha,
            "plugin": True,
            "python": {"distribution": f"autospan-{name}"},
            "tests": ["tests"],
        }
    repositories["autospan-primary"]["depends"] = ["autospan-companion"]
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "requirements-tools.txt").write_bytes(
        (Path(__file__).resolve().parents[1] / "requirements-tools.txt").read_bytes()
    )
    (root / "repos.local.yaml").write_text(
        yaml.safe_dump({"schema": 1, "repositories": repositories})
    )
    plugins.bootstrap(root, "primary")
    assert plugins.doctor(root, "primary")
    state = plugins._read_state(root, "autospan-primary")
    assert state["heads"] == {name: spec["revision"] for name, spec in repositories.items()}
    env_python = str(engine.python_path(Path(state["env"])))
    discovery = subprocess.run(
        [env_python, "-I", "-m", "autospan", "plugins"],
        cwd=tmp_path,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "primary" in discovery.stdout and "companion" in discovery.stdout
    with pytest.raises(subprocess.CalledProcessError):
        plugins.test(root, "primary", [], ["-q"], config="autospan.yaml")
    for name in ("primary", "companion"):
        assert (tmp_path / f"{name}.closed").read_text() == "closed"
    plugins.test(
        root, "primary", [], ["-q", "-k", "not deliberate_failure"], config="autospan.yaml"
    )
    plugins.test(root, "primary", [], ["-q"])
