import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def weigh_data_payload():
    return json.loads((FIXTURES / "getWeighingDataV2.json").read_text())
