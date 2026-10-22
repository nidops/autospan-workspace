#!/bin/sh
# Run from a fresh Workspace checkout. Requires POSIX sh, Python 3.11+ and Git.
set -eu

if [ ! -f scripts/ws.py ] || [ ! -f repos.example.yaml ]; then
    echo 'Run this script at the autospan-workspace repository root.' >&2
    exit 2
fi
if [ -e repos.local.yaml ] || [ -e .generated ] || [ -e .config ]; then
    echo 'Use a fresh Workspace checkout without local catalog or selection state.' >&2
    exit 2
fi

WORKSPACE_ROOT=$(pwd -P)
DEMO_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/autospan-starter.XXXXXXXX")
printf 'Demo source and generator environment: %s\n' "$DEMO_ROOT"
python3 scripts/ws.py tools
python3 -m venv "$DEMO_ROOT/framework"
DEMO_PY="$DEMO_ROOT/framework/bin/python"
"$DEMO_PY" -m pip install 'autospan[test]==1.0.0'
"$DEMO_PY" -m autospan new sample --dir "$DEMO_ROOT"
SOURCE="$DEMO_ROOT/autospan-sample"

cat > "$SOURCE/.gitignore" <<'IGNORE'
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
artifacts/
build/
dist/
.venv/
IGNORE
git -C "$SOURCE" init -b main
git -C "$SOURCE" add .
git -C "$SOURCE" -c user.name='Workspace Demo' -c user.email='workspace-demo@example.invalid' \
    commit -m 'Create sample AutoSpan plugin'

"$WORKSPACE_ROOT/.tools/bin/python" - "$SOURCE" <<'PY'
from pathlib import Path
import subprocess
import sys
import yaml

source = Path(sys.argv[1]).resolve()
sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
catalog = {"schema": 1, "repositories": {"autospan-sample": {
    "plugin": True, "url": str(source), "revision": sha, "default": False,
    "python": {"distribution": "autospan-sample"}, "tests": ["tests"],
}}}
Path("repos.local.yaml").write_text(yaml.safe_dump(catalog, sort_keys=False), encoding="utf8")
PY

python3 scripts/ws.py plugins
python3 scripts/ws.py select --plugin sample
python3 scripts/ws.py bootstrap
python3 scripts/ws.py doctor
python3 scripts/ws.py exec -- python -m autospan plugins
python3 scripts/ws.py exec -- python -m autospan plan --config autospan.yaml
python3 scripts/ws.py test -- -q
python3 scripts/ws.py console --config autospan.yaml -- --plain --exec 'sample status'
python3 scripts/ws.py test --config autospan.yaml -- -q
printf '\nSample source: %s\nWorkspace: %s\n' "$SOURCE" "$WORKSPACE_ROOT"
printf 'Both directories are retained for inspection; the local catalog records the source path.\n'
