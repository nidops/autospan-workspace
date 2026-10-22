# Workspace architecture

## Scope and ownership

Workspace prepares sources and Python environments for AutoSpan projects. AutoSpan
and its plugins can also be used directly, without Workspace.

| Owner | Responsibility |
| --- | --- |
| Workspace catalog | Repository URLs, revisions, source dependencies, package extras and checkout test paths |
| Plugin metadata | Named profiles, scenario packages, enabled plugins and bench configuration paths |
| Workspace | Source preparation, environment selection, command dispatch and report assembly |
| AutoSpan | Runtime lifecycle, resource ownership, gates, scenario execution and evidence |
| Project plugins | Drivers, protocols, configuration and scenarios |

The same Workspace commands apply to each project. BLE supplies one example
plugin; its device settings and hardware behavior stay in the BLE repository.

## Catalog and selection

The catalog has `schema: 1` and a nonempty `repositories` mapping. Each repository
requires `url` and `revision`. Optional fields are `path`, `default`, `depends`,
`python`, `plugin` and `tests`. Checkout paths are relative to `repos/` and default
to the repository name. A repository without `python` is source-only.

`python.distribution` names the installed distribution. `python.features` maps
selection names to package extras and their defaults. Pip resolves Python package
dependencies. Catalog `depends` entries select additional Git source repositories.

`select --plugin NAME --profile NAME` saves a project and an optional profile.
Preparation includes the project's source dependencies. The plugin's optional
`autospan-workspace.yaml` declares its profiles and scenario packages; see
[plugin environments](PLUGIN_WORKFLOW.md) for the fields and precedence rules.

The catalog rejects duplicate YAML keys, unknown fields, unsafe or overlapping
paths, missing or cyclic dependencies, duplicate distribution names and Kconfig
symbol collisions. Package metadata is checked before installation.

## Source operations

Workspace has two source workflows:

| Operation | Selected plugin | Global editable selection |
| --- | --- | --- |
| Selection | `select --plugin NAME`, optionally with a profile | `deselect`, then Kconfig component and extra selection |
| `bootstrap` / `sync` | Fetch and resolve catalog revisions, then prepare pinned worktrees and an environment | Fetch and resolve all selected revisions, then update clean ordinary checkouts and prepare an environment |
| `install` | Reuse existing pinned worktrees without fetching | Use current ordinary checkout HEADs and local edits without fetching |
| `doctor` | Verify pinned sources, selection and environment | Verify selection, checkout HEADs, package metadata and environment; local source edits are allowed |

Ordinary developer checkouts live at `repos/<component>/`. Plugin preparation
creates separate pinned worktrees at
`repos/.isolated/<plugin>/<component>/<commit>/`; it leaves existing developer
checkouts in place. Uncommitted changes in the ordinary checkout do not appear
in the pinned environment. Pinned worktrees must remain clean.

The editable workflow uses the ordinary checkouts directly. Its Kconfig menu
selects components and extras; generated symbols use `COMP_<NAME>` and
`FEAT_<NAME>_<FEATURE>`. See [editable development](ADVANCED_DEVELOPMENT.md) when
working on local package sources.

Deselection leaves source checkouts and environments on disk. A directory's
presence does not mean that its package is active.

## Environment preparation and activation

`.tools` holds Workspace's Python tools. Plugin environments live under
`.envs/<plugin>/`; global editable environments live directly under `.envs/`.
Their fingerprints include source commits, package metadata, selected extras,
interpreter, platform, workspace location and tool requirements.

Environments are created at permanent paths. A cached environment is reused only
after its packages and dependencies pass validation. New state is activated after
installation and checks succeed. Plugin state lives in
`.generated/plugins/<plugin>/state.json`; global editable state lives in
`.generated/state.json`. Operating-system file locks prevent concurrent workspace
mutations and are released when the process exits.

A failed preparation leaves the previous environment selected. Fetched objects
and newly created worktrees may remain on disk. In editable mode, source changes
are not rolled back, so the previous environment can still see changes to those
ordinary checkouts.

## Using and checking the environment

`exec` uses the active environment's `PATH` and `VIRTUAL_ENV`, removes ambient
`PYTHONPATH` and `PYTHONHOME`, and disables user-site packages.

`test` without a bench configuration runs the project's checkout test suites.
With a configuration or hardware profile, it delegates to AutoSpan, which
collects scenarios from installed, enabled plugins. Workspace passes the selected
profile, configuration and test selectors through to the host.

`hw-list` only collects scenarios. `hw` collects the same selection before
execution and passes the hardware gate. AutoSpan owns acquisition and cleanup.
Firmware maintenance requires its separate configuration and authorization.

Workspace combines finalized AutoSpan evidence with pytest's HTML summary for
hosted runs. Collection leaves the previous report untouched. `doctor` checks
source and environment consistency; device health is checked by the plugin.

## Reproducibility boundary

Full commit SHAs fix Git inputs. Python dependencies still resolve at install
time. `freeze` records sources and installed packages; see
[recording and repeating a run](REPRODUCING_RUNS.md) for what to keep with results.
