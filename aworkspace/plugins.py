"""Project/plugin-oriented workspace orchestration.

Every Git checkout stays below repos/.  Ordinary developer checkouts remain at
repos/<catalog-path>; pinned per-plugin worktrees live at repos/.isolated/.
Global Kconfig selection supports editable development in the ordinary checkouts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from . import catalog, engine

PLUGIN_STATE_SCHEMA = 1


def read_catalog(root: Path) -> dict[str, Any]:
    return catalog.read_catalog(root / "repos.local.yaml", root)


def plugin_names(doc: dict[str, Any]) -> list[str]:
    """Find plugin projects without treating framework/library packages as plugins."""
    return [
        name
        for name, spec in doc["repositories"].items()
        if spec.get("plugin", name.startswith("autospan-") and name != "autospan")
    ]


def resolve_plugin(doc: dict[str, Any], name: str) -> str:
    candidates = plugin_names(doc)
    if name in candidates:
        return name
    expanded = "autospan-" + name
    if expanded in candidates:
        return expanded
    raise catalog.WorkspaceError(
        f"Unknown plugin {name!r}. Available plugins: {', '.join(candidates) or '(none)'}"
    )


def plugin_sources(doc: dict[str, Any], plugin: str) -> list[dict[str, Any]]:
    """Dependency closure, ordered dependencies first; no global component flags."""
    needed: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        # Catalog validation rejects cycles. Do not revisit shared dependency
        # subgraphs: otherwise diamond DAGs grow exponentially.
        if name in visited:
            return
        visited.add(name)
        for dep in doc["repositories"][name].get("depends", []):
            visit(dep)
        needed.append(name)

    visit(plugin)
    flags = {catalog.symbol(name): True for name in needed}
    for name in needed:
        for feature, spec in (
            doc["repositories"][name].get("python", {}).get("features", {}).items()
        ):
            flags[catalog.symbol(name + "_" + feature, feature=True)] = spec.get("default", False)
    selected = {p["name"]: p for p in catalog.selected(doc, flags)}
    return [selected[name] for name in needed]


def selected_plugin(root: Path) -> str | None:
    path = root / ".generated" / "active-plugin.json"
    if not path.exists():
        return None
    _safe_directory(root, path.parent)
    if path.is_symlink():
        raise catalog.WorkspaceError(f"Plugin selection must not be a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf8"))
    except (ValueError, OSError) as exc:
        raise catalog.WorkspaceError(f"Invalid plugin selection {path}: {exc}") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or not isinstance(value.get("plugin"), str)
    ):
        raise catalog.WorkspaceError(f"Invalid plugin selection: {path}")
    return resolve_plugin(read_catalog(root), value["plugin"])


def selected_profile(root: Path, plugin: str) -> str | None:
    """Return saved profile only for the active project, never for an override."""
    if selected_plugin(root) != resolve_plugin(read_catalog(root), plugin):
        return None
    path = root / ".generated" / "active-plugin.json"
    value = json.loads(path.read_text(encoding="utf8"))
    profile = value.get("profile")
    if profile is not None and (
        not isinstance(profile, str) or not catalog.NAME.fullmatch(profile)
    ):
        raise catalog.WorkspaceError("Invalid saved plugin profile; reselect your project")
    return profile


def select_plugin(root: Path, name: str | None = None, *, profile: str | None = None) -> str:
    doc = read_catalog(root)
    if name is None:
        candidates = plugin_names(doc)
        if not candidates:
            raise catalog.WorkspaceError("No plugins found in repos.local.yaml")
        if not os.isatty(0):
            raise catalog.WorkspaceError("Non-interactive selection requires --plugin NAME")
        for index, candidate in enumerate(candidates, 1):
            print(f"  {index}. {candidate}")
        response = input("Select plugin number: ").strip()
        if not response.isdecimal() or not 1 <= int(response) <= len(candidates):
            raise catalog.WorkspaceError("Invalid plugin selection")
        name = candidates[int(response) - 1]
    plugin = resolve_plugin(doc, name)
    if profile is not None and not catalog.NAME.fullmatch(profile):
        raise catalog.WorkspaceError(f"Invalid profile name: {profile!r}")
    with engine.operation_lock(root):
        _safe_directory(root, root / ".generated")
        engine.atomic_json(
            root / ".generated" / "active-plugin.json",
            {"schema": 1, "plugin": plugin, "profile": profile},
        )
    print(f"Selected plugin: {plugin}" + (f" / profile: {profile}" if profile else ""))
    return plugin


def deselect_plugin(root: Path) -> None:
    with engine.operation_lock(root):
        _safe_directory(root, root / ".generated")
        (root / ".generated" / "active-plugin.json").unlink(missing_ok=True)
    print("Cleared default plugin selection; commands now use global Kconfig selection")


def show_plugins(root: Path) -> None:
    doc = read_catalog(root)
    active = selected_plugin(root)
    names = plugin_names(doc)
    if not names:
        print("No plugins found in repos.local.yaml")
    for name in names:
        print(f"  {name}" + (" [selected]" if name == active else ""))


def state_path(root: Path, plugin: str) -> Path:
    return root / ".generated" / "plugins" / plugin / "state.json"


def checkout_base(root: Path, plugin: str) -> Path:
    return root / "repos" / ".isolated" / plugin


def env_base(root: Path, plugin: str) -> Path:
    return root / ".envs" / plugin


def _safe_directory(root: Path, path: Path) -> None:
    """Reject preexisting symlink ancestors, including the workspace's repos root."""
    if not path.is_relative_to(root):
        raise catalog.WorkspaceError(f"Path outside workspace: {path}")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise catalog.WorkspaceError(f"Symlink path is not allowed: {current}")
        if current.exists() and not current.is_dir():
            raise catalog.WorkspaceError(f"Expected directory: {current}")


def _resolve_heads(root: Path, sources: list[dict[str, Any]]) -> dict[str, str]:
    """Fetch objects, but NEVER move the developer checkouts at repos/<name>."""
    engine.assert_safe_checkouts(root, sources, require_clean=False)
    resolved: dict[str, str] = {}
    for source in sources:
        dest = engine.checkout_path(root, source)
        if not dest.exists():
            _safe_directory(root, dest.parent)
            dest.parent.mkdir(parents=True, exist_ok=True)
            engine.git(root, "clone", "--origin", "origin", "--", source["url"], str(dest))
        sha = engine.fetch_revision(root, dest, source["revision"])
        resolved[source["name"]] = sha
    return resolved


def _worktree_path(root: Path, plugin: str, name: str, sha: str) -> Path:
    return checkout_base(root, plugin) / name / sha


def _verify_worktree(root: Path, path: Path, source: dict[str, Any], sha: str) -> None:
    _safe_directory(root, path.parent)
    if (
        path.is_symlink()
        or not path.is_dir()
        or not (path / ".git").exists()
        or (path / ".git").is_symlink()
    ):
        raise catalog.WorkspaceError(f"Missing or unsafe pinned checkout: {path}")
    if engine.git_head(path) != sha:
        raise catalog.WorkspaceError(f"Pinned checkout HEAD changed: {path}")
    if (
        engine.git(root, "-C", str(path), "remote", "get-url", "origin", output=True)
        != source["url"]
    ):
        raise catalog.WorkspaceError(f"Pinned checkout origin changed: {path}")
    if engine.git(root, "-C", str(path), "status", "--porcelain", output=True):
        raise catalog.WorkspaceError(f"Pinned checkout is dirty: {path}")


def _ensure_worktrees(
    root: Path, plugin: str, sources: list[dict[str, Any]], heads: dict[str, str]
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for source in sources:
        name = source["name"]
        path = _worktree_path(root, plugin, name, heads[name])
        _safe_directory(root, path.parent)
        if not path.exists() and not path.is_symlink():
            path.parent.mkdir(parents=True, exist_ok=True)
            engine.git(
                root,
                "-C",
                str(engine.checkout_path(root, source)),
                "worktree",
                "add",
                "--detach",
                str(path),
                heads[name],
            )
        _verify_worktree(root, path, source, heads[name])
        paths[name] = path
    return paths


def _load_manifest(plugin_checkout: Path) -> dict[str, Any]:
    """Plugin-owned optional metadata. Package requirements remain in pyproject.toml."""
    path = plugin_checkout / "autospan-workspace.yaml"
    if not path.exists():
        return {}
    if catalog.yaml is None:
        raise catalog.WorkspaceError("PyYAML is required to load plugin metadata")
    try:
        data = catalog.yaml.load(path.read_text(encoding="utf8"), Loader=catalog.UniqueLoader)
    except (catalog.yaml.YAMLError, OSError) as exc:
        raise catalog.WorkspaceError(f"Invalid plugin metadata {path}: {exc}") from exc
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise catalog.WorkspaceError(f"{path}: expected schema: 1")
    catalog._keys(data, {"schema", "tests", "console", "profiles", "revisions"}, str(path))
    revisions = data.get("revisions", {})
    if not isinstance(revisions, dict) or not all(
        isinstance(name, str) and isinstance(revision, str) and catalog.REV.fullmatch(revision)
        for name, revision in revisions.items()
    ):
        raise catalog.WorkspaceError(f"{path}: revisions must map names to Git refs")
    if "tests" in data:
        tests = data["tests"]
        if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
            raise catalog.WorkspaceError(f"{path}: tests must be a list of relative paths")
        for item in tests:
            catalog.validate_path(item, plugin_checkout)
    if "console" in data:
        console = data["console"]
        if not isinstance(console, dict):
            raise catalog.WorkspaceError(f"{path}: console must be a mapping")
        catalog._keys(console, {"config"}, f"{path}.console")
        if "config" in console:
            catalog.validate_path(console["config"], plugin_checkout)
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        raise catalog.WorkspaceError(f"{path}: profiles must be a mapping")
    for name, profile in profiles.items():
        if not isinstance(name, str) or not catalog.NAME.fullmatch(name):
            raise catalog.WorkspaceError(f"{path}: invalid profile name {name!r}")
        if not isinstance(profile, dict):
            raise catalog.WorkspaceError(f"{path}: profile {name} must be a mapping")
        catalog._keys(
            profile,
            {"tests", "console", "hardware", "scenario_packages", "plugins"},
            f"{path}.{name}",
        )
        for field in ("scenario_packages", "plugins"):
            values = profile.get(field, [])
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise catalog.WorkspaceError(f"{path}: {name}.{field} must be a list")
            if len(set(values)) != len(values):
                raise catalog.WorkspaceError(f"{path}: duplicate {field} entries")
        for package in profile.get("scenario_packages", []):
            catalog.validate_path(package, plugin_checkout)
            _safe_directory(plugin_checkout, plugin_checkout / package)
            if not (plugin_checkout / package / "pyproject.toml").is_file():
                raise catalog.WorkspaceError(f"{path}: missing scenario package: {package}")
            engine.package_name(plugin_checkout / package)
        if any(not catalog.NAME.fullmatch(v) for v in profile.get("plugins", [])):
            raise catalog.WorkspaceError(f"{path}: invalid installed plugin name")
        if type(profile.get("hardware", False)) is not bool:
            raise catalog.WorkspaceError(f"{path}: {name}.hardware must be true/false")
        if "tests" in profile:
            if not isinstance(profile["tests"], list) or not all(
                isinstance(t, str) for t in profile["tests"]
            ):
                raise catalog.WorkspaceError(f"{path}: {name}.tests must be a list")
            for item in profile["tests"]:
                catalog.validate_path(item, plugin_checkout)
        if "console" in profile:
            if not isinstance(profile["console"], dict):
                raise catalog.WorkspaceError(f"{path}: {name}.console must be a mapping")
            catalog._keys(profile["console"], {"config"}, f"{path}.{name}.console")
            if "config" in profile["console"]:
                catalog.validate_path(profile["console"]["config"], plugin_checkout)
    return data


def _effective_sources(
    sources: list[dict[str, Any]], plugin: str, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    """Apply plugin-owned revision pins to its declared catalog dependencies."""
    overrides = manifest.get("revisions", {})
    dependencies = {source["name"] for source in sources} - {plugin}
    unexpected = set(overrides) - dependencies
    if unexpected:
        raise catalog.WorkspaceError(
            f"Plugin {plugin} overrides unknown or non-dependency revisions: "
            + ", ".join(sorted(unexpected))
        )
    return [
        {**source, "revision": overrides.get(source["name"], source["revision"])}
        for source in sources
    ]


def _profile(manifest: dict[str, Any], name: str | None) -> dict[str, Any]:
    if name is None:
        return {}
    profiles = manifest.get("profiles", {})
    if name not in profiles:
        raise catalog.WorkspaceError(f"Unknown plugin profile {name!r}")
    return profiles[name]


def _source_digest(sources: list[dict[str, Any]]) -> str:
    return hashlib.sha256(engine.selection_text(sources).encode()).hexdigest()


def _read_state(root: Path, plugin: str) -> dict[str, Any] | None:
    try:
        _safe_directory(root, state_path(root, plugin).parent)
        if state_path(root, plugin).is_symlink():
            return None
        state = json.loads(state_path(root, plugin).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict) or state.get("schema") != PLUGIN_STATE_SCHEMA:
        return None
    if state.get("plugin") != plugin or not isinstance(state.get("env_key"), str):
        return None
    if not re.fullmatch(r"[0-9a-f]{20}", state["env_key"]):
        return None
    if not isinstance(state.get("heads"), dict) or not isinstance(state.get("checkouts"), dict):
        return None
    if not isinstance(state.get("selected"), list) or not isinstance(state.get("env"), str):
        return None
    if not isinstance(state.get("selection_sha256"), str):
        return None
    profile = state.get("profile")
    if profile is not None and (
        not isinstance(profile, str) or not catalog.NAME.fullmatch(profile)
    ):
        return None
    packages = state.get("scenario_packages", [])
    if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
        return None
    if not all(
        isinstance(name, str) and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha)
        for name, sha in state["heads"].items()
    ):
        return None
    if not all(
        isinstance(name, str) and isinstance(path, str) for name, path in state["checkouts"].items()
    ):
        return None
    return state


def _state_checkouts(
    root: Path, plugin: str, sources: list[dict[str, Any]], state: dict[str, Any]
) -> dict[str, Path]:
    result = {}
    for source in sources:
        name = source["name"]
        sha = state["heads"].get(name)
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise catalog.WorkspaceError(f"Missing pinned SHA for {name}")
        expected = _worktree_path(root, plugin, name, sha)
        if state["checkouts"].get(name) != str(expected.relative_to(root)):
            raise catalog.WorkspaceError(f"Invalid pinned checkout path for {name}")
        _verify_worktree(root, expected, source, sha)
        result[name] = expected
    return result


def _activate(
    root: Path,
    plugin: str,
    sources: list[dict[str, Any]],
    heads: dict[str, str],
    checkouts: dict[str, Path],
) -> None:
    for source in sources:
        if source["python"]:
            engine.check_package_metadata(checkouts[source["name"]], source)
    manifest = _load_manifest(checkouts[plugin])
    selected = selected_profile(root, plugin)
    profile_data = _profile(manifest, selected)
    extras = [checkouts[plugin] / path for path in profile_data.get("scenario_packages", [])]
    flags = {"AUTOSPAN_EDITABLE": False}
    _safe_directory(root, env_base(root, plugin))
    print(f"Preparing {plugin} / {selected or '(default)'}", flush=True)
    key, env = engine.environment(
        root,
        sources,
        flags,
        heads,
        checkouts=checkouts,
        base=env_base(root, plugin),
        **({"extra_packages": extras} if extras else {}),
    )
    engine.check_plugins(env, profile_data.get("plugins", []))
    state = {
        "schema": PLUGIN_STATE_SCHEMA,
        "plugin": plugin,
        "profile": selected,
        "scenario_packages": profile_data.get("scenario_packages", []),
        "selection_sha256": _source_digest(sources),
        "selected": [source["name"] for source in sources],
        "heads": heads,
        "checkouts": {name: str(path.relative_to(root)) for name, path in checkouts.items()},
        "env_key": key,
        "env": str(env),
        "distributions": [
            source["python"]["distribution"] for source in sources if source["python"]
        ]
        + [engine.package_name(package) for package in extras],
    }
    # Commit state only after sources, metadata, installation and pip check succeeded.
    _safe_directory(root, state_path(root, plugin).parent)
    engine.atomic_json(state_path(root, plugin), state)
    print(f"Prepared {plugin}: {env}")


def bootstrap(root: Path, name: str) -> None:
    with engine.operation_lock(root):
        doc = read_catalog(root)
        plugin = resolve_plugin(doc, name)
        catalog_sources = plugin_sources(doc, plugin)
        plugin_source = next(p for p in catalog_sources if p["name"] == plugin)
        # Resolve the plugin first: its own pinned metadata may specify distinct
        # dependency revisions for this project. No developer checkout is moved.
        plugin_head = _resolve_heads(root, [plugin_source])
        plugin_checkout = _ensure_worktrees(root, plugin, [plugin_source], plugin_head)[plugin]
        manifest = _load_manifest(plugin_checkout)
        sources = _effective_sources(catalog_sources, plugin, manifest)
        dependencies = [source for source in sources if source["name"] != plugin]
        heads = {**_resolve_heads(root, dependencies), **plugin_head}
        checkouts = _ensure_worktrees(root, plugin, sources, heads)
        _activate(root, plugin, sources, heads, checkouts)


def install(root: Path, name: str) -> None:
    """Reinstall from existing pinned sources without fetching or changing Git HEAD."""
    with engine.operation_lock(root):
        plugin, sources, state, checkouts = _current(root, name)
        _activate(root, plugin, sources, state["heads"], checkouts)


def _current(
    root: Path, name: str
) -> tuple[str, list[dict[str, Any]], dict[str, Any], dict[str, Path]]:
    doc = read_catalog(root)
    plugin = resolve_plugin(doc, name)
    catalog_sources = plugin_sources(doc, plugin)
    state = _read_state(root, plugin)
    if state is None:
        raise catalog.WorkspaceError(
            f"Plugin {plugin} not prepared; run bootstrap --plugin {plugin}"
        )
    plugin_source = next(p for p in catalog_sources if p["name"] == plugin)
    plugin_sha = state["heads"].get(plugin)
    if not isinstance(plugin_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", plugin_sha):
        raise catalog.WorkspaceError(f"Plugin {plugin} pinned commit missing")
    plugin_checkout = _worktree_path(root, plugin, plugin, plugin_sha)
    _verify_worktree(root, plugin_checkout, plugin_source, plugin_sha)
    sources = _effective_sources(catalog_sources, plugin, _load_manifest(plugin_checkout))
    if state["selection_sha256"] != _source_digest(sources) or state["selected"] != [
        p["name"] for p in sources
    ]:
        raise catalog.WorkspaceError(
            f"Plugin {plugin} catalog changed; run bootstrap --plugin {plugin}"
        )
    if set(state["heads"]) != {p["name"] for p in sources}:
        raise catalog.WorkspaceError(f"Plugin {plugin} source selection changed")
    checkouts = _state_checkouts(root, plugin, sources, state)
    return plugin, sources, state, checkouts


def inspect(root: Path, name: str, *, profile: str | None = None) -> list[str]:
    try:
        plugin, sources, state, checkouts = _current(root, name)
        env = Path(state["env"])
        base = env_base(root, plugin)
        _safe_directory(root, base)
        if base.is_symlink() or env.is_symlink() or env.parent != base:
            return ["Active environment path is outside the plugin environment directory"]
        if env.name != state["env_key"] and not env.name.startswith(state["env_key"] + "-"):
            return ["Active environment name does not match its fingerprint"]
        manifest = _load_manifest(checkouts[plugin])
        profile_name = state.get("profile")
        profile_data = _profile(manifest, profile_name)
        if state.get("scenario_packages", []) != profile_data.get("scenario_packages", []):
            return ["Prepared scenario packages changed; rebootstrap the profile"]
        if (
            profile is None
            and selected_plugin(root) == plugin
            and selected_profile(root, plugin) != profile_name
        ):
            return ["Saved profile changed; run bootstrap to prepare it"]
        extras = [checkouts[plugin] / p for p in profile_data.get("scenario_packages", [])]
        key = engine.env_fingerprint(
            sources,
            state["heads"],
            {"AUTOSPAN_EDITABLE": False},
            root,
            checkouts=checkouts,
            **({"extra_packages": extras} if extras else {}),
        )
        if key != state["env_key"]:
            return ["Pinned source or environment fingerprint changed; reinstall plugin"]
        if not (env / ".ready").is_file() or (env / ".ready").read_text() != key:
            return ["Active plugin environment is not ready"]
        expected = [p["python"]["distribution"] for p in sources if p["python"]]
        expected += [engine.package_name(package) for package in extras]
        if state.get("distributions") != expected:
            return ["Installed distribution selection changed"]
        _load_manifest(checkouts[plugin])
        engine.check_installed(env, expected)
        engine.check_plugins(env, profile_data.get("plugins", []))
        return []
    except (catalog.WorkspaceError, OSError, ValueError, UnicodeError) as exc:
        return [str(exc)]


def doctor(root: Path, name: str) -> bool:
    errors = inspect(root, name)
    if errors:
        print(f"Doctor [{name}]: FAILED")
        for error in errors:
            print(" - " + error)
        return False
    print(f"Doctor [{name}]: OK")
    return True


def status(root: Path, name: str) -> None:
    doc = read_catalog(root)
    plugin = resolve_plugin(doc, name)
    state = _read_state(root, plugin)
    print(f"Plugin: {plugin}")
    print(f"Saved profile: {selected_profile(root, plugin) or '(default)'}")
    print(f"Prepared profile: {state.get('profile') or '(default)' if state else '(not prepared)'}")
    print(f"Environment: {state['env'] if state else '(not prepared)'}")
    if state:
        for component, sha in state["heads"].items():
            print(f"  {component}: {sha[:12]}")
    for error in inspect(root, plugin):
        print("Warning: " + error)


def active(
    root: Path, name: str, *, profile: str | None = None
) -> tuple[Path, Path, dict[str, Any]]:
    errors = inspect(root, name, **({"profile": profile} if profile is not None else {}))
    if errors:
        raise catalog.WorkspaceError("Plugin not ready: " + "; ".join(errors))
    plugin, _, state, checkouts = _current(root, name)
    return Path(state["env"]), checkouts[plugin], _load_manifest(checkouts[plugin])


def _execution_profile(
    root: Path, name: str, manifest: dict[str, Any], profile: str | None
) -> tuple[str | None, dict[str, Any]]:
    profile = profile if profile is not None else selected_profile(root, name)
    data = _profile(manifest, profile)
    state = _read_state(root, resolve_plugin(read_catalog(root), name))
    if state is None:
        raise catalog.WorkspaceError("Plugin not prepared; select a profile and bootstrap first")
    if data.get("scenario_packages", []) != state.get("scenario_packages", []):
        raise catalog.WorkspaceError(
            "Profile uses different scenario packages; select it and bootstrap first"
        )
    return profile, data


def _profile_options(profile_data: dict[str, Any]) -> list[str]:
    names = profile_data.get("plugins", [])
    return ["--set", "autospan.plugins=" + json.dumps(names)] if names else []


def test(
    root: Path,
    name: str,
    paths: list[str],
    options: list[str],
    *,
    profile: str | None = None,
    config: str | None = None,
) -> None:
    env, checkout, manifest = active(
        root, name, **({"profile": profile} if profile is not None else {})
    )
    profile, profile_data = _execution_profile(root, name, manifest, profile)
    if profile_data.get("hardware") and "--hardware" not in options:
        raise catalog.WorkspaceError("Hardware profile requires explicit --hardware gate")
    if "--hardware" in options and not profile_data.get("hardware") and config is None:
        raise catalog.WorkspaceError(
            "Select and bootstrap a hardware profile, or pass CONFIG=bench.local.yaml"
        )
    if config is not None or profile_data.get("hardware") or "--hardware" in options:
        bench = config_path(
            root,
            name,
            checkout,
            manifest,
            profile_data,
            config,
            require_explicit="--hardware" in options,
        )
        print(f"Selected: {name} / {profile or '(default)'}", flush=True)
        # AutoSpan owns collection of installed scenario packages and validates
        # explicit selectors within those packages.
        selectors = paths or profile_data.get("tests", [])
        engine.check_plugins(env, profile_data.get("plugins", []))
        command = [
            str(engine.python_path(env)),
            "-m",
            "autospan",
            "test",
            "--config",
            str(bench),
            *_profile_options(profile_data),
            "--",
            *selectors,
            *options,
        ]
        if "--hardware" in options and not any(
            flag in options for flag in ("--collect-only", "--collectonly", "--co")
        ):
            # Exact same selectors and options; no runtime acquisition in collection.
            print("Collecting before hardware execution", flush=True)
            engine.run_hosted_tests(
                [*command, "--collect-only"],
                cwd=checkout,
                env=engine.execution_environment(env),
            )
        engine.run_hosted_tests(
            command,
            cwd=checkout,
            env=engine.execution_environment(env),
        )
        return
    doc = read_catalog(root)
    plugin = resolve_plugin(doc, name)
    selected = plugin_sources(doc, plugin)
    plugin_source = next(p for p in selected if p["name"] == plugin)
    suites = paths or profile_data.get("tests", manifest.get("tests", plugin_source["tests"]))
    if not suites:
        raise catalog.WorkspaceError(f"Plugin {plugin} has no configured tests")
    if not paths:
        for suite in suites:
            catalog.validate_path(suite, checkout)
            if not (checkout / suite).exists():
                raise catalog.WorkspaceError(f"Plugin test path missing: {checkout / suite}")
    engine.run_pytest(
        [
            str(engine.python_path(env)),
            "-m",
            "pytest",
            "--import-mode=importlib",
            *suites,
            *options,
        ],
        cwd=checkout,
        env=engine.execution_environment(env),
    )


def exec_command(root: Path, name: str, arguments: list[str]) -> None:
    if not arguments:
        raise catalog.WorkspaceError("Pass a command after --")
    env, checkout, _ = active(root, name)
    process_env = engine.execution_environment(env)
    executable = shutil.which(arguments[0], path=process_env["PATH"])
    engine.run([executable or arguments[0], *arguments[1:]], cwd=checkout, env=process_env)


def config_path(
    root: Path,
    name: str,
    checkout: Path,
    manifest: dict[str, Any],
    profile_data: dict[str, Any],
    config: str | None,
    *,
    require_explicit: bool = False,
) -> Path:
    """Resolve explicit and profile-owned benches without guessing hardware."""
    configured = config or profile_data.get("console", {}).get("config")
    if require_explicit and not configured:
        raise catalog.WorkspaceError(
            "Missing hardware bench configuration: select a prepared profile with "
            "console.config, or pass CONFIG=path/to/bench.local.yaml"
        )
    configured = configured or manifest.get("console", {}).get("config", "autospan.fake.yaml")
    path = Path(configured)
    if not path.is_absolute():
        # Plugin-owned tracked configs resolve from the pinned source. Explicit
        # local overrides can also live in the ordinary developer checkout or
        # be addressed relative to the workspace root.
        plugin = resolve_plugin(read_catalog(root), name)
        source = next(p for p in plugin_sources(read_catalog(root), plugin) if p["name"] == plugin)
        candidates = [checkout / path]
        if config is not None or profile_data.get("console", {}).get("config"):
            candidates.extend([engine.checkout_path(root, source) / path, root / path])
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    if not path.is_file():
        raise catalog.WorkspaceError(f"Bench configuration missing: {path}")
    return path


def console(
    root: Path, name: str, config: str | None, arguments: list[str], *, profile: str | None = None
) -> None:
    env, checkout, manifest = active(
        root, name, **({"profile": profile} if profile is not None else {})
    )
    profile, profile_data = _execution_profile(root, name, manifest, profile)
    if profile_data.get("hardware") and "--hardware" not in arguments:
        arguments = ["--hardware", *arguments]
    if "--hardware" in arguments and not profile_data.get("hardware") and config is None:
        raise catalog.WorkspaceError(
            "Select and bootstrap a hardware profile, or pass CONFIG=bench.local.yaml"
        )
    path = config_path(
        root,
        name,
        checkout,
        manifest,
        profile_data,
        config,
        require_explicit="--hardware" in arguments,
    )
    engine.check_plugins(env, profile_data.get("plugins", []))
    process_env = engine.execution_environment(env)
    engine.run(
        [
            str(engine.python_path(env)),
            "-m",
            "autospan",
            "console",
            "--config",
            str(path),
            *_profile_options(profile_data),
            *arguments,
        ],
        cwd=checkout,
        env=process_env,
    )


def freeze(root: Path, name: str) -> Path:
    """Audit snapshot, NOT a portable lockfile."""
    with engine.operation_lock(root):
        plugin, sources, state, _ = _current(root, name)
        if not doctor(root, plugin):
            raise catalog.WorkspaceError("Cannot freeze an unhealthy plugin")
        dest = root / ".generated" / "plugins" / plugin / "snapshot"
        _safe_directory(root, dest)
        dest.mkdir(parents=True, exist_ok=True)
        source_catalog = catalog.read_catalog(root / "repos.local.yaml", root)
        frozen = {
            p["name"]: {
                **source_catalog["repositories"][p["name"]],
                "revision": state["heads"][p["name"]],
            }
            for p in sources
        }
        (dest / "sources-frozen.yaml").write_text(
            catalog.yaml.safe_dump({"schema": 1, "repositories": frozen}, sort_keys=False),
            encoding="utf8",
        )
        engine.atomic_json(dest / "state.json", state)
        env = Path(state["env"])
        (dest / "requirements-frozen.txt").write_text(
            engine.run(
                [str(engine.python_path(env)), "-m", "pip", "freeze"],
                cwd=root,
                output=True,
            )
            + "\n",
            encoding="utf8",
        )
        print(f"Wrote plugin audit snapshot: {dest} (not a portable lockfile)")
        return dest
