#!/usr/bin/env bash
# Install the helper only; the operator explicitly starts/stops a trial afterward.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=/opt/hpr/gateway/bin/hpr-video-stream-trial.py
[[ ! -e "$TARGET" ]] || { echo 'Existing trial helper; inspect before replacing' >&2; exit 1; }
python3 - <<'PY'
import hashlib
from pathlib import Path
p = Path('/opt/hpr/gateway/runtime/video/hpr-video-native.py')
assert hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == '7b5afebda9327bf3beaf08bfcd1b08e8ce97979c377b6b9b6728a6be4356c8fb', 'Unexpected native viewer; refusing deployment'
PY
/opt/hpr/gateway/venv/bin/python -m py_compile "$REPO_ROOT/bin/hpr-video-stream-trial.py"
install -o root -g root -m 0755 "$REPO_ROOT/bin/hpr-video-stream-trial.py" "$TARGET"
echo 'Trial helper installed. No service restart or settings change yet.'
