from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class GpsHotplugInstallTests(unittest.TestCase):
    def test_hotplug_waits_for_stable_device_and_restarts_full_pipeline(self):
        installer = (ROOT / "scripts" / "install-pi-gateway.sh").read_text(encoding="utf-8")
        self.assertIn("while [ ! -e \"\\$GPS_DEVICE\" ]", installer)
        self.assertIn("systemctl restart gpsd.service; systemctl restart hpr-gps.service", installer)


if __name__ == "__main__":
    unittest.main()
