from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hpr_gateway.bluetooth_power import ensure_powered, unblock_adapter
from hpr_gateway.config import ConfigError


class BluetoothPowerTests(unittest.TestCase):
    def test_unblock_adapter_targets_matching_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for number, name, soft in ((0, "hci0", "0\n"), (1, "hci1", "1\n")):
                entry = root / f"rfkill{number}"
                entry.mkdir()
                (entry / "type").write_text("bluetooth\n", encoding="ascii")
                (entry / "name").write_text(f"{name}\n", encoding="ascii")
                (entry / "soft").write_text(soft, encoding="ascii")

            unblock_adapter("hci1", root)

            self.assertEqual((root / "rfkill0" / "soft").read_text(), "0\n")
            self.assertEqual((root / "rfkill1" / "soft").read_text(), "0\n")

    def test_unblock_adapter_fails_when_role_is_not_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ConfigError):
                unblock_adapter("hci1", Path(temporary))

    @patch("subprocess.run")
    def test_ensure_powered_starts_selected_controller(self, run) -> None:
        ensure_powered("hci2")
        run.assert_called_once_with(
            ["hciconfig", "hci2", "up"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )


if __name__ == "__main__":
    unittest.main()
