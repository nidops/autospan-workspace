"""Catch local and cross-repository documentation anchors before release."""

from pathlib import Path

import pytest

from scripts.check_links import check


def test_workspace_documentation_links():
    root = Path(__file__).resolve().parents[1]
    assert check({"nidops/autospan-workspace": root}) > 10


def test_cross_repository_anchor_is_checked(tmp_path):
    workspace, plugin = tmp_path / "workspace", tmp_path / "plugin"
    workspace.mkdir()
    plugin.mkdir()
    (workspace / "README.md").write_text("# Workspace\n\n## Tests, hardware and reports\n")
    guide = plugin / "CONTRIBUTING.md"
    base = "https://github.com/nidops/autospan-workspace/blob/master/README.md#"
    guide.write_text(f"[hardware]({base}run-a-physical-bench)\n")
    roots = {"nidops/autospan-workspace": workspace, "example/plugin": plugin}
    with pytest.raises(ValueError, match="missing anchor"):
        check(roots)
    guide.write_text(f"[hardware]({base}tests-hardware-and-reports)\n")
    assert check(roots) == 1
