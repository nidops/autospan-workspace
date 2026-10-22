"""Validated YAML component catalog and generated Kconfig menu."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import yaml
except ImportError:  # Doctor and help must still be callable before `make tools`.
    yaml = None

NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
DIST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
REV = re.compile(r"^[^\s\x00-\x1f]+$")
CANDIDATE_FIELDS = {"url", "revision", "path", "default", "depends", "python", "tests", "plugin"}
TOP_FIELDS = {"schema", "repositories"}
PYTHON_FIELDS = {"distribution", "features"}
FEATURE_FIELDS = {"extra", "default"}


class WorkspaceError(Exception):
    """Actionable configuration or workspace error."""


if yaml is not None:

    class UniqueLoader(yaml.SafeLoader):
        """Reject duplicate and non-string keys instead of silently overriding."""

    def unique_mapping(loader: Any, mapping_value: Any) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        for k, v in mapping_value.value:
            key = loader.construct_object(k, deep=True)
            if not isinstance(key, str) or key in mapping:
                raise WorkspaceError(f"Duplicate or non-string YAML key: {key!r}")
            mapping[key] = loader.construct_object(v, deep=True)
        return mapping

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def symbol(name: str, *, feature: bool = False) -> str:
    return ("FEAT_" if feature else "COMP_") + name.upper().replace("-", "_")


def validate_path(path: str, root: Path) -> str:
    if not isinstance(path, str) or not path or "\\" in path or ":" in path:
        raise WorkspaceError(f"Invalid checkout path: {path!r}")
    p = PurePosixPath(path)
    if (
        p.is_absolute()
        or path != p.as_posix()
        or any(part in (".", "..") for part in path.split("/"))
        or any(not part or part.startswith(".") for part in path.split("/"))
    ):
        raise WorkspaceError(f"Unsafe checkout path: {path!r}")
    return path


def _keys(spec: dict[str, Any], allowed: set[str], where: str) -> None:
    extras = set(spec) - allowed
    if extras:
        raise WorkspaceError(f"{where}: unknown fields: {', '.join(sorted(extras))}")


def read_catalog(path: Path, root: Path) -> dict[str, Any]:
    if yaml is None:
        raise WorkspaceError("Missing PyYAML: run `make tools` first")
    if not path.is_file():
        raise WorkspaceError("Missing repos.local.yaml; copy repos.example.yaml first")
    try:
        raw = yaml.load(path.read_text(encoding="utf8"), Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise WorkspaceError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkspaceError("Repository catalog must be a YAML mapping")
    _keys(raw, TOP_FIELDS, "catalog")
    if type(raw.get("schema")) is not int or raw["schema"] != 1:
        raise WorkspaceError("Expected catalog schema: 1")
    repos = raw.get("repositories")
    if not isinstance(repos, dict) or not repos:
        raise WorkspaceError("repositories must be a nonempty mapping")
    symbols: set[str] = set()
    distributions: set[str] = set()
    paths: list[PurePosixPath] = []
    for name, spec in repos.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise WorkspaceError(f"Invalid repository name {name!r}")
        if not isinstance(spec, dict):
            raise WorkspaceError(f"{name}: expected mapping")
        _keys(spec, CANDIDATE_FIELDS, name)
        url, rev = spec.get("url"), spec.get("revision")
        if not isinstance(url, str) or not url.strip() or "\n" in url:
            raise WorkspaceError(f"{name}: missing/invalid URL")
        if not isinstance(rev, str) or not REV.fullmatch(rev):
            raise WorkspaceError(f"{name}: revision must be a nonempty single token")
        rel = validate_path(spec.get("path", name), root)
        current = PurePosixPath(rel)
        if any(current == old or current in old.parents or old in current.parents for old in paths):
            raise WorkspaceError(f"{name}: overlapping checkout path: {rel}")
        paths.append(current)
        if type(spec.get("default", False)) is not bool:
            raise WorkspaceError(f"{name}: default must be true/false")
        if "plugin" in spec and type(spec["plugin"]) is not bool:
            raise WorkspaceError(f"{name}: plugin must be true/false")
        deps = spec.get("depends", [])
        if not isinstance(deps, list) or not all(isinstance(x, str) for x in deps):
            raise WorkspaceError(f"{name}: depends must be a list of repo names")
        if len(deps) != len(set(deps)) or name in deps:
            raise WorkspaceError(f"{name}: duplicate/self dependency")
        sym = symbol(name)
        if sym in symbols:
            raise WorkspaceError(f"Kconfig symbol collision for {name}")
        symbols.add(sym)
        tests = spec.get("tests", [])
        if not isinstance(tests, list) or not all(isinstance(x, str) for x in tests):
            raise WorkspaceError(f"{name}: tests must be a list of relative paths")
        for test in tests:
            validate_path(test, root)
        pkg = spec.get("python")
        if "python" in spec:
            if not isinstance(pkg, dict):
                raise WorkspaceError(f"{name}: python must be a mapping")
            _keys(pkg, PYTHON_FIELDS, f"{name}.python")
            dist = pkg.get("distribution")
            if not isinstance(dist, str) or not DIST.fullmatch(dist):
                raise WorkspaceError(f"{name}: python.distribution required")
            normalized = re.sub(r"[-_.]+", "-", dist).lower()
            if normalized in distributions:
                raise WorkspaceError(f"{name}: duplicate Python distribution {dist!r}")
            distributions.add(normalized)
            feats = pkg.get("features", {})
            if not isinstance(feats, dict):
                raise WorkspaceError(f"{name}: features must be mapping")
            for feat, options in feats.items():
                if not isinstance(feat, str) or not NAME.fullmatch(feat):
                    raise WorkspaceError(f"{name}: invalid feature name {feat!r}")
                if not isinstance(options, dict):
                    raise WorkspaceError(f"{name}.{feat}: expected mapping")
                _keys(options, FEATURE_FIELDS, f"{name}.{feat}")
                extra = options.get("extra")
                if not isinstance(extra, str) or not DIST.fullmatch(extra):
                    raise WorkspaceError(f"{name}.{feat}: extra required")
                if type(options.get("default", False)) is not bool:
                    raise WorkspaceError(f"{name}.{feat}: default must be true/false")
                fsym = symbol(f"{name}_{feat}", feature=True)
                if fsym in symbols:
                    raise WorkspaceError(f"Feature symbol collision for {name}.{feat}")
                symbols.add(fsym)
    for name, spec in repos.items():
        missing = set(spec.get("depends", [])) - set(repos)
        if missing:
            raise WorkspaceError(f"{name}: unknown dependencies: {sorted(missing)}")

    def visit(n: str, stack: set[str], done: set[str]) -> None:
        if n in stack:
            raise WorkspaceError(f"Dependency cycle involving {n}")
        if n in done:
            return
        for dep in repos[n].get("depends", []):
            visit(dep, stack | {n}, done)
        done.add(n)

    visited: set[str] = set()
    for name in repos:
        visit(name, set(), visited)
    return raw


def kconfig_text(doc: dict[str, Any]) -> str:
    lines = ["# Generated from repos.local.yaml. Do not edit.", 'menu "Components"']
    for name, spec in doc["repositories"].items():
        lines.extend(
            [
                f"config {symbol(name)}",
                f'    bool "{name}"',
                f"    default {'y' if spec.get('default', False) else 'n'}",
            ]
        )
        if spec.get("depends"):
            lines.append("    depends on " + " && ".join(symbol(d) for d in spec["depends"]))
        lines.append("")
        for feat, opts in spec.get("python", {}).get("features", {}).items():
            lines.extend(
                [
                    f"config {symbol(name + '_' + feat, feature=True)}",
                    f'    bool "{name}: {feat}"',
                    f"    depends on {symbol(name)}",
                    f"    default {'y' if opts.get('default', False) else 'n'}",
                    "",
                ]
            )
    lines.append("endmenu")
    return "\n".join(lines) + "\n"


def write_generated_menu(root: Path, doc: dict[str, Any]) -> Path:
    path = root / ".generated" / "components.Kconfig"
    if path.parent.is_symlink() or path.is_symlink():
        raise WorkspaceError("Generated Kconfig paths must not be symlinks")
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = kconfig_text(doc)
    if not path.exists() or path.read_text() != rendered:
        path.write_text(rendered, encoding="utf8")
    return path


def selected(doc: dict[str, Any], flags: dict[str, bool]) -> list[dict[str, Any]]:
    result = []
    for name, spec in doc["repositories"].items():
        if not flags.get(symbol(name), False):
            continue
        if any(not flags.get(symbol(dep), False) for dep in spec.get("depends", [])):
            raise WorkspaceError(f"{name} enabled without a required component")
        url = spec["url"]
        if any(tag in url for tag in ("REPLACE-ME", "YOUR-ORG", "example.invalid")):
            raise WorkspaceError(f"{name}: replace placeholder URL in repos.local.yaml")
        extras = [
            options["extra"]
            for feature, options in spec.get("python", {}).get("features", {}).items()
            if flags.get(symbol(name + "_" + feature, feature=True), False)
        ]
        result.append(
            {
                "name": name,
                "url": url,
                "revision": spec["revision"],
                "path": spec.get("path", name),
                "python": spec.get("python"),
                "extras": sorted(set(extras)),
                "tests": spec.get("tests", []),
            }
        )
    return result
