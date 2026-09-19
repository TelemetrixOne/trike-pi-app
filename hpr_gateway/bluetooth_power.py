from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from .bluetooth import resolve_bluetooth_role
from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config


RFKILL_ROOT = Path("/sys/class/rfkill")


def unblock_adapter(adapter: str, rfkill_root: Path = RFKILL_ROOT) -> None:
    matches = []
    for entry in rfkill_root.glob("rfkill*"):
        try:
            if (entry / "type").read_text(encoding="ascii").strip() != "bluetooth":
                continue
            if (entry / "name").read_text(encoding="ascii").strip() == adapter:
                matches.append(entry)
        except OSError:
            continue

    if len(matches) != 1:
        raise ConfigError(
            f"expected one rfkill entry for {adapter}, found {len(matches)}"
        )
    (matches[0] / "soft").write_text("0\n", encoding="ascii")


def ensure_powered(adapter: str) -> None:
    subprocess.run(
        ["hciconfig", adapter, "up"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare an HPR Bluetooth role")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--role", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    adapter = resolve_bluetooth_role(config, args.role)
    unblock_adapter(adapter)
    ensure_powered(adapter)


if __name__ == "__main__":
    main()
