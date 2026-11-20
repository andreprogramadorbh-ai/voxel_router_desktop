"""Envia um estudo DICOM SINTÉTICO (sem dado de paciente real) para testar o Router.

Uso (de outra máquina da LAN, com Python 3.12 e `pip install pydicom pynetdicom`):

    python scripts/send_synthetic_study.py --host 192.168.0.50 --port 4242 \
        --called-ae VOXEL_ROUTER --calling-ae MODALIDADE_TESTE --instances 3

O Calling AE e o IP desta máquina precisam estar cadastrados e habilitados em Modalidades.
Paciente: "VOXEL^TESTE^SINTETICO", ID "SINTETICO-0001" — fácil de achar e apagar depois.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid
from pynetdicom import AE


def synthetic_instance(study_uid: str, series_uid: str, number: int) -> Dataset:
    dataset = Dataset()
    dataset.file_meta = FileMetaDataset()
    dataset.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dataset.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    dataset.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    dataset.SOPClassUID = SecondaryCaptureImageStorage
    dataset.SOPInstanceUID = dataset.file_meta.MediaStorageSOPInstanceUID
    dataset.StudyInstanceUID = study_uid
    dataset.SeriesInstanceUID = series_uid
    dataset.PatientName = "VOXEL^TESTE^SINTETICO"
    dataset.PatientID = "SINTETICO-0001"
    dataset.StudyDescription = "TESTE SINTETICO VOXEL ROUTER"
    dataset.InstitutionName = "TESTE VOXEL"
    dataset.Modality = "OT"
    now = datetime.now()
    dataset.StudyDate = now.strftime("%Y%m%d")
    dataset.StudyTime = now.strftime("%H%M%S")
    dataset.AccessionNumber = f"SINT{now.strftime('%H%M%S')}"
    dataset.InstanceNumber = number
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.Rows = 64
    dataset.Columns = 64
    dataset.BitsAllocated = 8
    dataset.BitsStored = 8
    dataset.HighBit = 7
    dataset.PixelRepresentation = 0
    dataset.PixelData = bytes((x + y + number * 20) % 256 for y in range(64) for x in range(64))
    return dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=4242)
    parser.add_argument("--called-ae", default="VOXEL_ROUTER")
    parser.add_argument("--calling-ae", default="MODALIDADE_TESTE")
    parser.add_argument("--instances", type=int, default=3)
    parser.add_argument("--echo-only", action="store_true", help="Só C-ECHO, sem enviar imagens")
    args = parser.parse_args()

    ae = AE(ae_title=args.calling_ae)
    ae.add_requested_context("1.2.840.10008.1.1")
    ae.add_requested_context(SecondaryCaptureImageStorage, ExplicitVRLittleEndian)
    assoc = ae.associate(args.host, args.port, ae_title=args.called_ae)
    if not assoc.is_established:
        print("Associação recusada ou sem resposta (confira IP/AE cadastrados em Modalidades e o firewall).")
        return 2
    try:
        echo = assoc.send_c_echo()
        print(f"C-ECHO: status 0x{int(echo.Status):04X}" if echo else "C-ECHO sem resposta")
        if args.echo_only:
            return 0 if echo and echo.Status == 0 else 1
        study_uid, series_uid = generate_uid(), generate_uid()
        failures = 0
        for number in range(1, max(1, args.instances) + 1):
            status = assoc.send_c_store(synthetic_instance(study_uid, series_uid, number))
            code = int(status.Status) if status and "Status" in status else -1
            print(f"C-STORE {number}/{args.instances}: status 0x{code:04X}" if code >= 0 else f"C-STORE {number}: sem resposta")
            failures += code != 0
        return 1 if failures else 0
    finally:
        assoc.release()


if __name__ == "__main__":
    sys.exit(main())
