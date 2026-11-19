from pathlib import Path

import pytest

from fleet_copilot.actions import Actions
from fleet_copilot.analytics import Analytics
from fleet_copilot.domain import Principal
from fleet_copilot.store import Store

DATASET = Path(__file__).resolve().parents[1] / "data/raw/device-telemetry-dataset.ndjson"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    s.ingest(DATASET)
    return s


@pytest.fixture
def analytics(store):
    return Analytics(store)


@pytest.fixture
def actions(store, analytics):
    return Actions(store, analytics)


@pytest.fixture
def operator():
    return Principal(company_id="acme-001", actor_id="operator", role="operator")


@pytest.fixture
def admin():
    return Principal(company_id="acme-001", actor_id="approver", role="admin")
