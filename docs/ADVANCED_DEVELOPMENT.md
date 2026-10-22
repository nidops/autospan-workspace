# Advanced editable development

The normal Workspace workflow uses pinned committed Git sources. Use the
optional ordinary-clone/Kconfig mode when actively editing local package sources.

```sh
make deselect
make menuconfig
make bootstrap
make doctor
make test
```

On Windows, or without GNU Make, use `python scripts/ws.py` followed by the
same command name. Run `python scripts/ws.py tools` before `menuconfig` to
install its dependencies. On Linux and macOS, use `python3` if needed.

Choose components and extras, then use `make bootstrap` to fetch and prepare
sources for a fresh selection. Bootstrap may change checkout HEADs and refuses
dirty checkouts.

For existing developer checkouts, use `make install` to prepare the environment
from current sources without fetching or moving HEADs. Editable installs can
observe uncommitted changes. Run `make install` again after changing package
metadata or checkout revisions.

Avoid `sync` during active development: it moves clean checkouts to configured
revisions and refuses dirty ones.

Return to pinned execution with
`make select PLUGIN=NAME PROFILE=NAME` and `make bootstrap`.

This workflow is deliberately independent of saved pinned plugin profiles.
Do not edit generated `.isolated` worktrees.
