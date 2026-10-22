# Plugin environments

Each selected plugin gets pinned source worktrees and a Python environment.
For editable development in the ordinary clones, use the global Kconfig
workflow described in the [advanced development guide](ADVANCED_DEVELOPMENT.md).

## Ownership and source of truth

| Data | Owner |
| --- | --- |
| Git URL, requested revision, workspace path, repository dependencies | `repos.local.yaml` |
| Python package requirements | Plugin's `pyproject.toml` |
| Software test paths, hosted selectors, console benches, named profiles | Plugin's `autospan-workspace.yaml` |
| Selected default plugin and profile | `.generated/active-plugin.json` (ignored) |
| Resolved Git commits and environment identity | `.generated/plugins/<plugin>/state.json` |
| Source code and Git history | `repos/<component>/` |
| Pinned per-plugin source view | `repos/.isolated/<plugin>/<component>/<full-sha>/` |
| Python packages for one plugin | `.envs/<plugin>/<environment>/` |
| Hardware permissions, runtime lifecycle and evidence | AutoSpan |

New environment directory names use `<fingerprint>-<suffix>`; existing names
without a suffix remain supported. The plugin state records the exact active path.

The example catalog lists optional repositories but does not select a plugin.
Pip resolves AutoSpan and any other runtime dependencies from selected packages.
Add catalog `depends` only for source repositories that should be checked out
alongside a plugin; Python dependencies remain package-owned.

All managed Git repositories and worktrees live under `repos/` inside the
workspace. The ordinary checkouts are for developer work. The pinned worktrees
are generated Git worktrees and must not be edited.

## Selection and preparation

`plugins` lists catalog entries named `autospan-*`, except `autospan` itself.
A catalog entry can override inference with `plugin: true` or `plugin: false`.
`select --plugin PLUGIN_NAME --profile PROFILE_NAME` records the local default
plugin and profile; it does not clone or install anything. Omit `--profile` to
clear a saved profile, or use `deselect` to clear both choices. Profile names are
validated against pinned plugin metadata during preparation. Explicit `--plugin`
selects another project's environment without inheriting the saved profile.

`bootstrap --plugin PLUGIN_NAME` prepares the selected project:

1. Validate the catalog and resolve the plugin plus its transitive `depends`.
2. Fetch and pin the plugin checkout first, without moving developer checkouts.
3. Read its optional `revisions` overrides for declared repository dependencies.
4. Fetch dependencies and resolve their effective revisions to full commits.
5. Create or reuse detached worktrees under
   `repos/.isolated/<plugin>/<component>/<full-sha>/`.
6. Verify pinned worktrees are clean and match their expected origins/commits.
7. Validate plugin metadata and Python package distribution/extras.
8. Create or reuse a plugin-scoped Python environment, run `pip check`.
9. Atomically activate that plugin's new state **last**.

If preparation fails, the previous plugin state remains. Already-fetched Git
objects and newly created pinned worktrees may remain for later reuse; they do
not alter the previous pinned sources. A workspace-wide operation lock
serializes mutations.

`install --plugin` uses the already pinned worktrees and never fetches or
changes their HEADs. `sync --plugin` has the same prepare-from-catalog behavior
as plugin `bootstrap`. Neither operation alters another plugin's state.

`doctor --plugin` checks catalog selection, pinned worktree origin, SHA,
cleanliness, environment identity, readiness, distribution presence and
`pip check`. It does not verify physical hardware availability.

## Plugin metadata

`autospan-workspace.yaml` is optional and lives **inside each plugin Git repo**.
The schema is intentionally small:

```yaml
schema: 1
tests: [tests]
console:
  config: benches/fake.yaml
profiles:
  fake:
    tests: [tests/unit]
    console: {config: benches/fake.yaml}
  lab:
    console: {config: benches/lab.yaml}
    hardware: true
    scenario_packages: [tests]
    plugins: [example, example-acceptance]
revisions:
  autospan: 0123456789abcdef0123456789abcdef01234567
```

Unknown fields, duplicate YAML keys, unsafe relative paths and malformed
profiles are rejected. For software runs, profile tests override the top-level
test paths. Profile console config overrides the top-level bench; CLI `--config`
overrides both. Relative explicit configs can also resolve in the ordinary
developer checkout or workspace root for private bench settings.
If metadata is absent, the catalog's `tests` paths and the conventional
plugin-local `autospan.fake.yaml` console fallback apply. For new plugins,
prefer explicit metadata or a `--config` override.

`revisions` can override the catalog Git ref for any repository listed in
the plugin's transitive `depends`, but cannot override the plugin's own ref or
introduce undeclared dependencies. This enables simultaneous plugin
projects with different revisions of shared dependencies.

`--hardware` is mandatory when testing a `hardware: true` profile; `make hw` and
`make hw-list` supply it. The console adds it for a selected hardware profile.
Hardware profiles run tests through AutoSpan with the
profile bench configuration; an explicit `--config` can select a private bench. AutoSpan remains responsible for hardware
authorization and runtime safety.

Hosted runs collect scenarios contributed by installed plugins selected in the
bench configuration. A selected Workspace project, an installed AutoSpan entry
point and a plugin enabled by `autospan.plugins` are separate choices. Catalog
`depends` selects Git source dependencies at their declared revisions; package
requirements still belong in `pyproject.toml`.

A profile may declare `scenario_packages: [tests]` to install a separately
packaged scenario distribution from a subdirectory of the pinned plugin checkout.
Workspace installs that distribution into the same managed environment and includes
its packaging metadata and identity in the environment fingerprint. Set
`plugins: [example, example-acceptance]` in the profile when both registered plugin
entry points must be enabled during hosted execution. The list represents the
complete enabled plugin set for that profile. Profile-specific dependencies are
prepared during `bootstrap`, not installed by `hw` or by hand. Switching profiles
that change the scenario package set requires selecting and bootstrapping again.

Top-level catalog and manifest test paths apply to software runs.
Explicit test paths, or a profile's `tests` entries in hosted mode, select within
the installed scenarios; AutoSpan validates these selectors and controls pytest's
collection boundaries.

## CLI contract

```text
python scripts/ws.py plugins
python scripts/ws.py select --plugin PLUGIN_NAME --profile PROFILE_NAME
python scripts/ws.py deselect
python scripts/ws.py bootstrap --plugin PLUGIN_NAME
python scripts/ws.py install --plugin PLUGIN_NAME
python scripts/ws.py sync --plugin PLUGIN_NAME
python scripts/ws.py doctor --plugin PLUGIN_NAME
python scripts/ws.py status --plugin PLUGIN_NAME
python scripts/ws.py test --plugin PLUGIN_NAME -- -q
python scripts/ws.py console --plugin PLUGIN_NAME -- --plain
python scripts/ws.py exec --plugin PLUGIN_NAME -- autospan plugins
python scripts/ws.py freeze --plugin PLUGIN_NAME
```

Use `--profile NAME` only when the selected plugin declares that profile in
`autospan-workspace.yaml`.

The `Makefile` forwards `PLUGIN=`, `PROFILE=`, `CONFIG=`, `TEST_PATH=`,
`PYTEST_ARGS=`, `CONSOLE_ARGS=` and `COMMAND=` to the Python CLI. `TEST=` adds a
pytest `-k` filter. `make hw` collects the selected scenarios before enabling their
execution; `make hw-list` performs the same selection with `--collect-only`.
Both reuse the selected, prepared profile's bench by default. Explicit
`CONFIG=` and `PROFILE=` overrides remain available. Without a valid hardware
profile or explicit bench, hardware commands fail before execution. `hw-list`
collects the same scenario selection as `hw`, without opening devices. A provided
`TEST=` selector also sees unmarked scenarios because no implicit `-m hardware`
filter is applied. Destructive/maintenance operations remain separately gated.
Hosted tests load fixtures from the installed scenario packages.
`--plugin` must
appear before any positional test path or the `--` separator.

## Execution boundaries

Pinned worktrees and their environments contain committed source. To run local
edits, use the [editable development guide](ADVANCED_DEVELOPMENT.md). Do not edit generated
worktrees or install packages into a prepared environment by hand.

Pip resolves transitive dependencies at installation. Source snapshots do not
provide a portable dependency lock. Unused environments and worktrees are retained;
Git worktrees must be removed with Git's worktree commands when no active state
references them.

A workspace lock serializes preparation operations. Test executions are not
serialized across processes: run one physical owner per bench. Local bench
configuration is supplied explicitly and remains outside tracked source.
