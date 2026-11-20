import socket

import pytest
from fastapi.testclient import TestClient

from fleet_copilot.api import create_app
from fleet_copilot.domain import FleetError, Principal


def test_frontend_shell_and_assets_without_data_leak(store, operator):
    client = TestClient(create_app(store, {"a" * 40: operator}))
    page = client.get("/")
    assert page.status_code == 200
    assert "Your fleet, explained." in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["referrer-policy"] == "no-referrer"
    assert "emp-acme" not in page.text
    assert "OPENAI_API_KEY" not in page.text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/styles.css").status_code == 200
    assert client.get("/assets/../../../.env.local").status_code == 404
    assert client.get("/v1/context").status_code == 401


def test_context_is_derived_from_credential_and_ignores_query_scope(store, operator):
    other = Principal(company_id="globex-002", actor_id="viewer")
    client = TestClient(create_app(store, {"a" * 40: operator, "b" * 40: other}))
    acme = client.get("/v1/context?company_id=globex-002", headers={"Authorization": "Bearer " + "a" * 40})
    assert acme.json()["company_id"] == "acme-001"
    assert acme.json()["devices"] == 10
    assert acme.json()["role"] == "operator"
    assert acme.headers["cache-control"] == "no-store"
    globex = client.get("/v1/context", headers={"Authorization": "Bearer " + "b" * 40})
    assert globex.json()["devices"] == 8
    assert globex.json()["role"] == "viewer"


def test_second_launcher_does_not_replace_existing_session(tmp_path, monkeypatch):
    from fleet_copilot.ui import launch

    monkeypatch.chdir(tmp_path)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        access_file = runtime / f"ui-access-{port}.json"
        access_file.write_text('{"url":"existing-session"}')
        with pytest.raises(FleetError, match="unavailable"):
            launch("acme-001", "admin", port, open_browser=False)
        assert access_file.read_text() == '{"url":"existing-session"}'
