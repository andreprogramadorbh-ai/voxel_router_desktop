"""Destino DICOM de TESTE que simula a célula VOXEL, para validar o envio do Router sem VPN.

Aceita C-ECHO e C-STORE só do Calling AE informado, não grava nada em disco e mostra a contagem
de instâncias recebidas. Uso (na mesma máquina do Router):

    python scripts/fake_voxel_destination.py --port 11112 --ae VOXEL_TESTE --calling-ae VXTESTE_RT

No Router: Destinos → DICOM VOXEL_TESTE@127.0.0.1:11112, AE de saída VXTESTE_RT.
Ctrl+C encerra.
"""

from __future__ import annotations

import argparse
import threading

from pynetdicom import AE, AllStoragePresentationContexts, VerificationPresentationContexts, evt

STATUS_SUCCESS = 0x0000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11112)
    parser.add_argument("--ae", default="VOXEL_TESTE")
    parser.add_argument("--calling-ae", default="VXTESTE_RT", help="Único Calling AE aceito (como a célula real)")
    args = parser.parse_args()

    lock = threading.Lock()
    counters = {"echo": 0, "store": 0, "refused": 0}

    def requested(event) -> None:
        calling = str(event.assoc.requestor.primitive.calling_ae_title).strip()
        if calling != args.calling_ae:
            with lock:
                counters["refused"] += 1
            print(f"Recusado: Calling AE '{calling}' não autorizado (esperado '{args.calling_ae}')")
            event.assoc.acse.send_reject(0x01, 0x01, 0x03)

    def echo(_event) -> int:
        with lock:
            counters["echo"] += 1
        print("C-ECHO recebido")
        return STATUS_SUCCESS

    def store(_event) -> int:
        with lock:
            counters["store"] += 1
            total = counters["store"]
        print(f"C-STORE recebido — total de instâncias nesta sessão: {total}")
        return STATUS_SUCCESS

    ae = AE(ae_title=args.ae)
    for context in VerificationPresentationContexts + AllStoragePresentationContexts:
        ae.add_supported_context(context.abstract_syntax, context.transfer_syntax)
    print(f"Destino de teste {args.ae} escutando em {args.host}:{args.port} (aceita só {args.calling_ae}). Ctrl+C para sair.")
    try:
        ae.start_server(
            (args.host, args.port),
            block=True,
            evt_handlers=[(evt.EVT_REQUESTED, requested), (evt.EVT_C_ECHO, echo), (evt.EVT_C_STORE, store)],
        )
    except KeyboardInterrupt:
        pass
    print(f"Resumo: {counters}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
