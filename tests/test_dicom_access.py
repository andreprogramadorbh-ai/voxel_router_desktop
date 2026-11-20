from __future__ import annotations

import socket
import time

import pytest
from fastapi.testclient import TestClient
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
from pynetdicom import AE
from pynetdicom.sop_class import Verification

from app.api.server import create_app
from app.core.engine import RouterEngine
from app.dicom.access import MAX_RECORDED_ATTEMPTS, DicomAccessControl, normalize_ae_title, normalize_host_network
from app.dicom.ingest import DicomIngestor
from app.dicom.scp import DicomScp

ROUTER_AE = "VOXEL_ROUTER_T"


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def synthetic_ct() -> Dataset:
    dataset = Dataset()
    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = CTImageStorage
    dataset.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    dataset.SOPClassUID = CTImageStorage
    dataset.SOPInstanceUID = dataset.file_meta.MediaStorageSOPInstanceUID
    dataset.StudyInstanceUID = generate_uid()
    dataset.SeriesInstanceUID = generate_uid()
    dataset.PatientID = "PID-ACCESS"
    dataset.Modality = "CT"
    dataset.Rows = 1
    dataset.Columns = 1
    dataset.BitsAllocated = 8
    dataset.PixelRepresentation = 0
    dataset.PixelData = b"\x00"
    return dataset


def add_node(database, ae_title: str, host: str, enabled: bool = True) -> int:
    with database.transaction() as connection:
        cursor = connection.execute(
            "INSERT INTO dicom_nodes(name, ae_title, host, port, enabled) VALUES (?, ?, ?, 104, ?)",
            (f"{ae_title}-{host}", ae_title, host, int(enabled)),
        )
        return int(cursor.lastrowid)


@pytest.fixture
def scp(database, paths):
    port = available_port()
    server = DicomScp(DicomIngestor(database, paths), ROUTER_AE, port, bind_host="127.0.0.1")
    server.start()
    time.sleep(0.1)
    yield server
    server.stop()


class StoreResult:
    def __init__(self, assoc, established: bool, status) -> None:
        self.assoc = assoc
        self.is_established = established
        self.is_rejected = assoc.is_rejected
        self.status = status


def store(port: int, calling_ae: str, called_ae: str = ROUTER_AE) -> tuple[StoreResult, object]:
    """Envia um CT sintético; devolve (resultado da associação, status C-STORE ou None)."""
    scu = AE(ae_title=calling_ae)
    scu.acse_timeout = 5
    scu.add_requested_context(CTImageStorage, ExplicitVRLittleEndian)
    assoc = scu.associate("127.0.0.1", port, ae_title=called_ae)
    if not assoc.is_established:
        return StoreResult(assoc, False, None), None
    status = assoc.send_c_store(synthetic_ct())
    assoc.release()
    return StoreResult(assoc, True, status), status


def echo(port: int, calling_ae: str, called_ae: str = ROUTER_AE):
    scu = AE(ae_title=calling_ae)
    scu.acse_timeout = 5
    scu.add_requested_context(Verification)
    return scu.associate("127.0.0.1", port, ae_title=called_ae)


def stored_count(database) -> int:
    return int(database.query_one("SELECT COUNT(*) AS n FROM instances")["n"])


def object_files(paths) -> list:
    return [path for path in (paths.storage / "objects").rglob("*.dcm")] if (paths.storage / "objects").exists() else []


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.0/24"])
def test_registered_modality_by_ip_or_cidr_is_accepted(database, scp, host):
    add_node(database, "CT_SALA1", host)

    assoc, status = store(scp.port, "CT_SALA1")

    assert assoc.is_established
    assert status.Status == 0x0000
    assert stored_count(database) == 1


@pytest.mark.parametrize(
    ("node", "calling_ae", "called_ae"),
    [
        (None, "CT_SALA1", ROUTER_AE),  # nada cadastrado
        (("OUTRO_AE", "127.0.0.1", True), "CT_SALA1", ROUTER_AE),  # AE desconhecido
        (("CT_SALA1", "10.20.30.40", True), "CT_SALA1", ROUTER_AE),  # AE certo, IP errado
        (("CT_SALA1", "10.20.30.0/24", True), "CT_SALA1", ROUTER_AE),  # AE certo, fora do CIDR
        (("CT_SALA1", "127.0.0.1", False), "CT_SALA1", ROUTER_AE),  # modalidade desabilitada
        (("CT_SALA1", "127.0.0.1", True), "CT_SALA1", "OUTRO_ROUTER"),  # Called AE errado
    ],
)
def test_unauthorized_association_is_rejected_before_any_object_is_stored(database, paths, scp, node, calling_ae, called_ae):
    if node:
        add_node(database, *node)

    assoc, status = store(scp.port, calling_ae, called_ae)

    assert assoc.is_rejected
    assert status is None
    assert stored_count(database) == 0
    assert object_files(paths) == []


def test_called_ae_rejection_uses_called_ae_diagnostic(database, scp):
    add_node(database, "CT_SALA1", "127.0.0.1")

    assoc, _ = store(scp.port, "CT_SALA1", "OUTRO_ROUTER")

    assert assoc.assoc.acceptor.primitive.diagnostic == 0x07


def test_c_echo_answers_unknown_requestors(database, scp):
    assoc = echo(scp.port, "DESCONHECIDO")

    assert assoc.is_established
    assert assoc.send_c_echo().Status == 0x0000
    assoc.release()


def test_c_echo_with_wrong_called_ae_is_rejected(database, scp):
    assert echo(scp.port, "DESCONHECIDO", "OUTRO_ROUTER").is_rejected


def test_rejections_are_recorded_without_learning_mode_but_echo_is_not(database, scp):
    store(scp.port, "CT_NOVO")
    store(scp.port, "CT_NOVO")
    echo(scp.port, "US_NOVO").release()

    attempts = DicomAccessControl(database).list_attempts()
    assert [(row["calling_ae"], row["reason"], row["attempt_count"]) for row in attempts] == [("CT_NOVO", "UNKNOWN_NODE", 2)]
    assert stored_count(database) == 0


def test_recorded_attempts_are_aggregated_and_limited_to_most_recent(database):
    access = DicomAccessControl(database)
    for index in range(MAX_RECORDED_ATTEMPTS + 10):
        access.record_attempt(f"AE{index}", ROUTER_AE, "192.168.0.10", "UNKNOWN_NODE")
        with database.transaction() as connection:
            # Datas distintas e crescentes, sem depender do relógio.
            connection.execute("UPDATE dicom_access_attempts SET last_seen_at = ? WHERE calling_ae = ?", (f"2026-01-01 00:{index // 60:02d}:{index % 60:02d}", f"AE{index}"))
    access.record_attempt("AE5", ROUTER_AE, "192.168.0.10", "UNKNOWN_NODE")  # já descartado: volta como novo
    access.record_attempt("AE59", ROUTER_AE, "192.168.0.10", "UNKNOWN_NODE")  # existente: agrega

    attempts = access.list_attempts()
    by_ae = {row["calling_ae"]: row for row in attempts}
    assert len(attempts) == MAX_RECORDED_ATTEMPTS
    assert int(database.query_one("SELECT COUNT(*) AS n FROM dicom_access_attempts")["n"]) == MAX_RECORDED_ATTEMPTS
    assert by_ae["AE59"]["attempt_count"] == 2
    assert by_ae["AE5"]["attempt_count"] == 1
    assert "AE0" not in by_ae and "AE10" not in by_ae  # a volta do AE5 expulsou o mais antigo
    assert "AE11" in by_ae


def test_learning_mode_records_attempts_but_never_allows_store(database, paths, scp):
    access = DicomAccessControl(database)
    access.enable_learning(1)

    for _ in range(2):
        assoc, _ = store(scp.port, "CT_NOVO")
        assert assoc.is_rejected
    echo(scp.port, "US_NOVO").release()
    store(scp.port, "CT_NOVO", "AE_ERRADO")

    attempts = {(row["calling_ae"], row["reason"]): row for row in access.list_attempts()}
    assert attempts[("CT_NOVO", "UNKNOWN_NODE")]["attempt_count"] == 2
    assert attempts[("CT_NOVO", "UNKNOWN_NODE")]["source_ip"] == "127.0.0.1"
    assert ("US_NOVO", "ECHO_ONLY") in attempts
    assert attempts[("CT_NOVO", "CALLED_AE")]["called_ae"] == "AE_ERRADO"
    assert stored_count(database) == 0
    assert object_files(paths) == []


def test_learning_mode_expires_and_is_limited_to_24_hours(database):
    access = DicomAccessControl(database)
    with pytest.raises(ValueError):
        access.enable_learning(25)
    access.enable_learning(24)
    assert access.learning_active()
    with database.transaction() as connection:
        connection.execute("UPDATE settings SET value = '2000-01-01 00:00:00' WHERE key = 'dicom.learning_mode_until'")
    assert not access.learning_active()
    assert access.record_attempt("CT", ROUTER_AE, "127.0.0.1", "ECHO_ONLY") is False


def test_access_check_failure_rejects_association(database, scp, monkeypatch):
    add_node(database, "CT_SALA1", "127.0.0.1")

    def broken(*_):
        raise RuntimeError("banco indisponível")

    monkeypatch.setattr(scp.access, "is_allowed", broken)
    assoc, _ = store(scp.port, "CT_SALA1")

    assert assoc.is_rejected
    assert stored_count(database) == 0


@pytest.mark.parametrize(("value", "expected"), [("192.168.0.50", "192.168.0.50"), ("192.168.0.0/24", "192.168.0.0/24"), ("192.168.0.7/24", "192.168.0.0/24"), ("10.0.0.9/32", "10.0.0.9")])
def test_host_normalization_accepts_ip_and_cidr(value, expected):
    assert normalize_host_network(value) == expected


@pytest.mark.parametrize("value", ["ct-sala1.local", "0.0.0.0/0", "10.0.0.0/8", "", "300.1.1.1"])
def test_host_normalization_rejects_hostnames_and_broad_ranges(value):
    with pytest.raises(ValueError):
        normalize_host_network(value)


@pytest.mark.parametrize("value", ["", " ", "A" * 17, "CT\\1", "CT\x01"])
def test_ae_title_validation(value):
    with pytest.raises(ValueError):
        normalize_ae_title(value)


def login(client) -> None:
    client.post("/api/auth/provision", json={"username": "voxeladmin", "password": "SenhaInicial@2026"})
    client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaInicial@2026"})
    client.post("/api/auth/change-password", json={"current_password": "SenhaInicial@2026", "new_password": "SenhaDefinitiva@2026"})
    client.post("/api/auth/login", json={"username": "voxeladmin", "password": "SenhaDefinitiva@2026"})


def test_access_api_learning_register_and_audit(settings, database):
    settings.update("orthanc", {"enabled": False})
    engine = RouterEngine(settings, database)
    with TestClient(create_app(engine, start_engine=False)) as client:
        assert client.get("/api/dicom/access").status_code == 401
        login(client)

        access = client.get("/api/dicom/access").json()
        assert access["nodes"] == {"total": 0, "enabled": 0}
        assert client.get("/api/health").json()["dicom"]["enabled_modalities"] == 0

        assert client.post("/api/dicom/learning", json={"hours": 25}).status_code == 422
        assert client.post("/api/dicom/learning", json={"hours": 2}).status_code == 200
        engine.scp.access.record_attempt("CT_NOVO", engine.scp.ae_title, "192.168.10.20", "UNKNOWN_NODE")
        attempt_id = client.get("/api/dicom/access").json()["attempts"][0]["id"]

        response = client.post(f"/api/dicom/attempts/{attempt_id}/register", json={"name": "Tomógrafo Sala 1", "modality": "CT"})
        assert response.status_code == 201
        node = database.query_one("SELECT ae_title, host, enabled FROM dicom_nodes WHERE id = ?", (response.json()["id"],))
        assert (node["ae_title"], node["host"], node["enabled"]) == ("CT_NOVO", "192.168.10.20", 1)
        assert engine.scp.access.is_allowed("CT_NOVO", "192.168.10.20")
        assert client.get("/api/dicom/access").json()["attempts"] == []

        assert client.post("/api/modalities", json={"name": "Hostname", "ae_title": "US1", "host": "us-sala2", "port": 104}).status_code == 422
        assert client.post("/api/modalities", json={"name": "Rede ampla", "ae_title": "US1", "host": "0.0.0.0/0", "port": 104}).status_code == 422
        node_id = response.json()["id"]
        update = {"name": "Tomógrafo Sala 1", "ae_title": "CT_NOVO", "host": "192.168.10.20", "port": 104, "enabled": False}
        assert client.put(f"/api/modalities/{node_id}", json=update).status_code == 204
        assert not engine.scp.access.is_allowed("CT_NOVO", "192.168.10.20")
        assert client.delete("/api/dicom/learning").status_code == 204

        actions = [row["action"] for row in database.query_all("SELECT action FROM audit_logs ORDER BY id")]
        assert {"ENABLE_DICOM_LEARNING", "CREATE_MODALITY", "UPDATE_MODALITY", "DISABLE_DICOM_LEARNING"} <= set(actions)
        update_audit = database.query_one("SELECT details FROM audit_logs WHERE action = 'UPDATE_MODALITY'")["details"]
        assert "enabled=1" in update_audit and "enabled=0" in update_audit
        assert "origem=aprendizado" in database.query_one("SELECT details FROM audit_logs WHERE action = 'CREATE_MODALITY'")["details"]
