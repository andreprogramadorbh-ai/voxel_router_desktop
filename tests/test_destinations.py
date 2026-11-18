from __future__ import annotations

import socket
import sqlite3

import pytest
from pynetdicom import AE, evt
from pynetdicom.sop_class import Verification

from app.cloud.connectors import CloudConnectorError, DicomCloudConnector
from app.core.database import Database

OUTBOUND_AE = "CLINICA_GH"
VOXEL_AE = "VOXEL_CELL"


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


@pytest.fixture
def voxel_cell():
    """SCP sintético no papel da célula VOXEL: só aceita o Calling AE cadastrado (OUTBOUND_AE)."""
    calling_aes: list[str] = []

    def on_echo(event):
        calling_aes.append(str(event.assoc.requestor.ae_title).strip())
        return 0x0000

    ae = AE(ae_title=VOXEL_AE)
    ae.require_calling_aet = [OUTBOUND_AE]
    ae.add_supported_context(Verification)
    port = available_port()
    server = ae.start_server(("127.0.0.1", port), block=False, evt_handlers=[(evt.EVT_C_ECHO, on_echo)])
    yield port, calling_aes
    server.shutdown()


def destination(port: int, **overrides) -> dict:
    return {"name": "VOXEL Célula", "kind": "DICOM", "ae_title": VOXEL_AE, "host": "127.0.0.1", "port": port, **overrides}


def row(database, destination_id: int):
    return database.query_one("SELECT * FROM destinations WHERE id = ?", (destination_id,))


def audit_results(database, action: str) -> list[str]:
    return [item["result"] for item in database.query_all("SELECT result FROM audit_logs WHERE action = ? ORDER BY id", (action,))]


@pytest.mark.parametrize("value", ["", "   ", "A" * 17, "AE\\X", "AE\x01"])
def test_outbound_ae_is_validated(client, value):
    response = client.patch("/api/settings/dicom", json={"values": {"outbound_ae_title": value}})
    assert response.status_code == 422


def test_outbound_ae_is_separate_from_scp_ae(client, settings):
    response = client.patch("/api/settings/dicom", json={"values": {"outbound_ae_title": f" {OUTBOUND_AE} "}})

    assert response.status_code == 200
    assert settings.get("dicom", "outbound_ae_title") == OUTBOUND_AE
    assert settings.get("dicom", "ae_title") == "VOXEL_ROUTER"
    assert client.get("/api/settings/dicom").json()["outbound_ae_title"] == OUTBOUND_AE


def test_new_destination_starts_paused_and_cannot_be_enabled_without_echo(client, database, voxel_cell):
    port, _ = voxel_cell
    client.patch("/api/settings/dicom", json={"values": {"outbound_ae_title": OUTBOUND_AE}})

    destination_id = client.post("/api/destinations", json={**destination(port), "enabled": True}).json()["id"]

    assert row(database, destination_id)["enabled"] == 0
    listed = client.get("/api/destinations").json()[0]
    assert (listed["enabled"], listed["echo_status"], listed["echo_valid"]) == (0, None, False)
    assert client.post(f"/api/destinations/{destination_id}/enable").status_code == 409
    assert row(database, destination_id)["enabled"] == 0
    assert audit_results(database, "ENABLE_DESTINATION") == ["DENIED"]


def test_echo_uses_outbound_ae_records_result_and_unlocks_enable(client, database, voxel_cell):
    port, calling_aes = voxel_cell
    client.patch("/api/settings/dicom", json={"values": {"outbound_ae_title": OUTBOUND_AE}})
    destination_id = client.post("/api/destinations", json=destination(port)).json()["id"]

    result = client.post(f"/api/destinations/{destination_id}/echo").json()

    assert calling_aes == [OUTBOUND_AE]  # nunca o AE local do SCP
    assert (result["echo_status"], result["echo_error"], result["echo_valid"]) == ("SUCCESS", None, True)
    assert result["echo_at"]
    assert result["enabled"] == 0  # C-ECHO não habilita sozinho
    assert client.post(f"/api/destinations/{destination_id}/enable").json()["enabled"] == 1
    assert audit_results(database, "TEST_DESTINATION_ECHO") == ["SUCCESS"]
    assert audit_results(database, "ENABLE_DESTINATION") == ["SUCCESS"]


def test_echo_rejected_by_voxel_records_failure(client, database, voxel_cell):
    port, _ = voxel_cell
    client.patch("/api/settings/dicom", json={"values": {"outbound_ae_title": "AE_ERRADO"}})
    destination_id = client.post("/api/destinations", json=destination(port)).json()["id"]

    result = client.post(f"/api/destinations/{destination_id}/echo").json()

    assert (result["echo_status"], result["echo_error"], result["echo_valid"]) == ("FAILED", "DESTINATION_REJECTED", False)
    assert result["echo_at"]
    assert client.post(f"/api/destinations/{destination_id}/enable").status_code == 409
    assert audit_results(database, "TEST_DESTINATION_ECHO") == ["FAILURE"]


def test_echo_to_closed_port_records_unavailable(client):
    client.patch("/api/settings/dicom", json={"values": {"outbound_ae_title": OUTBOUND_AE}})
    destination_id = client.post("/api/destinations", json=destination(available_port())).json()["id"]

    result = client.post(f"/api/destinations/{destination_id}/echo").json()

    assert (result["echo_status"], result["echo_error"]) == ("FAILED", "DESTINATION_UNAVAILABLE")


def test_echo_without_outbound_ae_fails_clearly(client, voxel_cell):
    port, calling_aes = voxel_cell
    destination_id = client.post("/api/destinations", json=destination(port)).json()["id"]

    result = client.post(f"/api/destinations/{destination_id}/echo").json()

    assert (result["echo_status"], result["echo_error"]) == ("FAILED", "OUTBOUND_AE_NOT_CONFIGURED")
    assert calling_aes == []


def enabled_destination(client, port: int) -> int:
    client.patch("/api/settings/dicom", json={"values": {"outbound_ae_title": OUTBOUND_AE}})
    destination_id = client.post("/api/destinations", json=destination(port)).json()["id"]
    client.post(f"/api/destinations/{destination_id}/echo")
    assert client.post(f"/api/destinations/{destination_id}/enable").status_code == 200
    return destination_id


@pytest.mark.parametrize("change", [{"host": "127.0.0.2"}, {"port": 11112}, {"ae_title": "OUTRA_CELULA"}, {"tls_enabled": True}])
def test_changing_destination_identity_requires_new_echo(client, database, voxel_cell, change):
    port, _ = voxel_cell
    destination_id = enabled_destination(client, port)

    assert client.put(f"/api/destinations/{destination_id}", json={**destination(port), **change}).status_code == 204

    current = row(database, destination_id)
    assert (current["enabled"], current["echo_status"], current["echo_target"]) == (0, None, None)
    assert client.post(f"/api/destinations/{destination_id}/enable").status_code == 409
    details = database.query_one("SELECT details FROM audit_logs WHERE action = 'UPDATE_DESTINATION'")["details"]
    assert "enabled=1" in details and "enabled=0" in details


def test_changing_only_name_or_priority_keeps_destination_enabled(client, database, voxel_cell):
    port, _ = voxel_cell
    destination_id = enabled_destination(client, port)

    client.put(f"/api/destinations/{destination_id}", json={**destination(port), "name": "VOXEL Produção", "priority": 2})

    current = row(database, destination_id)
    assert (current["enabled"], current["echo_status"]) == (1, "SUCCESS")


def test_changing_outbound_ae_pauses_destinations_until_new_echo(client, database, voxel_cell):
    port, _ = voxel_cell
    destination_id = enabled_destination(client, port)

    client.patch("/api/settings/dicom", json={"values": {"outbound_ae_title": "CLINICA_NOVA"}})

    assert row(database, destination_id)["enabled"] == 0
    assert client.post(f"/api/destinations/{destination_id}/enable").status_code == 409
    details = database.query_one("SELECT details FROM audit_logs WHERE details LIKE 'outbound_ae:%' ORDER BY id DESC")["details"]
    assert details == "outbound_ae:CLINICA_GH->CLINICA_NOVA;destinos_pausados=1"


async def test_connector_refuses_enabled_destination_without_matching_echo(database, paths):
    """Segunda barreira: AE de saída alterado direto no router.json não envia sem novo C-ECHO."""
    with database.transaction() as connection:
        destination_id = connection.execute(
            "INSERT INTO destinations(name, kind, ae_title, host, port, enabled, echo_status, echo_target) VALUES ('V', 'DICOM', ?, '127.0.0.1', 104, 1, 'SUCCESS', ?)",
            (VOXEL_AE, f"{OUTBOUND_AE}|{VOXEL_AE}|127.0.0.1|104|0"),
        ).lastrowid

    await DicomCloudConnector(database, paths.storage, destination_id, OUTBOUND_AE).connect()
    with pytest.raises(CloudConnectorError) as raised:
        await DicomCloudConnector(database, paths.storage, destination_id, "OUTRO_AE").connect()

    assert str(raised.value) == "DESTINATION_NOT_VERIFIED"
    assert raised.value.permanent is False


def test_migration_adds_echo_columns_and_pauses_legacy_destinations(paths):
    legacy = Database(paths)
    legacy.paths.ensure()
    with sqlite3.connect(legacy.path) as connection:
        connection.execute(
            "CREATE TABLE destinations (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, ae_title TEXT, host TEXT, port INTEGER, endpoint TEXT,"
            " tls_enabled INTEGER NOT NULL DEFAULT 0, priority INTEGER NOT NULL DEFAULT 3, enabled INTEGER NOT NULL DEFAULT 1,"
            " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute("INSERT INTO destinations(name, kind, ae_title, host, port, enabled) VALUES ('Antigo', 'DICOM', 'X', '10.0.0.1', 104, 1)")

    legacy.initialize()
    assert legacy.query_one("SELECT enabled, echo_status FROM destinations")["enabled"] == 0
    assert legacy.query_one("SELECT 1 FROM schema_migrations WHERE version = 5") is not None

    with legacy.transaction() as connection:
        connection.execute("UPDATE destinations SET enabled = 1")
    legacy.initialize()  # idempotente: não pausa de novo
    assert legacy.query_one("SELECT enabled FROM destinations")["enabled"] == 1
