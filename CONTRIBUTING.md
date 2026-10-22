# Contributing

Install the workspace tools and Git hook once per checkout:

```sh
make pre-commit-install
```

Format code and run the workspace checks:

```sh
make format
make pre-commit
make test-workspace
make lint
```

`make format` runs Ruff's formatter on `aworkspace`, `scripts`, and `tests`.
The Git hook runs [Ruff lint fixes followed by formatting](https://docs.astral.sh/ruff/integrations/#pre-commit)
on staged Python files in those directories. If it changes files, review and stage
the changes before committing again. `make pre-commit` runs the same hooks on all
tracked workspace Python files; CI also runs these hooks.

Keep the Ruff version in `.pre-commit-config.yaml` aligned with
`requirements-tools.txt`. The first hook run downloads its isolated environment.

PowerShell:

```powershell
python scripts/ws.py tools
.tools\Scripts\python.exe -m pre_commit install
.tools\Scripts\python.exe -m ruff format aworkspace scripts tests
.tools\Scripts\python.exe -m pre_commit run --all-files
.tools\Scripts\python.exe -m pytest -q tests
.tools\Scripts\python.exe -m ruff check aworkspace scripts tests
.tools\Scripts\python.exe -m ruff format --check aworkspace scripts tests
.tools\Scripts\python.exe -m compileall -q aworkspace scripts tests
```

Enable the Git/pip integrations explicitly when running all manager checks:

```sh
AUTO_WORKSPACE_E2E=1 make test-workspace
```

In PowerShell, set `$env:AUTO_WORKSPACE_E2E = '1'` before running pytest.

The suite uses temporary Git repositories to exercise revision isolation, dirty
checkout protection, dependency selection, command routing, hardware gates and
failure rollback. The opted-in integration also installs real packages with pip.
CI runs these checks on Linux, Windows and macOS with Python 3.11 through 3.14.
The real integration also generates two projects using AutoSpan 1.0.0, installs
their declared Git composition, and checks both contributions and failure cleanup.

The separate BLE reference matrix runs on all three operating systems with
Python 3.11. It checks out the branch named in
`repos.example.yaml`, then resolves that checkout to a concrete SHA for its
single-run integration. The example tracks moving `master`, record the resolved
SHA when comparing runs. It runs bootstrap, doctor, software tests, fake scenarios,
console and cross-repository documentation checks.
The public BLE repository requires no additional access token for the reference
checkout. Private repositories may require a read-only `AUTOSPAN_READ_TOKEN`
Actions secret. Do not store tokens in local catalogs.
To run against an accessible remote locally:

```sh
AUTO_WORKSPACE_BLE_E2E=1 .tools/bin/python -m pytest -q tests/test_reference_ble.py
```

For an authenticated clean checkout at the intended branch head, also set
`AUTO_WORKSPACE_BLE_SOURCE=/path/to/clean/checkout`. Cross-repository links can
be checked separately with
`python scripts/check_links.py --repository nidops/autospan-ble=/path/to/checkout`.
The BLE repository's installation verifier covers its separately installed
acceptance package. The reference job asserts it is absent from ordinary bootstrap,
then prepares the `lab` profile and collects its scenarios using an explicit fake
configuration without acquiring physical devices.

For integration changes, bootstrap a representative plugin with its declared
dependencies, run its software suite, and check plugin discovery and console use.
For physical qualification, follow the selected plugin's contributor and bench guides.
Inspect the run verdict, artifact hashes and teardown; software fixtures do not
establish RF behavior. Firmware programming remains separately authorized.

Keep repositories, local catalogs, hardware identities, vendor tools and generated
artifacts outside workspace Git. Do not embed customer schemas, credentials or
private bench configurations in examples. Public templates should use placeholders.

Workspace configuration must remain independent of library runtime APIs. Package
dependencies belong in each package's pyproject.toml; the workspace catalog selects
repositories and extras. Preserve both pinned execution and editable development.
