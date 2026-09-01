from __future__ import annotations

import socket
import time

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
from pynetdicom import AE
from pynetdicom.sop_class import Verification

from app.dicom.ingest import DicomIngestor
from app.dicom.scp import DicomScp


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.bind(("127.0.0.1", 0))
        return int(candidate.getsockname()[1])


def test_scp_accepts_c_echo_and_c_store(database, paths):
    ingestor = DicomIngestor(database, paths)
    port = available_port()
    scp = DicomScp(ingestor, "VOXEL_ROUTER_T", port)
    scp.start()
    time.sleep(0.1)
    try:
        echo_scu = AE(ae_title="TEST_SCU")
        echo_scu.add_requested_context(Verification)
        echo_assoc = echo_scu.associate("127.0.0.1", port, ae_title="VOXEL_ROUTER_T")
        assert echo_assoc.is_established
        assert echo_assoc.send_c_echo().Status == 0x0000
        echo_assoc.release()

        dataset = Dataset()
        dataset.file_meta = FileMetaDataset()
        dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        dataset.file_meta.MediaStorageSOPClassUID = CTImageStorage
        dataset.file_meta.MediaStorageSOPInstanceUID = generate_uid()
        dataset.SOPClassUID = CTImageStorage
        dataset.SOPInstanceUID = dataset.file_meta.MediaStorageSOPInstanceUID
        dataset.StudyInstanceUID = generate_uid()
        dataset.SeriesInstanceUID = generate_uid()
        dataset.PatientID = "PID-NETWORK"
        dataset.Modality = "CT"
        dataset.Rows = 1
        dataset.Columns = 1
        dataset.BitsAllocated = 8
        dataset.PixelRepresentation = 0
        dataset.PixelData = b"\x00"

        store_scu = AE(ae_title="TEST_SCU")
        store_scu.add_requested_context(CTImageStorage, ExplicitVRLittleEndian)
        store_assoc = store_scu.associate("127.0.0.1", port, ae_title="VOXEL_ROUTER_T")
        assert store_assoc.is_established
        assert store_assoc.send_c_store(dataset).Status == 0x0000
        store_assoc.release()

        stored = database.query_one("SELECT study_instance_uid, instance_count FROM studies")
        assert stored["study_instance_uid"] == str(dataset.StudyInstanceUID)
        assert stored["instance_count"] == 1
    finally:
        scp.stop()
