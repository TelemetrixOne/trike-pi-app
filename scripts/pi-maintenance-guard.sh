#!/usr/bin/env bash
# Sourced before operator script work. No effect on live service entrypoints.
HPR_MAINTENANCE_HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pi-maintenance-check.py"
HPR_MAINTENANCE_STATE=/var/lib/hpr/maintenance
[[ "$(id -u)" == 0 ]] || { echo 'Maintenance refused: use sudo.' >&2; exit 75; }
[[ ! -L "$HPR_MAINTENANCE_STATE" ]] || exit 75
mkdir -p -m 0700 "$HPR_MAINTENANCE_STATE"
[[ "$(stat -c '%u:%a' "$HPR_MAINTENANCE_STATE")" == 0:700 ]] || exit 75
if [[ "$(readlink /proc/$$/fd/199 2>/dev/null || true)" != "$HPR_MAINTENANCE_STATE/operation.lock" ]]; then
  [[ ! -L "$HPR_MAINTENANCE_STATE/operation.lock" ]] || exit 75
  exec 199>"$HPR_MAINTENANCE_STATE/operation.lock"
fi
flock -n 199 || { echo 'Maintenance refused: another local operation holds the lock.' >&2; exit 75; }
hpr_maintenance_check() {
  /usr/bin/python3 "$HPR_MAINTENANCE_HELPER" || exit 75
}
hpr_maintenance_check
# Recheck immediately before external disruptive commands, including embedded
# Python writers and package/build commands. Do not kill an in-flight restore.
hpr_maintenance_boundary() {
  case "$1" in
    *systemctl*|*apt-get*|*'apt '*|*python*|*'pip '*|*'cp '*|*'mv '*|*'rm '*|*'install '*|*'tee '*|*'sed '*|*'tar '*|*'unzip '*|*'rsync '*|*' > '*|*'>>'*)
      hpr_maintenance_check ;;
  esac
}
# Inherit DEBUG into shell functions/subshells too (rollback helpers included).
set -T
trap 'hpr_maintenance_boundary "$BASH_COMMAND"' DEBUG
