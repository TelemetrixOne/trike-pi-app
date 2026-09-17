from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from hpr_gateway.config import config_arg_parser, load_config


def read_command(*args: str) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unavailable"


def main() -> int:
    parser = config_arg_parser("Record Raspberry Pi power and temperature health")
    args = parser.parse_args()
    config = load_config(args.config)
    interval = float(config.get("services.power_watch.interval_seconds", 60))
    log_path = Path(config.get("services.power_watch.log_path", "/var/log/hpr/power-watch.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        line = (
            f"{datetime.now(timezone.utc).isoformat()} "
            f"{read_command('vcgencmd', 'get_throttled')} "
            f"{read_command('vcgencmd', 'measure_temp')}\n"
        )
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

