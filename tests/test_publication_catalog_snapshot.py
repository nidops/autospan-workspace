"""Regression: frozen source inventories retain catalog schema and metadata."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import pytest
import yaml

from aworkspace import catalog, engine, plugins

SHA = "d" * 40


def _make_catalog(root: Path) -> None:
    (root / "repos.local.yaml").write_text(
        """schema: 1
repositories:
  library:
    url: https://example.org/library.git
    revision: main
    python:
      distribution: library-example
      features:
        tests: {extra: test, default: true}
  autospan-ble:
    url: https://example.org/ble.git
    revision: main
    path: tests/ble
    plugin: true
    depends: [library]
    python:
      distribution: autospan-ble-example
      features:
        testing: {extra: test, default: true}
    tests: [tests]
""",
        encoding="utf8",
    )


def _verify_frozen(root: Path, target: Path) -> None:
    snapshot = target / "sources-frozen.yaml"
    content = yaml.safe_load(snapshot.read_text(encoding="utf8"))
    assert isinstance(content["repositories"], dict)
    assert content["repositories"]["autospan-ble"]["revision"] == SHA
    assert content["repositories"]["autospan-ble"]["depends"] == ["library"]
    assert content["repositories"]["autospan-ble"]["plugin"] is True
    assert content["repositories"]["autospan-ble"]["path"] == "tests/ble"
    assert (
        content["repositories"]["autospan-ble"]["python"]["distribution"] == "autospan-ble-example"
    )
    assert set(catalog.read_catalog(snapshot, root)["repositories"]) == {"library", "autospan-ble"}


def test_global_freeze_emits_schema_valid_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_catalog(tmp_path)
    (tmp_path / ".generated").mkdir()
    state = {"heads": {"library": SHA, "autospan-ble": SHA}, "env": str(tmp_path / "env")}
    (tmp_path / ".generated" / "state.json").write_text(json.dumps(state), encoding="utf8")
    projects = [{"name": "library"}, {"name": "autospan-ble"}]
    monkeypatch.setattr(engine, "doctor", lambda root: True)
    monkeypatch.setattr(engine, "load_selection", lambda root: (None, None, projects))
    monkeypatch.setattr(engine, "get_state", lambda root: state)
    monkeypatch.setattr(engine, "run", lambda *args, **kwargs: "package==1.0")
    monkeypatch.setattr(engine, "python_path", lambda path: path / "python")
    out = engine.freeze(tmp_path)
    _verify_frozen(tmp_path, out)


def test_plugin_freeze_emits_schema_valid_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_catalog(tmp_path)
    state = {"heads": {"library": SHA, "autospan-ble": SHA}, "env": str(tmp_path / "env")}
    projects = [{"name": "library"}, {"name": "autospan-ble"}]
    monkeypatch.setattr(engine, "operation_lock", lambda root: nullcontext())
    monkeypatch.setattr(
        plugins, "_current", lambda root, name: ("autospan-ble", projects, state, {})
    )
    monkeypatch.setattr(plugins, "doctor", lambda root, name: True)
    monkeypatch.setattr(engine, "run", lambda *args, **kwargs: "package==1.0")
    monkeypatch.setattr(engine, "python_path", lambda path: path / "python")
    out = plugins.freeze(tmp_path, "autospan-ble")
    _verify_frozen(tmp_path, out)
