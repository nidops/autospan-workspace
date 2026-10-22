"""Real Kconfig, Git and pip tests.

Run with AUTO_WORKSPACE_E2E=1 after pip installing requirements-tools.txt.
The regular offline suite does not claim these external integrations passed.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from aworkspace import engine


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, check=True, stdout=subprocess.PIPE
    ).stdout.strip()


def test_menuconfig_dependencies_import_without_a_terminal():
    pytest.importorskip("kconfiglib")
    # Kconfiglib does not install Windows' curses implementation itself.
    # Import in a child because menuconfig exits when curses is unavailable.
    subprocess.run([sys.executable, "-c", "import curses; import menuconfig"], check=True)


@pytest.mark.skipif(
    importlib.util.find_spec("kconfiglib") is None,
    reason="Kconfiglib unavailable in offline test interpreter",
)
def test_real_kconfig_dependencies(tmp_path):
    root = tmp_path / "custom-name"
    root.mkdir()
    original = Path(__file__).resolve().parents[1]
    (root / "Kconfig").write_text((original / "Kconfig").read_text())
    doc = {
        "schema": 1,
        "repositories": {
            "autospan": {"url": "/tmp/autospan.git", "revision": "main", "default": False},
            "sample-plugin": {
                "url": "/tmp/sample-plugin.git",
                "revision": "main",
                "default": True,
                "depends": ["autospan"],
            },
        },
    }
    (root / "repos.local.yaml").write_text(yaml.safe_dump(doc))
    _, flags, projects = engine.load_selection(root)
    assert flags["COMP_AUTOSPAN"] is False
    assert flags["COMP_SAMPLE_PLUGIN"] is False
    assert projects == []
    (root / ".config").write_text("CONFIG_COMP_AUTOSPAN=y\nCONFIG_COMP_SAMPLE_PLUGIN=y\n")
    _, flags, projects = engine.load_selection(root)
    assert [p["name"] for p in projects] == ["autospan", "sample-plugin"]


@pytest.mark.skipif(
    os.environ.get("AUTO_WORKSPACE_E2E") != "1",
    reason="Full Git/pip integration requires explicit opt-in",
)
def test_real_git_and_python_package_lifecycle(tmp_path, monkeypatch):
    pytest.importorskip("kconfiglib")
    base = tmp_path / "sandbox"
    base.mkdir()
    root = base / "renamable-workspace"
    root.mkdir()
    original = Path(__file__).resolve().parents[1]
    for name in ("Kconfig", "requirements-tools.txt"):
        (root / name).write_text((original / name).read_text())
    repositories = {}
    for name in ("autospan", "autospan-ble"):
        origin = tmp_path / (name + "-origin")
        origin.mkdir()
        git(origin, "init", "-q", "-b", "main")
        git(origin, "config", "user.email", "ci@example.invalid")
        git(origin, "config", "user.name", "CI")
        (origin / name.replace("-", "_")).mkdir()
        (origin / name.replace("-", "_") / "__init__.py").write_text('__version__ = "0.0.1"\n')
        (origin / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools>=61"]\n'
            'build-backend = "setuptools.build_meta"\n'
            f'[project]\nname = "{name}"\nversion = "0.0.1"\n'
        )
        marker = "project_" + name.replace("-", "_")
        with (origin / "pyproject.toml").open("a") as handle:
            handle.write(
                f'\n[tool.pytest.ini_options]\naddopts="--strict-markers"\nmarkers=["{marker}: local fixture"]\n'
            )
        (origin / "tests").mkdir()
        (origin / "tests/test_environment.py").write_text(
            f"import importlib.metadata as m\nimport pytest\n@pytest.mark.{marker}\n"
            f'def test_active_package():\n    assert m.version("{name}") == "0.0.1"\n'
        )
        (origin / ".gitignore").write_text(
            "*.egg-info/\nbuild/\n__pycache__/\n.pytest_cache/\nartifacts/\n"
        )
        git(origin, "add", ".")
        git(origin, "commit", "-qm", "start")
        repositories[name] = {
            "url": str(origin),
            "revision": "main",
            "path": name,
            "default": name == "autospan",
            "python": {"distribution": name},
            "tests": ["tests"],
        }
    repos = {"schema": 1, "repositories": repositories}
    (root / "repos.local.yaml").write_text(yaml.safe_dump(repos))
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "ci@example.invalid")
    git(root, "config", "user.name", "CI")
    git(root, "add", "Kconfig", "requirements-tools.txt")
    git(root, "commit", "-qm", "workspace")
    engine.tools(root)
    engine.bootstrap(root)
    assert engine.doctor(root)
    first = engine.get_state(root)
    (root / ".config").write_text("CONFIG_COMP_AUTOSPAN=y\nCONFIG_COMP_AUTOSPAN_BLE=y\n")
    engine.bootstrap(root)
    assert engine.doctor(root)
    engine.run_tests(root, [], ["-q", "--html=artifacts/report.html", "--self-contained-html"])
    for name in repositories:
        report = (root / "repos" / name / "artifacts/report.html").read_text()
        assert "test_active_package" in report
        assert 'href="assets/style.css"' not in report
    engine.run_command(
        root,
        [
            "python",
            "-c",
            "import os,sys; from pathlib import Path; "
            'assert Path(sys.prefix).samefile(os.environ["VIRTUAL_ENV"])',
        ],
    )
    second = engine.get_state(root)
    assert second["env_key"] != first["env_key"]
    assert second["selected"] == ["autospan", "autospan-ble"]
    (root / ".config").write_text("CONFIG_COMP_AUTOSPAN=y\n# CONFIG_COMP_AUTOSPAN_BLE is not set\n")
    engine.bootstrap(root)
    assert engine.doctor(root)
    current = engine.get_state(root)
    assert current["env_key"] == first["env_key"]
    assert (root / "repos" / "autospan-ble").exists()
    # The newly active environment does not contain deselected plugin metadata.
    # Distribution metadata is more reliable than package import mapping.
    script = 'import importlib.metadata as m; assert all(x.metadata["Name"] != "autospan-ble" for x in m.distributions())'
    engine.run([str(engine.python_path(Path(current["env"]))), "-c", script], cwd=root)

    # Package presence alone must not hide broken transitive requirements.
    metadata_script = (
        "import importlib.metadata as m; "
        "d=m.distribution('autospan'); "
        "p=d.locate_file(next(f for f in d.files if str(f).endswith('.dist-info/METADATA'))); "
        "p.write_text('Requires-Dist: autospan-workspace-missing-fixture>=1\\n' + p.read_text())"
    )
    engine.run([str(engine.python_path(Path(current["env"]))), "-c", metadata_script], cwd=root)
    assert not engine.doctor(root)


def test_example_catalog_has_no_implicit_plugin_selection(tmp_path):
    pytest.importorskip("kconfiglib")
    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Kconfig").write_text((original / "Kconfig").read_text())
    (tmp_path / "repos.local.yaml").write_text((original / "repos.example.yaml").read_text())
    _, flags, projects = engine.load_selection(tmp_path)
    assert flags["COMP_AUTOSPAN_BLE"] is False
    assert projects == []


def test_ble_preset_selects_plugin_with_pypi_dependencies(tmp_path):
    pytest.importorskip("kconfiglib")
    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Kconfig").write_text((original / "Kconfig").read_text())
    (tmp_path / "repos.local.yaml").write_text((original / "repos.example.yaml").read_text())
    _, _, projects = engine.load_selection(tmp_path, config_path=original / "configs/ble.config")
    assert {p["name"] for p in projects} == {"autospan-ble"}
    extras = {p["name"]: p["extras"] for p in projects}
    assert extras == {"autospan-ble": ["test"]}
    from aworkspace import plugins

    doc = plugins.read_catalog(tmp_path)
    assert [p["name"] for p in plugins.plugin_sources(doc, "autospan-ble")] == ["autospan-ble"]


def test_invalid_preset_keeps_previous_configuration(tmp_path):
    pytest.importorskip("kconfiglib")
    from aworkspace import catalog

    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Kconfig").write_text((original / "Kconfig").read_text())
    (tmp_path / ".config").write_text("# previous choices\n")
    (tmp_path / "repos.local.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": 1,
                "repositories": {
                    "core": {
                        "url": "https://example.invalid/core",
                        "revision": "main",
                        "default": True,
                    }
                },
            }
        )
    )
    profile = tmp_path / "preset.config"
    profile.write_text("CONFIG_COMP_CORE=y\n")
    with pytest.raises(catalog.WorkspaceError, match="placeholder"):
        engine.load_selection(tmp_path, persist=True, config_path=profile)
    assert (tmp_path / ".config").read_text() == "# previous choices\n"


@pytest.mark.skipif(
    os.environ.get("AUTO_WORKSPACE_E2E") != "1",
    reason="PyPI dependency installation requires explicit opt-in",
)
def test_plugin_installs_pypi_companions_without_source_checkouts(tmp_path):
    from aworkspace import plugins

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "requirements-tools.txt").write_text("fixture tools\n")
    origin = tmp_path / "plugin-origin"
    origin.mkdir()
    (origin / ".gitignore").write_text("build/\n*.egg-info/\n__pycache__/\n")
    (origin / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=77"]\n'
        'build-backend="setuptools.build_meta"\n'
        '[project]\nname="autospan-pypi-probe"\nversion="0.1.0"\n'
        'dependencies=["autospan==1.0.0", "structwire==1.0.0"]\n'
        "[tool.setuptools]\npy-modules=[]\n"
    )
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.name", "Test")
    git(origin, "config", "user.email", "test@example.invalid")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "plugin with published dependencies")
    (root / "repos.local.yaml").write_text(
        yaml.safe_dump(
            {
                "schema": 1,
                "repositories": {
                    "autospan-pypi-probe": {
                        "url": str(origin),
                        "revision": "main",
                        "plugin": True,
                        "python": {"distribution": "autospan-pypi-probe"},
                    }
                },
            }
        )
    )
    plugins.bootstrap(root, "pypi-probe")
    assert plugins.doctor(root, "pypi-probe")
    state = plugins._read_state(root, "autospan-pypi-probe")
    assert state["selected"] == ["autospan-pypi-probe"]
    assert not (root / "repos/autospan").exists()
    assert not (root / "repos/structwire").exists()
    script = (
        "import sys, autospan, structwire; from pathlib import Path; "
        "from importlib.metadata import distribution, version; "
        "assert version('autospan') == '1.0.0'; "
        "assert version('structwire') == '1.0.0'; "
        "assert all(distribution(name).read_text('direct_url.json') "
        "is None for name in ('autospan', 'structwire')); "
        "assert all(Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()) "
        "for module in (autospan, structwire))"
    )
    engine.run([str(engine.python_path(Path(state["env"]))), "-I", "-c", script], cwd=root)
