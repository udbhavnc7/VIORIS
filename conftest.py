# Makes the repo root importable so `packages`, `apps`, `services` resolve.
# Pytest inserts this file's directory (the repo root) into sys.path.

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
