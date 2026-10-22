# Record and repeat a run

The example catalog uses `v1.0.0` tags for AutoSpan and StructWire. BLE follows
`master`, so fetching it later can select different source code. To keep the same
sources, put full commit SHAs in the selected
repositories' `revision` fields in `repos.local.yaml`.

After preparing the workspace, record its sources and installed packages:

```sh
python scripts/ws.py freeze
python scripts/ws.py exec -- python --version
python scripts/ws.py exec -- python -m pip check
```

`freeze` prints the directory containing the source catalog and package inventory.
Keep that directory with the Workspace commit, operating system, selected profile,
test command, configuration and run evidence. Keep private bench identities and
captured data out of public reports.

The saved source catalog records exact Git commits. The package inventory records
what pip installed, but it is not a portable installation lock: local paths and
platform-specific packages may appear in it, and a new install can resolve
different dependency versions. Retain the required wheels and a complete set of
versioned, hashed requirements when an installation must use exactly the same
packages.

When comparing results, check both source revisions and package versions. Use the
same configuration and test selection. Software tests and fake scenarios check
software behavior; physical results also depend on the devices, firmware and bench
setup. Preserve that information alongside the hardware evidence.
