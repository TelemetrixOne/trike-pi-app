#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <full-platform-archive.zip> <rendered-trike-config.yaml>" >&2
  exit 2
fi

ARCHIVE="$(realpath "$1")"
RENDERED_CONFIG="$(realpath "$2")"

if [ ! -f "${ARCHIVE}" ]; then
  echo "Archive not found: ${ARCHIVE}" >&2
  exit 1
fi
if [ ! -f "${RENDERED_CONFIG}" ]; then
  echo "Rendered Pi config not found: ${RENDERED_CONFIG}" >&2
  exit 1
fi

STAGE_DIR="$(mktemp -d "${HOME}/hpr-gps-uplift.XXXXXX")"
BACKUP_DIR="/opt/hpr/deployment-backups/gps-uplift-$(date -u +%Y%m%dT%H%M%SZ)"

sha256sum "${ARCHIVE}"
unzip -q "${ARCHIVE}" -d "${STAGE_DIR}"

INSTALLER="${STAGE_DIR}/scripts/install-pi-gateway.sh"
VALIDATOR="${STAGE_DIR}/scripts/validate-pi-gateway.sh"
if [ ! -f "${INSTALLER}" ] || [ ! -f "${VALIDATOR}" ]; then
  echo "Archive does not contain the Pi installer and validator." >&2
  exit 1
fi

sudo install -d -o root -g root -m 750 "${BACKUP_DIR}"
for source in \
  /etc/systemd/system/hpr-gps.service \
  /etc/systemd/system/hpr-gps-hotplug.service \
  /etc/udev/rules.d/99-hpr-gps-hotplug.rules \
  /etc/default/gpsd \
  /etc/hpr/hpr.yaml; do
  if [ -e "${source}" ]; then
    sudo cp -a "${source}" "${BACKUP_DIR}/"
  fi
done

sudo install -d -o root -g admin -m 750 /etc/hpr
sudo install -o root -g admin -m 640 "${RENDERED_CONFIG}" /etc/hpr/hpr.yaml
sudo env \
  HPR_CONFIG_PATH=/etc/hpr/hpr.yaml \
  HPR_INSTALL_SCOPE=gps \
  bash "${INSTALLER}"

sudo systemctl restart hpr-gps.service
sudo bash "${VALIDATOR}"

echo "Pi GPS uplift deployed."
echo "Staging directory: ${STAGE_DIR}"
echo "Backup directory: ${BACKUP_DIR}"
