"""Add finalized AutoSpan evidence to a pytest-html 4 report."""

from __future__ import annotations

import html
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote


@dataclass
class Step:
    label: str
    source: str
    started: int
    depth: int
    finished: int | None = None
    outcome: str = "incomplete"
    error: str = ""
    artifacts: list[str] = field(default_factory=list)


def publish(pending: Path, destination: Path) -> None:
    """Atomically publish a complete report, allowing brief Windows file contention."""
    for attempt in range(6):
        try:
            pending.replace(destination)
            return
        except PermissionError as exc:
            # Windows can deny replacement while another publisher or reader
            # briefly holds the destination. Never fall back to a partial copy.
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(0.02 * 2**attempt)


def mark_incomplete(report: Path, reason: str) -> None:
    """Keep an unenriched pytest summary from looking like a complete evidence report."""
    document = report.read_text(encoding="utf-8") if report.is_file() else ""
    message = f"<h2>Evidence report incomplete</h2><p>{html.escape(reason)}</p>"
    if "<body>" in document:
        document = document.replace("<body>", "<body>" + message, 1)
    else:
        document = (
            '<!doctype html><html><head><meta charset="utf-8"></head><body>'
            + message
            + "</body></html>"
        )
    _write_atomic(report, document)


def _write_atomic(report: Path, document: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", dir=report.parent, encoding="utf-8", prefix=".html-", delete=False
    ) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(document)
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(report)
    finally:
        temporary.unlink(missing_ok=True)


def steps_by_test(events: list[dict]) -> dict[str | None, list[Step]]:
    steps: dict[str | None, list[Step]] = {}
    stacks: dict[str | None, list[Step]] = {}
    for event in events:
        test_id = event.get("test_id")
        stack = stacks.setdefault(test_id, [])
        attrs = event["attributes"]
        if event["name"] == "step_started":
            step = Step(attrs["step"], event["source"], event["monotonic_ns"], len(stack))
            steps.setdefault(test_id, []).append(step)
            stack.append(step)
        elif event["name"] == "step_finished":
            if not stack or (stack[-1].label, stack[-1].source) != (attrs["step"], event["source"]):
                raise ValueError(f"Mismatched evidence step: {test_id}: {attrs['step']}")
            step = stack.pop()
            step.finished = event["monotonic_ns"]
            if step.finished < step.started:
                raise ValueError(f"Negative evidence duration: {test_id}: {step.label}")
            step.outcome = attrs["outcome"]
            step.error = attrs.get("error", "")
        elif event["name"] == "artifact_committed":
            for step in reversed(stack):
                if step.label == event.get("step_id"):
                    step.artifacts.append(attrs["artifact_id"])
                    break
    return steps


def enrich(report: Path, directory: Path) -> None:
    """Atomically enrich a report without changing pytest's recorded verdicts.

    Read only a completed manifest. Artifact links are relative to the HTML file;
    the evidence tree must accompany it when sharing captures and serial logs.
    """
    report, directory = report.resolve(), directory.resolve()
    document = report.read_text(encoding="utf-8")
    match = re.search(r'\bdata-jsonblob="([^"]*)"', document)
    if match is None:
        raise ValueError("Expected a pytest-html 4 report with embedded result data")
    data = json.loads(html.unescape(match[1]))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if (
        not events
        or events[-1].get("name") != "run_finished"
        or events[-1].get("attributes", {}).get("outcome") != manifest["outcome"]
        or any(event.get("run_id") != manifest["run_id"] for event in events)
    ):
        raise ValueError("Missing or inconsistent finalized evidence events")
    steps = steps_by_test(events)

    def link(path: Path, label: str) -> str:
        path = path.resolve()
        if not path.is_relative_to(directory):
            raise ValueError(f"Artifact escapes evidence run: {path}")
        if not path.is_file():
            raise ValueError(f"Evidence file missing: {path}")
        relative = quote(Path(os.path.relpath(path, report.parent)).as_posix(), safe="/")
        return f'<a href="{html.escape(relative, quote=True)}">{html.escape(label)}</a>'

    artifacts = manifest["artifacts"]
    links = {}
    for artifact in artifacts:
        path = directory / artifact["path"]
        state = "complete" if artifact["complete"] else "incomplete"
        links[artifact["artifact_id"]] = f"{link(path, path.name)} ({state}, {artifact['size']} B)"

    def details(test_id: str | None) -> str:
        rows = []
        assigned: set[str] = set()
        for step in steps.get(test_id, []):
            assigned.update(step.artifacts)
            duration = (
                "—" if step.finished is None else f"{(step.finished - step.started) / 1e9:.3f}"
            )
            label = "↳ " * step.depth + step.label
            cells = [
                html.escape(label),
                html.escape(step.outcome.upper()),
                duration,
                html.escape(step.error),
                "<br>".join(links[a] for a in step.artifacts),
            ]
            rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
        body = (
            "<table><thead><tr><th>Step</th><th>Result</th><th>Seconds</th>"
            "<th>Error</th><th>Evidence</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
            if rows
            else "<p>No named steps recorded.</p>"
        )
        unassigned = [
            links[a["artifact_id"]]
            for a in artifacts
            if a["context"].get("test_id") == test_id and a["artifact_id"] not in assigned
        ]
        if unassigned:
            body += "<ul>" + "".join(f"<li>{entry}</li>" for entry in unassigned) + "</ul>"
        errors = [
            f"{html.escape(event['name'])}: {html.escape(str(event['attributes']['error']))}"
            for event in events
            if event.get("test_id") == test_id
            and event["name"] != "step_finished"
            and event["attributes"].get("error")
        ]
        if errors:
            body += (
                "<details><summary>Recorded errors</summary><ul>"
                + "".join(f"<li>{error}</li>" for error in errors)
                + "</ul></details>"
            )
        return '<div class="autospan-evidence">' + body + "</div>"

    for test_id, results in data["tests"].items():
        for result in results:
            extras = result.setdefault("extras", [])
            extras[:] = [extra for extra in extras if extra.get("name") != "AutoSpan evidence"]
            extras.append(
                {"name": "AutoSpan evidence", "format_type": "html", "content": details(test_id)}
            )
            if result.get("log") == "No log output captured.":
                result["log"] = ""

    verdict = html.escape(str(manifest["outcome"]).upper())
    summary = (
        '<section id="autospan-evidence-summary" class="autospan-evidence">'
        f"<h2>Final run verdict: {verdict}</h2>"
        f"<p>Run {html.escape(manifest['run_id'])}. "
        "Expand a test below to see its steps and evidence. Steps include setup and teardown; "
        "parent timings include nested work.</p><p>"
        + link(directory / "manifest.json", "Evidence manifest")
        + " · "
        + link(directory / "events.jsonl", "Event log")
        + "</p>"
    )
    if manifest["outcome"] != "passed":
        summary += "<p>The run did not pass. Individual pytest results below retain their recorded phase verdicts.</p>"
    if (
        steps.get(None)
        or any(a["context"].get("test_id") is None for a in artifacts)
        or any(
            event.get("test_id") is None and event["attributes"].get("error") for event in events
        )
    ):
        summary += (
            "<details><summary>Session evidence and serial logs</summary>"
            "<p>Asynchronous serial logs are session-scoped; they are not attributed to a test.</p>"
            + details(None)
            + "</details>"
        )
    # Keep evidence visible even when pytest aborts before creating a result row.
    for test_id in steps.keys() - data["tests"].keys() - {None}:
        summary += (
            f"<details><summary>{html.escape(str(test_id))}</summary>{details(test_id)}</details>"
        )
    summary += "</section>"
    document = (
        document[: match.start(1)]
        + html.escape(json.dumps(data), quote=True)
        + document[match.end(1) :]
    )
    document = re.sub(
        r'<section id="autospan-evidence-summary".*?</section>', "", document, flags=re.S
    )
    document = document.replace("<body>", "<body>\n" + summary, 1)
    style = (
        '<style id="autospan-evidence-style">'
        ".autospan-evidence{color:#222;margin:1em 0}"
        ".autospan-evidence table{width:100%;border-collapse:collapse}"
        ".autospan-evidence th,.autospan-evidence td{padding:.5em;text-align:left;"
        "border:1px solid #ddd;white-space:pre-wrap;overflow-wrap:anywhere}"
        ".autospan-evidence summary{cursor:pointer;font-weight:bold;padding:.5em 0}"
        ".autospan-evidence a{color:#165ab6}"
        ".logwrapper:not(:has(.log)){display:none}"
        "</style>"
    )
    document = re.sub(r'<style id="autospan-evidence-style">.*?</style>', "", document, flags=re.S)
    document = document.replace("</head>", style + "</head>", 1)
    _write_atomic(report, document)
