from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import IO, Any

from .config import ConfigError, HprConfig


_ADAPTER_RE = re.compile(r"^(hci\d+):")
_ADDRESS_RE = re.compile(r"BD Address:\s*([0-9A-F:]{17})", re.IGNORECASE)


async def acquire_connection_slot(adapter: str) -> IO[bytes]:
    """Serialise BLE discovery/GATT setup on one controller across services."""
    import fcntl

    handle = Path(f"/tmp/hpr-ble-connect-{adapter}.lock").open("a+b")
    await asyncio.to_thread(fcntl.flock, handle.fileno(), fcntl.LOCK_EX)
    return handle


def release_connection_slot(handle: IO[bytes] | None) -> None:
    if handle is None:
        return
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def controller_map() -> dict[str, str]:
    """Return Bluetooth controller addresses keyed by Linux hci name."""
    try:
        output = subprocess.check_output(
            ["hciconfig", "-a"], text=True, stderr=subprocess.STDOUT
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ConfigError(f"unable to enumerate Bluetooth controllers: {exc}") from exc

    result: dict[str, str] = {}
    current: str | None = None
    for raw in output.splitlines():
        adapter_match = _ADAPTER_RE.match(raw.strip())
        if adapter_match:
            current = adapter_match.group(1)
        address_match = _ADDRESS_RE.search(raw)
        if current and address_match:
            result[current] = address_match.group(1).upper()
    return result


def resolve_bluetooth_role(
    config: HprConfig,
    role: str,
    *,
    legacy_adapter_path: str | None = None,
    default_adapter: str = "hci0",
) -> str:
    role_cfg: dict[str, Any] = config.get(f"bluetooth.roles.{role}", {}) or {}
    configured_address = str(role_cfg.get("controller_address") or "").upper()
    configured_adapter = str(role_cfg.get("fallback_adapter") or "")
    if not configured_adapter and legacy_adapter_path:
        configured_adapter = str(config.get(legacy_adapter_path, "") or "")
    configured_adapter = configured_adapter or default_adapter

    controllers = controller_map()
    if configured_address:
        for adapter, address in controllers.items():
            if address == configured_address:
                return adapter
        raise ConfigError(
            f"Bluetooth role {role!r} requires controller {configured_address}, "
            f"but enumerated controllers are {controllers or 'none'}"
        )

    if configured_adapter not in controllers:
        raise ConfigError(
            f"Bluetooth role {role!r} fallback {configured_adapter!r} is not enumerated"
        )
    return configured_adapter

