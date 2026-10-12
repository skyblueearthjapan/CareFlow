import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from relaykit import Rig  # noqa: E402


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)
