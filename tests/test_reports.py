"""Final reports preserve pytest verdicts and correlate completed evidence exactly."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

from aworkspace import catalog, engine, reports


def pytest_html(path, outcomes=None):
    data = {
        "tests": {
            name: [{"result": outcome, "extras": [], "log": "original traceback"}]
            for name, outcome in (outcomes or {"test_peer": "Passed"}).items()
        }
    }
    path.write_text(
        '<html><head></head><body><div id="data-container" data-jsonblob="'
        + html.escape(json.dumps(data), quote=True)
        + '"></div></body></html>'
    )


def report_data(path):
    return json.loads(html.unescape(re.search(r'data-jsonblob="([^"]*)"', path.read_text())[1]))


def evidence(directory, *, outcome="passed", artifacts=()):
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(
        json.dumps({"run_id": directory.name, "outcome": outcome, "artifacts": list(artifacts)})
    )
    write_events(directory, [])


def event(name, time, *, test="test_peer", step="Connect <peer>", **attributes):
    return {
        "name": name,
        "monotonic_ns": int(time * 1e9),
        "source": "ble",
        "test_id": test,
        "step_id": step,
        "attributes": {"step": step, **attributes},
    }


def write_events(directory, events):
    manifest = json.loads((directory / "manifest.json").read_text())
    events = [*events, event("run_finished", 100, test=None, outcome=manifest["outcome"])]
    for item in events:
        item["run_id"] = manifest["run_id"]
    (directory / "events.jsonl").write_text("\n".join(map(json.dumps, events)))


def test_expandable_steps_links_escaping_and_repeated_labels(tmp_path):
    output = tmp_path / "report.html"
    pytest_html(output)
    run = tmp_path / "evidence" / "run"
    artifacts = [
        {
            "artifact_id": name,
            "path": name + " #.pcap",
            "size": 3,
            "complete": True,
            "context": {"test_id": test},
        }
        for name, test in (("one", "test_peer"), ("two", "test_peer"), ("serial", None))
    ]
    evidence(run, artifacts=artifacts)
    for artifact in artifacts:
        (run / artifact["path"]).write_bytes(b"123")
    write_events(
        run,
        [
            event("step_started", 1),
            event("step_started", 2, step="Capture"),
            event("artifact_committed", 3, step="Capture", artifact_id="one"),
            event("step_finished", 4, step="Capture", outcome="passed"),
            event("step_started", 5, step="Capture"),
            event("artifact_committed", 6, step="Capture", artifact_id="two"),
            event("step_finished", 7, step="Capture", outcome="passed"),
            event("step_finished", 8, outcome="passed"),
        ],
    )
    reports.enrich(output, run)
    reports.enrich(output, run)
    document = output.read_text()
    row = report_data(output)["tests"]["test_peer"][0]
    assert row["result"] == "Passed" and row["log"] == "original traceback"
    assert len(row["extras"]) == 1
    details = row["extras"][0]["content"]
    assert "Connect &lt;peer&gt;" in details and "↳ Capture" in details
    assert "7.000" in details and details.count("2.000") == 2
    assert details.count("one%20%23.pcap") == details.count("two%20%23.pcap") == 1
    assert "serial" not in details
    assert document.count('id="autospan-evidence-summary"') == 1
    assert "Session evidence and serial logs" in document
    for href in re.findall(r'href="([^"]*)"', document + details):
        assert (output.parent / unquote(html.unescape(href))).is_file()


def test_teardown_failure_incomplete_and_skipped_tests_are_visible(tmp_path):
    output = tmp_path / "report.html"
    pytest_html(output, {"test_peer": "Passed", "test_skipped": "Skipped"})
    run = tmp_path / "run"
    evidence(run, outcome="failed")
    write_events(
        run,
        [
            event("step_started", 1),
            event("step_finished", 2, outcome="failed", error='<script>alert("x")</script>'),
            event("step_started", 3, step="Unfinished"),
            event("step_started", 4, test=None, step="Session teardown"),
            event(
                "step_finished",
                5,
                test=None,
                step="Session teardown",
                outcome="failed",
                error="close failed",
            ),
            event("component_close_failed", 6, test=None, error="serial close failed"),
        ],
    )
    reports.enrich(output, run)
    data = report_data(output)["tests"]
    details = data["test_peer"][0]["extras"][0]["content"]
    assert "FAILED" in details and "INCOMPLETE" in details
    assert "&lt;script&gt;" in details and "<script>" not in details
    assert data["test_peer"][0]["result"] == "Passed"
    assert data["test_skipped"][0]["result"] == "Skipped"
    assert "No named steps recorded" in data["test_skipped"][0]["extras"][0]["content"]
    assert "Final run verdict: FAILED" in output.read_text()
    assert "Session teardown" in output.read_text() and "close failed" in output.read_text()
    assert "component_close_failed: serial close failed" in output.read_text()


def test_manifest_paths_cannot_escape_run(tmp_path):
    output = tmp_path / "report.html"
    pytest_html(output)
    run = tmp_path / "run"
    evidence(
        run,
        artifacts=[
            {
                "artifact_id": "bad",
                "path": "../../outside.txt",
                "complete": True,
                "size": 1,
                "context": {},
            }
        ],
    )
    with pytest.raises(ValueError, match="escapes evidence"):
        reports.enrich(output, run)


@pytest.mark.parametrize("exitcode", [0, 1])
def test_hosted_report_finalizes_after_command_and_preserves_exitcode(
    tmp_path, monkeypatch, exitcode
):
    output = tmp_path / "report.html"
    pytest_html(output)  # A stale report must not be reused.
    args = [
        "python",
        "-m",
        "autospan",
        "test",
        "--config",
        "bench.yaml",
        "--",
        "--html",
        str(output),
    ]

    def run(command, **kwargs):
        assert not output.exists()
        parent = Path(command[command.index("--evidence-dir") + 1])
        assert parent.is_relative_to(tmp_path / "artifacts/evidence")
        pytest_html(
            Path(command[-1].split("=", 1)[1]), {"test_peer": "Failed" if exitcode else "Passed"}
        )
        evidence(parent / "run", outcome="failed" if exitcode else "passed")
        if exitcode:
            raise subprocess.CalledProcessError(exitcode, command)

    monkeypatch.setattr(engine, "_run_report_child", run)
    if exitcode:
        with pytest.raises(subprocess.CalledProcessError) as exc:
            engine.run_hosted_tests(args, cwd=tmp_path, env={})
        assert exc.value.returncode == exitcode
    else:
        engine.run_hosted_tests(args, cwd=tmp_path, env={})
    assert "Final run verdict: " + ("FAILED" if exitcode else "PASSED") in output.read_text()


def test_unfinalized_evidence_is_not_reported_as_complete(tmp_path, monkeypatch):
    output = tmp_path / "report.html"
    monkeypatch.setattr(
        engine,
        "_run_report_child",
        lambda args, **kwargs: pytest_html(Path(args[-1].split("=", 1)[1])),
    )
    with pytest.raises(catalog.WorkspaceError, match="incomplete"):
        engine.run_hosted_tests(
            ["python", "-m", "autospan", "test", "--", f"--html={output}"],
            cwd=tmp_path,
            env={},
        )
    assert "Evidence report incomplete" in output.read_text()


@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_collection_does_not_replace_report(tmp_path, monkeypatch, hosted, explicit):
    output = tmp_path / "report.html"
    output.write_text("previous report")
    calls = []
    monkeypatch.setattr(engine, "run", lambda args, **kwargs: calls.append(args))
    command = ["autospan", "test", "--"] if hosted else ["pytest"]
    args = ["python", "-m", *command, *([f"--html={output}"] if explicit else []), "--co"]
    runner = engine.run_hosted_tests if hosted else engine.run_pytest
    runner(args, cwd=tmp_path, env={})
    assert calls == [[*args, "-p", "no:html", "-p", "pytest_html.plugin", "--html="]]
    assert output.read_text() == "previous report"


@pytest.mark.skipif(os.name != "posix" or not shutil.which("make"), reason="GNU Make required")
@pytest.mark.parametrize("autoload", [False, True])
@pytest.mark.parametrize("mode", ["report", "collect", "collect-with-report"])
def test_report_plugin_loading_avoids_stdlib_collision(tmp_path, capfd, autoload, mode):
    """Exercise real pytest loading with a local reporter entry point, without pip."""
    original = Path(__file__).resolve().parents[1]
    (tmp_path / "Makefile").write_bytes((original / "Makefile").read_bytes())
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ws.py").write_text("import json,sys; print(json.dumps(sys.argv[1:]))\n")
    result = subprocess.run(
        ["make", "--no-print-directory", "test", f"PYTHON={sys.executable}", "REPORT=report.html"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    forwarded = json.loads(result.stdout)
    options = forwarded[forwarded.index("--") + 1 :] if mode != "collect" else []
    if mode != "report":
        options.append("--collect-only")

    # Model pytest-html's actual module/entry-point names. Real pytest must load
    # it exactly once even when stdlib html was imported before pytest started.
    package = tmp_path / "pytest_html"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "fixtures.py").touch()
    (package / "plugin.py").write_text(
        "from pathlib import Path\n"
        "def pytest_addoption(parser):\n"
        "    parser.addoption('--html', dest='htmlpath')\n"
        "    parser.addoption('--self-contained-html', action='store_true')\n"
        "def pytest_sessionfinish(session):\n"
        "    path = session.config.getoption('htmlpath')\n"
        "    if path:\n"
        "        Path(path).write_text('<html>completed report</html>')\n"
    )
    (tmp_path / "fixture_metadata.py").touch()
    distribution = tmp_path / "workspace_report_fixture-1.0.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text("Name: workspace-report-fixture\nVersion: 1.0\n")
    (distribution / "entry_points.txt").write_text(
        "[pytest11]\nhtml = pytest_html.plugin\nmetadata = fixture_metadata\n"
    )
    (tmp_path / "test_case.py").write_text("def test_case():\n    assert True\n")
    output = tmp_path / "report.html"
    output.write_text("previous report")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tmp_path)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "" if autoload else "1"
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    engine.run_pytest(
        [
            sys.executable,
            "-c",
            "import html,sys,pytest; sys.exit(pytest.main(sys.argv[1:]))",
            "-q",
            "-W",
            "error::pytest.PytestAssertRewriteWarning",
            "test_case.py",
            *options,
        ],
        cwd=tmp_path,
        env=env,
    )
    captured = capfd.readouterr()
    assert "PytestAssertRewriteWarning" not in captured.out + captured.err
    expected = "<html>completed report</html>" if mode == "report" else "previous report"
    assert output.read_text() == expected


@pytest.mark.parametrize("exitcode", [0, 1])
def test_plain_pytest_reports_stage_results_without_hosted_evidence(
    tmp_path, monkeypatch, exitcode
):
    output = tmp_path / "report.html"
    output.write_text("stale report")

    def run(command, **kwargs):
        assert not output.exists()
        assert "--evidence-dir" not in command
        pytest_html(Path(command[-1].split("=", 1)[1]))
        if exitcode:
            raise subprocess.CalledProcessError(exitcode, command)

    monkeypatch.setattr(engine, "_run_report_child", run)
    args = ["python", "-m", "pytest", f"--html={output}"]
    if exitcode:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            engine.run_pytest(args, cwd=tmp_path, env={})
        assert caught.value.returncode == exitcode
    else:
        engine.run_pytest(args, cwd=tmp_path, env={})
    assert "data-jsonblob" in output.read_text()
    assert "incomplete" not in output.read_text()


def test_original_test_failure_survives_incomplete_marker_failure(tmp_path, monkeypatch, capsys):
    original = subprocess.CalledProcessError(2, ["pytest"])

    def fail(*args, **kwargs):
        raise original

    monkeypatch.setattr(engine, "_run_report_child", fail)
    monkeypatch.setattr(
        reports, "mark_incomplete", lambda *args: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(subprocess.CalledProcessError) as caught:
        engine.run_hosted_tests(
            ["python", "-m", "autospan", "test", "--", "--html=report.html"], cwd=tmp_path, env={}
        )
    assert caught.value is original
    assert "disk full" in capsys.readouterr().err


def test_spawn_error_produces_current_diagnostic_report(tmp_path, monkeypatch):
    output = tmp_path / "report.html"
    output.write_text("stale passing report")
    original = FileNotFoundError("interpreter vanished")

    def fail(*args, **kwargs):
        raise original

    monkeypatch.setattr(engine, "_run_report_child", fail)
    with pytest.raises(FileNotFoundError) as caught:
        engine.run_hosted_tests(
            ["python", "-m", "autospan", "test", "--", f"--html={output}"], cwd=tmp_path, env={}
        )
    assert caught.value is original
    assert "stale passing" not in output.read_text()
    assert "interpreter vanished" in output.read_text()


def test_concurrent_reports_never_combine_other_runs_results(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)
    output = tmp_path / "report.html"
    original_enrich = reports.enrich

    def enrich(report, directory):
        # Both children have written before enrichment starts. A shared output
        # necessarily contains the other child's results for one of the runs.
        assert set(report_data(report)["tests"]) == {directory.name}
        original_enrich(report, directory)

    def run(command, **kwargs):
        parent = Path(command[command.index("--evidence-dir") + 1])
        identifier = parent.name
        destination = Path(
            [arg.split("=", 1)[1] for arg in command if arg.startswith("--html=")][-1]
        )
        pytest_html(destination, {identifier: "Passed"})
        evidence(parent / identifier)
        barrier.wait(timeout=3)

    monkeypatch.setattr(engine, "_run_report_child", run)
    monkeypatch.setattr(reports, "enrich", enrich)

    def invoke():
        engine.run_hosted_tests(
            ["python", "-m", "autospan", "test", "--", f"--html={output}"], cwd=tmp_path, env={}
        )

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(invoke) for _ in range(2)]
        for future in futures:
            future.result(5)
    (identifier,) = report_data(output)["tests"]
    assert f"Run {identifier}." in output.read_text()


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_report_publication_recovers_from_windows_file_contention(tmp_path, monkeypatch, winerror):
    output = tmp_path / "report.html"
    original_replace = Path.replace
    attempts = []

    def replace(source, destination):
        attempts.append(source)
        if len(attempts) <= 2:
            error = PermissionError("Another publisher holds the destination")
            error.winerror = winerror
            raise error
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(
        engine,
        "_run_report_child",
        lambda args, **kwargs: pytest_html(Path(args[-1].split("=", 1)[1])),
    )
    engine.run_pytest(["python", "-m", "pytest", f"--html={output}"], cwd=tmp_path, env={})
    assert report_data(output)["tests"]["test_peer"][0]["result"] == "Passed"
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert not list(tmp_path.glob(".report-*.html"))


@pytest.mark.parametrize("winerror", [None, 5, 32, 33])
@pytest.mark.parametrize("exitcode", [0, 1])
def test_report_publication_failure_is_bounded_and_retains_results(
    tmp_path, monkeypatch, capsys, winerror, exitcode
):
    output = tmp_path / "report.html"
    attempts = []
    original_error = PermissionError("Report is locked")
    if winerror is not None:
        original_error.winerror = winerror

    def replace(source, destination):
        attempts.append(source)
        raise original_error

    def run(args, **kwargs):
        pytest_html(Path(args[-1].split("=", 1)[1]))
        if exitcode:
            raise subprocess.CalledProcessError(exitcode, args)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(engine, "_run_report_child", run)
    error_type = subprocess.CalledProcessError if exitcode else catalog.WorkspaceError
    with pytest.raises(error_type) as caught:
        engine.run_pytest(["python", "-m", "pytest", f"--html={output}"], cwd=tmp_path, env={})
    if exitcode:
        assert caught.value.returncode == exitcode
    else:
        assert caught.value.__cause__ is original_error
    if winerror is None:
        assert len(attempts) == 1
    else:
        assert 1 < len(attempts) <= 10
    assert len(set(attempts)) == 1
    assert not output.exists()
    (pending,) = tmp_path.glob(".report-*.html")
    assert report_data(pending)["tests"]["test_peer"][0]["result"] == "Passed"
    assert "Final HTML report:" not in capsys.readouterr().out


@pytest.mark.skipif(os.name != "nt", reason="Windows denies replacement of open files")
def test_native_windows_report_publication_waits_for_reader(tmp_path):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event

    pending, output = tmp_path / "pending.html", tmp_path / "report.html"
    pending.write_text("complete new report")
    output.write_text("previous complete report")
    started = Event()

    def publish():
        started.set()
        reports.publish(pending, output)

    with ThreadPoolExecutor(1) as pool:
        with output.open("rb"):
            future = pool.submit(publish)
            assert started.wait(timeout=5)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
        future.result(timeout=5)
    assert output.read_text() == "complete new report"
    assert not pending.exists()


@pytest.mark.parametrize("kind", ["truncated", "wrong-run", "wrong-verdict"])
def test_rejects_unfinalized_or_mixed_events(tmp_path, kind):
    output = tmp_path / "report.html"
    pytest_html(output)
    run = tmp_path / "run"
    evidence(run)
    path = run / "events.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    if kind == "truncated":
        records = []
    elif kind == "wrong-run":
        records[-1]["run_id"] = "another-run"
    else:
        records[-1]["attributes"]["outcome"] = "failed"
    path.write_text("\n".join(map(json.dumps, records)))
    with pytest.raises(ValueError, match="finalized evidence"):
        reports.enrich(output, run)


def test_report_deletion_failure_stops_before_running_tests(tmp_path, monkeypatch):
    output = tmp_path / "report.html"
    output.mkdir()
    monkeypatch.setattr(engine, "run", lambda *a, **kw: pytest.fail("tests must not start"))
    with pytest.raises(catalog.WorkspaceError, match="previous HTML report.*tests not started"):
        engine.run_hosted_tests(
            ["python", "-m", "autospan", "test", "--", f"--html={output}"], cwd=tmp_path, env={}
        )
