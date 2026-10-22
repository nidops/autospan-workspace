# Getting started

Workspace fetches Git projects, prepares Python environments and dispatches
commands. AutoSpan supplies execution, resource ownership and evidence. Each
plugin supplies its own dependencies, configuration, drivers and scenarios.
No plugin is selected by default; BLE is an optional reference example.

## Prepare Workspace

Workspace targets Linux, Windows and macOS. Install Python 3.11+ with `venv`
support and Git. GNU Make is optional. The commands below work on Linux and
macOS and require access to the Workspace repository:

```sh
git clone https://github.com/nidops/autospan-workspace.git
cd autospan-workspace
python3 scripts/ws.py tools
cp repos.example.yaml repos.local.yaml
python3 scripts/ws.py plugins
```

PowerShell uses `python scripts/ws.py tools` and
`Copy-Item repos.example.yaml repos.local.yaml`. Use `python` instead of
`python3` in the remaining CLI commands. Workspace chooses `Scripts/python.exe`
for its Windows environments; no shell activation is needed.

Every Workspace operation is available through `python scripts/ws.py` without
Make or a Unix shell. For example, after selecting and bootstrapping a plugin:

| Make shortcut | Python CLI (use `python3` on Linux/macOS) |
| --- | --- |
| `make console` | `python scripts/ws.py console` |
| `make test` | `python scripts/ws.py test -- --html=artifacts/report.html --self-contained-html` |
| `make hw-list` | `python scripts/ws.py test -- --hardware -v --collect-only` |
| `make hw` | `python scripts/ws.py test -- --hardware -v --html=artifacts/report.html --self-contained-html` |
| `make menuconfig` | `python scripts/ws.py menuconfig` |

`tools` installs the Windows curses dependency used by `menuconfig`.
Interactive menus and the AutoSpan TUI need an interactive terminal. Hardware
support depends on the selected plugin's drivers and external tools; for example,
BLE's passive Nordic sniffer backend requires POSIX FIFOs.

`plugins` lists available projects; it does not install them. Keep private URLs
and local choices in the ignored `repos.local.yaml`.

## Add and select a Git project

Add your repository under `repositories:` in the local catalog. Replace the URL,
revision and distribution below with the values for a repository you can access:

```yaml
  project-checks:
    plugin: true
    url: https://github.com/YOUR_ORG/project-checks.git
    revision: REPLACE_WITH_FULL_COMMIT_SHA
    default: false
    python:
      distribution: project-checks
      features:
        tests: {extra: test, default: true}
    tests: [tests]
```

Only include `features.tests` if the package declares a `test` extra. Local Git
repository paths are also supported. A full commit SHA fixes the source input;
branches and tags are accepted but can move.

```sh
python3 scripts/ws.py select --plugin project-checks
python3 scripts/ws.py bootstrap
python3 scripts/ws.py doctor
python3 scripts/ws.py test -- -q
```

`bootstrap` needs access to the selected Git repository and its dependencies.
It installs the plugin from Git and resolves Python requirements from its
`pyproject.toml`, normally through PyPI. AutoSpan plugins need not be published
on PyPI. `doctor` checks source and environment consistency, not physical devices.

## Create a plugin

AutoSpan 1.0.0 provides the canonical starter: `autospan new sample`. It generates
a complete project with configuration, tests, installed scenarios and console
commands. Workspace has no separate generator.
See AutoSpan's [plugin authoring guide](https://github.com/nidops/autospan/blob/master/docs/PLUGIN_AUTHORING.md)
for the API contracts behind the generated project.

For a copy-and-run POSIX demonstration, use a **fresh Workspace clone**, before
creating `repos.local.yaml` or other selection state:

```sh
git clone https://github.com/nidops/autospan-workspace.git sample-workspace
cd sample-workspace
sh scripts/starter-smoke.sh
```

The [script](../scripts/starter-smoke.sh) installs `autospan[test]==1.0.0` into
a temporary generator environment, runs `autospan new sample`, commits the
generated source to a local Git repository, and writes a catalog pinned to that
commit. It then runs discovery, bootstrap, doctor, plugin listing, plan, ordinary
pytest, console and hosted scenarios. BLE is neither installed nor selected.
The printed source directory and Workspace state remain available afterward.
Keep the source directory while using the catalog; `sync` fetches from it.

On Windows, generate the same project with PowerShell in a separate directory:

```powershell
python -m venv framework
.\framework\Scripts\python.exe -m pip install 'autospan[test]==1.0.0'
.\framework\Scripts\python.exe -m autospan new sample
git -C autospan-sample init -b main
git -C autospan-sample add .
git -C autospan-sample commit -m 'Create sample AutoSpan plugin'
git -C autospan-sample rev-parse HEAD
```

Git needs your normal author identity configured. Do this before installing the
sample so generated build files are not committed. In Workspace's local catalog,
add `autospan-sample` with `plugin: true`, the absolute source path as `url`
(for example `C:/projects/autospan-sample`), the printed SHA as `revision`,
`python: {distribution: autospan-sample}` and `tests: [tests]`. Select `sample`
and follow the bootstrap/doctor/test commands above with that name.

## Run installed scenarios and the console

The sample uses `autospan.yaml`. For another plugin, use its documented config:

```sh
python3 scripts/ws.py exec -- python -m autospan plugins
python3 scripts/ws.py exec -- python -m autospan plan --config autospan.yaml
python3 scripts/ws.py test --config autospan.yaml -- -q
python3 scripts/ws.py console --config autospan.yaml -- --plain
```

`test` without a config runs the catalog's checkout software tests using pytest.
With `--config`, AutoSpan collects scenarios from installed, enabled plugins.
A checkout merely present on disk cannot contribute installed scenarios.
The console uses an explicit config or the plugin's optional Workspace metadata.

| Selection | Meaning |
| --- | --- |
| Catalog `depends` | Additional Git projects installed from their declared revisions |
| `pyproject.toml` dependencies | Python requirements resolved by pip |
| Workspace `select` | Project used by subsequent Workspace commands |
| Installed `autospan.plugins` entry point | Plugin available to AutoSpan discovery |
| Config `autospan.plugins` | Plugins activated for this AutoSpan run |
| Separate scenario distribution | Must also be installed and activated to contribute tests |

A plugin-owned `autospan-workspace.yaml` profile can declare nested scenario
distributions under `scenario_packages`. Select the profile and bootstrap once;
Workspace installs them into the same managed, fingerprinted environment.
Never install directly into managed environments by hand. For hardware scenarios
use `make hw-list` (collection only) and `make hw` after preparing the profile.

## Optional BLE example

To try AutoSpan BLE, use the supplied catalog entry:

```sh
python3 scripts/ws.py select --plugin ble
python3 scripts/ws.py bootstrap
python3 scripts/ws.py doctor
python3 scripts/ws.py test -- -q
python3 scripts/ws.py console -- --plain --exec 'ble status'
python3 scripts/ws.py test --config autospan.fake.yaml -- -q
```

These run software and fake scenarios. The catalog's BLE `master` reference
contains `autospan-workspace.yaml`; use a full commit SHA in `repos.local.yaml`
when you need to repeat a run with the same source revision.
For the optional BLE physical acceptance profile, use
`make select PLUGIN=ble PROFILE=lab` and `make bootstrap`;
then `make hw-list`, `make hw-list TEST=name`, `make hw`, or
`make hw TEST=name` use the selected profile and managed scenario packages.
Configure `bench.local.yaml` locally before these commands. Follow the
[BLE bench guide](https://github.com/nidops/autospan-ble/blob/master/docs/BENCH_BRINGUP.md)
at the selected revision. Hardware selection never silently falls back to fake
configurations. Ordinary pinned execution ignores uncommitted developer edits.

## Preserve and diagnose state

| Symptom | Next step |
| --- | --- |
| Missing local catalog | Copy `repos.example.yaml` and edit it |
| Unknown plugin | Run `plugins`; verify `plugin: true` and the selected name |
| Clone or fetch failure | Check repository URL, access and revision |
| Environment not prepared | Run `bootstrap` for the selected plugin |
| Missing bench configuration | Supply `--config` or plugin-owned metadata |
| No hosted scenarios | Check installed distributions, entry points and config activation |
| Dirty pinned worktree | Preserve edits externally; use ordinary clones for development |
| Package/HEAD drift in editable mode | Run `install`, then `doctor` |

Failed environment preparation leaves the previous environment active. Preserve
the terminal error, local catalog and active state while diagnosing it. `status`
shows source selection; `freeze` records source and package inventory for audit.
It is not a portable lockfile: pip still resolves dependencies during installation.

Use the [editable development workflow](ADVANCED_DEVELOPMENT.md)
for uncommitted source changes. Previous editable environments can see changed
source files even if a later install fails. The
[plugin workflow](PLUGIN_WORKFLOW.md) explains revision overrides, dependency
closure and profile/config precedence; [architecture](ARCHITECTURE.md) describes
environment activation and its limits.
