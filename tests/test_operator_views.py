"""Ajustes de operação: fila entregue, IPs da LAN, card VOXEL CLOUD, destinos só DICOM e bind_host."""

from __future__ import annotations

import json
import socket
from collections import namedtuple
from pathlib import Path

import pytest

from app.cloud.connectors import echo_target
from app.core.engine import RouterEngine
from app.dicom.ingest import DicomIngestor
from app.dicom.scp import DicomScp
from app.monitoring import network
from app.queue.manager import QueueManager, QueueStateError
from tests.test_engine_runtime import available_port
from tests.test_queue import queued_item

PROJECT_ROOT = Path(__file__).resolve().parents[1]
Address = namedtuple("Address", "family address netmask broadcast ptp")
Stats = namedtuple("Stats", "isup")


def deliver(database, queue: QueueManager, queue_id: int) -> None:
    item = queue.claim_next()
    assert item is not None and item.id == queue_id
    queue.complete(item, 10, 1)


def audit_results(database, action: str) -> list[str]:
    return [row["result"] for row in database.query_all("SELECT result FROM audit_logs WHERE action = ? ORDER BY id", (action,))]


# 1) Fila: item entregue não pode ser pausado nem retomado (retomar reenviaria o estudo).


def test_sent_and_validated_items_cannot_be_paused_or_resumed(database, paths):
    queue = QueueManager(database)
    queue_id = queued_item(database, paths, queue)
    deliver(database, queue, queue_id)

    for action in (queue.pause, queue.resume):
        with pytest.raises(QueueStateError):
            action(queue_id)
    row = database.query_one("SELECT q.status, s.status AS study_status FROM queue q JOIN studies s ON s.id = q.study_id")
    assert (row["status"], row["study_status"]) == ("SENT", "VALIDATED")

    # Estudo já VALIDATED também bloqueia, mesmo que o item de fila tenha outro estado.
    with database.transaction() as connection:
        connection.execute("UPDATE queue SET status = 'RETRY' WHERE id = ?", (queue_id,))
    with pytest.raises(QueueStateError):
        queue.pause(queue_id)


def test_pending_items_still_pause_and_resume_only_from_paused(database, paths):
    queue = QueueManager(database)
    queue_id = queued_item(database, paths, queue)

    with pytest.raises(QueueStateError):
        queue.resume(queue_id)
    queue.pause(queue_id)
    queue.resume(queue_id)
    assert database.query_one("SELECT status FROM queue")["status"] == "QUEUED"
    with pytest.raises(KeyError):
        queue.pause(9999)


def test_queue_api_refuses_pause_resume_of_delivered_item(client, database, paths):
    queue = QueueManager(database)
    queue_id = queued_item(database, paths, queue)
    deliver(database, queue, queue_id)

    assert client.post(f"/api/queue/{queue_id}/pause").status_code == 409
    assert client.post(f"/api/queue/{queue_id}/resume").status_code == 409
    assert client.post("/api/queue/9999/pause").status_code == 404
    assert audit_results(database, "PAUSE_QUEUE") == ["DENIED"]
    assert audit_results(database, "RESUME_QUEUE") == ["DENIED"]
    # A UI usa study_status para esconder PAUSAR/RETOMAR.
    assert client.get("/api/queue").json()[0]["study_status"] == "VALIDATED"


# 2) IPs da rede local para o operador: sem loopback, APIPA, VPN/virtual nem 0.0.0.0.


@pytest.fixture
def fake_interfaces(monkeypatch):
    inet = socket.AF_INET
    addrs = {
        "Ethernet": [Address(inet, "192.168.0.10", "255.255.255.0", None, None), Address(inet, "169.254.10.2", "255.255.0.0", None, None), Address(socket.AF_INET6, "fe80::1", None, None, None)],
        "Wi-Fi": [Address(inet, "10.0.0.5", "255.255.255.0", None, None)],
        "VOXELVPN": [Address(inet, "10.200.0.2", "255.255.255.255", None, None)],
        "WireGuard Tunnel": [Address(inet, "10.201.0.2", "255.255.255.255", None, None)],
        "vEthernet (WSL)": [Address(inet, "172.20.0.1", "255.255.240.0", None, None)],
        "Loopback Pseudo-Interface 1": [Address(inet, "127.0.0.1", "255.0.0.0", None, None)],
        "Ethernet 2": [Address(inet, "192.168.50.1", "255.255.255.0", None, None)],
        "Conexão Local": [Address(inet, "0.0.0.0", "0.0.0.0", None, None)],
    }
    stats = {name: Stats(name != "Ethernet 2") for name in addrs}
    monkeypatch.setattr(network.psutil, "net_if_addrs", lambda: addrs)
    monkeypatch.setattr(network.psutil, "net_if_stats", lambda: stats)


def test_local_ipv4_excludes_loopback_apipa_vpn_virtual_and_down_adapters(fake_interfaces):
    assert network.local_ipv4_addresses() == ["10.0.0.5", "192.168.0.10"]


def test_dicom_page_lists_lan_ips_only_for_authenticated_user(client, fake_interfaces):
    data = client.get("/api/dicom").json()
    assert data["local_ipv4"] == ["10.0.0.5", "192.168.0.10"]
    assert data["bind_host"] == "127.0.0.1"
    assert "local_ipv4" not in client.get("/api/health/dicom").json()


# 4) Card VOXEL CLOUD a partir de fatos: destino habilitado, C-ECHO válido e sucesso recente.


def cloud_case(settings, database, paths):
    settings.update("dicom", {"outbound_ae_title": "CLINICA_GH"})
    queue = QueueManager(database)
    queue_id = queued_item(database, paths, queue)
    destination = database.query_one("SELECT * FROM destinations")
    with database.transaction() as connection:
        connection.execute(
            "UPDATE destinations SET enabled = 1, echo_status = 'SUCCESS', echo_at = CURRENT_TIMESTAMP, echo_target = ? WHERE id = ?",
            (echo_target("CLINICA_GH", destination), destination["id"]),
        )
    return RouterEngine(settings, database).health, queue, queue_id


def test_cloud_card_is_disconnected_without_verified_enabled_destination(settings, database, paths):
    health = RouterEngine(settings, database).health
    assert health.cloud()["status"] == "DISCONNECTED"

    health, _, _ = cloud_case(settings, database, paths)
    with database.transaction() as connection:
        connection.execute("UPDATE destinations SET enabled = 0")
    assert health.cloud()["status"] == "DISCONNECTED"
    # Habilitado, mas o AE de saída mudou: o C-ECHO deixou de valer.
    with database.transaction() as connection:
        connection.execute("UPDATE destinations SET enabled = 1")
    settings.update("dicom", {"outbound_ae_title": "OUTRO_AE"})
    assert health.cloud()["status"] == "DISCONNECTED"


def test_cloud_card_is_online_only_with_recent_success(settings, database, paths):
    health, queue, queue_id = cloud_case(settings, database, paths)
    result = health.cloud()
    assert result["status"] == "ONLINE"
    assert result["last_success_at"] == database.query_one("SELECT echo_at FROM destinations")["echo_at"]

    with database.transaction() as connection:
        connection.execute("UPDATE destinations SET echo_at = datetime('now', '-2 days')")
    assert health.cloud()["status"] == "WARNING"

    # Envio confirmado recente volta a deixar verde e passa a ser o "último sucesso".
    deliver(database, queue, queue_id)
    result = health.cloud()
    assert result["status"] == "ONLINE"
    assert result["last_success_at"] == database.query_one("SELECT completed_at FROM transfers WHERE status = 'SUCCESS'")["completed_at"]


def test_cloud_card_warns_when_last_send_failed_after_success(settings, database, paths):
    health, queue, _ = cloud_case(settings, database, paths)
    with database.transaction() as connection:
        connection.execute("UPDATE destinations SET echo_at = datetime('now', '-1 hour')")
    item = queue.claim_next()
    queue.fail(item, "DESTINATION_UNAVAILABLE", "sem rota")
    assert health.cloud()["status"] == "WARNING"


# 6) Destinos: só DICOM na criação/edição; registros antigos de outros tipos só são listados.


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Web", "kind": "DICOMWEB", "ae_title": "X", "host": "10.0.0.1", "port": 443},
        {"name": "Nuvem", "kind": "CLOUD", "endpoint": "https://exemplo.invalid"},
        {"name": "Sem host", "kind": "DICOM", "ae_title": "X", "port": 104},
    ],
)
def test_only_complete_dicom_destinations_can_be_created(client, payload):
    assert client.post("/api/destinations", json=payload).status_code == 422


def test_legacy_non_dicom_destination_is_listed_but_cannot_be_enabled(client, database):
    with database.transaction() as connection:
        connection.execute("INSERT INTO destinations(name, kind, endpoint, enabled) VALUES ('Antigo', 'CLOUD', 'https://exemplo.invalid', 0)")
    listed = client.get("/api/destinations").json()
    assert [(row["kind"], row["echo_valid"]) for row in listed] == [("CLOUD", False)]
    assert client.post(f"/api/destinations/{listed[0]['id']}/enable").status_code == 409
    assert client.post(f"/api/destinations/{listed[0]['id']}/echo").status_code == 400


# 7) bind_host configurável: padrão 0.0.0.0 em produção; testes/demonstração em 127.0.0.1.


def test_bind_host_defaults_to_all_interfaces_and_is_used_by_engine(settings, database, paths):
    defaults = json.loads((PROJECT_ROOT / "config" / "default.json").read_text(encoding="utf-8"))
    assert defaults["dicom"]["bind_host"] == "0.0.0.0"
    assert DicomScp(DicomIngestor(database, paths), "R", 104).bind_host == "0.0.0.0"
    assert RouterEngine(settings, database).scp.bind_host == "127.0.0.1"


@pytest.mark.parametrize("value", ["", "localhost", "::1", "300.1.1.1"])
def test_bind_host_must_be_an_ipv4_literal(database, paths, value):
    with pytest.raises(ValueError):
        DicomScp(DicomIngestor(database, paths), "R", 104, bind_host=value)


def test_scp_listens_only_on_configured_bind_host(database, paths):
    port = available_port()
    scp = DicomScp(DicomIngestor(database, paths), "R", port, bind_host="127.0.0.1")
    scp.start()
    try:
        assert scp._server.server_address == ("127.0.0.1", port)
    finally:
        scp.stop()
