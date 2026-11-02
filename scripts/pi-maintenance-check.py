#!/usr/bin/env python3
"""Internal operator guard. Not a live service or background polling task."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hpr_gateway.maintenance import guarded_main

if __name__ == '__main__':
    raise SystemExit(guarded_main(lambda: 0))
