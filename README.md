# AutoSpan Workspace

Develop and run AutoSpan projects from a shared workspace. Define repositories
in a catalog, prepare isolated Python environments, and use a common set of
commands for tests, consoles, and reports across projects.

Workspace manages source revisions, environments, and command dispatch. AutoSpan
provides test execution, resource management, and evidence. Plugins supply the
drivers, protocols, and scenarios for each application domain.

AutoSpan is installed from PyPI; project plugins come from Git repositories.
No plugin is required or selected by default. Start with the
[new-user guide](docs/GETTING_STARTED.md) for the complete workflow.

Workspace targets Linux, Windows and macOS. It requires Python 3.11+, Git,
venv support and access to the selected repositories. The CI matrix covers
all three operating systems with Python 3.11 through 3.14.
The Python CLI is available on every platform; GNU Make provides optional shortcuts.
Hardware drivers and external tools have their own platform requirements.

## Quickstart

Clone the workspace, create a local catalog, and prepare the tools:

```sh
git clone https://github.com/nidops/autospan-workspace.git
cd autospan-workspace
cp repos.example.yaml repos.local.yaml
python3 scripts/ws.py tools
python3 scripts/ws.py plugins
```

On Windows, use `python` and your shell's copy command.

Choose a plugin from the catalog, or add your project's repository to
`repos.local.yaml` with `plugin: true`, its URL, revision, and Python distribution
name. Replace `PLUGIN_NAME` below with its catalog name:

```sh
python3 scripts/ws.py select --plugin PLUGIN_NAME
# If the project defines a bench profile, select it once instead:
# python3 scripts/ws.py select --plugin PLUGIN_NAME --profile PROFILE_NAME
python3 scripts/ws.py bootstrap
python3 scripts/ws.py doctor
python3 scripts/ws.py test -- -q
```

`select` stores the plugin and profile once. `bootstrap` prepares their complete
pinned environment, including the profile's separately packaged scenario projects.
Routine `make hw-list`, `make hw`, `make test` and `make console` reuse this context.
Use `PLUGIN=`, `PROFILE=` or `CONFIG=` only to explicitly override a saved choice.
Pinned execution uses committed Git revisions; uncommitted ordinary-clone changes
are deliberately invisible.
Pip resolves Python dependencies from the plugin's package metadata. Catalog
`depends` entries select additional source repositories to include in that environment.

## Create a plugin

Use AutoSpan 1.0.0's `autospan new sample` to generate a runnable plugin.
The [starter walkthrough](docs/GETTING_STARTED.md#create-a-plugin) installs the
framework, commits the generated project and runs it through Workspace
without BLE. Workspace manages that source; AutoSpan supplies the scaffold.

## Tests, hardware and reports

A selected plugin supplies its own test paths and, optionally, a
`autospan-workspace.yaml` with console configuration and named profiles.
`make test` runs the checkout's configured software tests. With `CONFIG=`, it
runs scenarios contributed by installed plugins through AutoSpan. `make console`
starts the AutoSpan console. Without a selected plugin, console use requires
an explicit `CONFIG=` and an environment with AutoSpan installed.

Select and prepare a plugin-owned hardware profile once, with its private bench
configuration in `bench.local.yaml`:

```sh
make select PLUGIN=PLUGIN_NAME PROFILE=lab
make bootstrap
make doctor
make hw-list
make hw-list TEST=example_case
make hw
make hw TEST=example_case
```

If no selected profile supplies a bench, pass `CONFIG=...` explicitly.
Missing hardware configuration fails before executing tests; no fake fallback.

`hw-list` only collects. `hw` forwards `--hardware` without imposing a marker filter;
explicitly selected ordinary scenarios are not silently excluded.
AutoSpan owns gates, resources, execution and evidence. These commands require
the scenario distributions to be installed and activated in the selected environment.
The profile's `scenario_packages` declares nested distributions such as `tests/`;
bootstrap installs them alongside the plugin in its managed environment. The
profile's `plugins` list enables the required AutoSpan entry points for tests and
the console. Select and bootstrap again when changing scenario package sets.
`TEST_PATH=` selects tests within those installed scenarios for hosted runs.
`TEST=` uses pytest `-k`; `PYTEST_ARGS=` forwards pytest options.

`make test` and `make hw` request a self-contained HTML summary at
`artifacts/report.html` in the executing checkout. Hosted runs enrich this
report with finalized AutoSpan step evidence and artifact links. Set `REPORT=`
to retain a report at another location. The HTML can be moved independently,
but evidence links require the referenced files to accompany it.
A collection-only command does not replace the previous HTML report.
The final report is printed as a clickable `file://` URI after evidence is finalized.

See [plugin workflow](docs/PLUGIN_WORKFLOW.md) for the command interface,
[architecture](docs/ARCHITECTURE.md) for source isolation, and
[security reporting](SECURITY.md) for responsible vulnerability disclosure.

## Sources and environments

```text
repos/                      Independent Git clones for development
repos/.isolated/<plugin>/    Detached worktrees pinned to resolved commits
.envs/<plugin>/              Installed plugin environments
.generated/plugins/         Active state and optional source snapshots
.tools/                     Workspace tooling
repos.local.yaml            Private repository catalog
*.local.yaml                Private bench configuration
```

Plugin commands use pinned worktrees, preserving edits and branches in the ordinary
clones. `sync` fetches the configured revisions and prepares a replacement environment;
`install` reuses the already pinned sources without fetching. Failed preparation
leaves the previous environment active. `doctor` rejects modified pinned worktrees.

Use full commit hashes in the local catalog for repeatable source selection.
`make freeze` records source hashes and installed packages; it is an inventory,
not a portable dependency lock. Pip still resolves Python dependencies at installation.
To return to the same sources and inspect what was installed, see
[recording and repeating a run](docs/REPRODUCING_RUNS.md).
Unused environments and worktrees are retained.

## Advanced editable development

Pinned environments execute committed Git revisions, not local uncommitted edits.
Use the [advanced development guide](docs/ADVANCED_DEVELOPMENT.md) for the
optional ordinary-clone and Kconfig workflow.

## Example: BLE

BLE demonstrates the same catalog, environment and test commands used by other
AutoSpan plugins. Its device configuration and scenarios belong to the BLE repository.

To try the workspace with [AutoSpan BLE](https://github.com/nidops/autospan-ble/tree/master),
select its optional catalog entry using the `ble` alias. The example catalog tracks
`master`; use a full Git SHA when you need to keep the source revision fixed:

```sh
make select PLUGIN=ble
make bootstrap
make doctor
make test
make console CONSOLE_ARGS="--plain --exec 'ble status'"
```

This example runs the plugin's software suite and opens its simulated bench.
For device configuration and physical acceptance, follow the plugin's
[console guide](https://github.com/nidops/autospan-ble/blob/master/docs/CONSOLE.md)
and [bench guide](https://github.com/nidops/autospan-ble/blob/master/docs/BENCH_BRINGUP.md).
The BLE `lab` profile installs the separate `autospan-ble-acceptance` distribution
from `tests/`, providing the `ble-acceptance` plugin in Workspace's managed
environment. Configure `bench.local.yaml`, then
run `make select PLUGIN=ble PROFILE=lab`, `make bootstrap`, and `make hw-list`
without additional plugin-enable flags. `make hw-list` does not acquire hardware.
A normal BLE bootstrap without `lab` intentionally excludes `ble-acceptance`.
For editable development, `make preset PROFILE=ble`
loads the supplied BLE component selection after `make deselect`.

See [plugin configuration](docs/PLUGIN_WORKFLOW.md),
[architecture](docs/ARCHITECTURE.md), and [contributing](CONTRIBUTING.md).
