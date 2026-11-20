# CLAUDE.md — VOXEL Router Desktop

Responda, documente e escreva commits em **português**.

## Propósito

Gateway DICOM instalado em Windows dentro da clínica. Recebe C-STORE das modalidades na LAN,
guarda no Orthanc local e encaminha para a célula dedicada do VOXEL PACS por túnel WireGuard
("VOXEL VPN"). Primeiro cliente: clínica sem IP público fixo.

## Arquitetura (estado atual do código)

| Componente | Onde | Código |
|---|---|---|
| Router SCP (C-ECHO/C-STORE) | `0.0.0.0:4242/TCP` | `app/dicom/scp.py`, `app/dicom/ingest.py` |
| Orthanc local (gravação via REST) | `127.0.0.1:8042` | `app/orthanc/client.py`, `app/orthanc/configuration.py` |
| Orthanc DICOM | desligado (`DicomServerEnabled: false`); REST só em loopback com autenticação | `app/orthanc/configuration.py` |
| Fila persistente SQLite | `ProgramData\VOXEL\Router` | `app/queue/manager.py`, `app/core/database.py` |
| Envio C-STORE (SCU) | destino/célula VOXEL | `app/transfer/manager.py`, `app/cloud/connectors.py` |
| UI/API administrativa | `127.0.0.1:8765` | `app/api/server.py`, `frontend/` |
| Segredos | DPAPI (Windows) / Fernet só em dev | `app/security/secrets.py` |
| VOXEL VPN (WireGuard) | serviço `WireGuardTunnel$VOXELVPN`, `ProgramData\VOXEL\Router\vpn` | `app/vpn/wireguard.py` |
| Serviços Windows | `VOXELRouter`, `VOXELOrthanc` | `app/service_main.py`, `app/orthanc/service_main.py` |
| Instalador | Inno Setup | `installer/VOXEL_ROUTER_SETUP.iss`, `scripts/build_windows.ps1` |

Padrões: FastAPI, SQLite com migrations idempotentes (`app/core/database.py`), `Settings`
(`app/config/settings.py`, `config/default.json`), `WindowsSecretStore`, structlog sanitizado
(`app/core/logging.py`).

## Regras

- Ler README, `docs/` e o código relacionado antes de alterar. Não inventar APIs; se algo não
  existir, dizer "Não localizado no código analisado".
- Trabalho por fases: ao fim de cada fase rodar testes e ruff, resumir o diff, fazer **um** commit
  local e parar para aprovação. **Nunca fazer push.**
- Na máquina de desenvolvimento: **não** instalar serviços Windows, **não** alterar firewall,
  **não** instalar WireGuard, **não** rodar o instalador.
- Nunca colocar senha, chave privada, token ou certificado no código, config versionada,
  instalador ou logs. Logs nunca com nome de paciente ou UIDs.
- Testes só com DICOM sintético gerado por pydicom. Nunca dado real.
- Mudanças pequenas, sem abstrações desnecessárias, seguindo os padrões existentes.
- Processos externos: caminho absoluto, sem shell, argumentos em lista, timeout, saída sanitizada.
- Fins de linha: `.gitattributes` (LF; `.ps1`/`.iss`/`.bat` em CRLF).

## Como testar

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check app scripts --select F,E9
```

O CI (`.github/workflows/ci.yml`) roda em Ubuntu; o caminho DPAPI (`win32crypt`) só é exercitado
no Windows.
