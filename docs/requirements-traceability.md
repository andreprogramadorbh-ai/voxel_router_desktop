# Rastreabilidade de Requisitos — MVP VOXEL Router Desktop

Esta matriz registra o tratamento dos requisitos recebidos e evita caracterizar como concluída uma integração que depende de infraestrutura, credenciais, certificados ou binários ainda não homologados. O código-fonte foi criado sobre repositório inicialmente vazio; a evidência automatizada encontra-se em `tests/`.

| Grupo de requisito | Implementação | Evidência/arquivo | Estado |
|---|---|---|---|
| Python, FastAPI, SQLite, `pydicom`, `pynetdicom`, `httpx`, `cryptography`, `psutil` | Manifesto de dependências e módulos dedicados | `pyproject.toml`, `app/` | Implementado |
| Login e administrador | Provisionamento sem senha codificada, Argon2id, sessão hash, expiração, logout, troca obrigatória e limitação | `app/auth/service.py`, `app/api/server.py`, `tests/test_authentication.py` | Implementado |
| Senha inicial declarada no prompt | O usuário inicial padrão é `voxeladmin`; a senha é provisionada localmente e não existe no fonte/instalador/log | `scripts/provision_admin.py`, `README.md` | Implementado com endurecimento de segurança |
| Identidade visual | Logotipo oficial localizado no repositório VOXEL PACS foi copiado sem alteração; paleta e ativos auxiliares do Router incluídos | `frontend/static/img/`, `frontend/static/css/router.css` | Implementado |
| Dashboard e monitoramento | Saúde de Router/Orthanc/cloud/SCP, fila, storage e tabela de estudos | `frontend/`, `app/monitoring/health.py`, `app/api/server.py` | Implementado |
| Fila persistente | SQLite WAL/FULL, prioridades, pause/resume/retry/cancel, tentativas e recuperação de `SENDING` | `app/queue/manager.py`, `tests/test_queue.py` | Implementado |
| Store and forward | Ingestão → checksum → persistência → estudo completo → fila → envio → validação; retenção somente validada | `app/dicom/ingest.py`, `app/core/engine.py` | Implementado |
| Checksum e duplicidade | SHA-256, SOP Instance UID único, storage atômico e evento de duplicidade | `app/dicom/ingest.py`, `tests/test_dicom_ingest.py` | Implementado |
| DICOM local | C-ECHO, C-STORE SCP, AE Title/porta configuráveis e cópia de configuração | `app/dicom/scp.py`, `frontend/` | Implementado |
| Modalidades e destinos | CRUD administrativo local para nós e destinos; modelo preparado para DICOM/DICOMweb/cloud | `app/api/server.py`, `frontend/` | Implementado |
| Orthanc | Processo `Orthanc.exe` independente, host de serviço próprio, REST real, DICOM `4243`, configuração e storage persistentes | `app/orthanc/`, `scripts/configure_orthanc.py`, `installer/`, `tests/test_orthanc_installation.py` | Implementado; requer binário homologado e validação Windows |
| CloudConnector | Interface assíncrona, conector DICOM SCU e fallback que preserva a fila | `app/cloud/connectors.py`, `app/transfer/manager.py` | Implementado |
| Retry e transmissão | Limite de concorrência, quatro intervalos padrão configuráveis, máximo de tentativas, persistência de erros | `app/transfer/manager.py`, `app/queue/manager.py` | Implementado |
| Health checks | `/health` consulta a REST real do Orthanc; diagnóstico final verifica serviços, storage, portas `4242/4243/8042/8765` e endpoints | `app/monitoring/health.py`, `scripts/diagnose_install.py`, `tests/test_orthanc_installation.py` | Implementado; validação física de serviços requer Windows |
| Segurança de API | Loopback por padrão, Trusted Host, validação Pydantic, SQL parametrizado, cookies `HttpOnly`/`SameSite`, DPAPI | `app/api/server.py`, `app/security/secrets.py` | Implementado |
| Privacidade | Redaction de senha/token/PHI nos logs; guia de coleta sanitizada | `app/core/logging.py`, `docs/troubleshooting.md` | Implementado |
| Windows Service e firewall | Serviços independentes `VOXELRouter` e `VOXELOrthanc`, recovery SCM e regras DICOM privadas `4242/4243` | `app/service_main.py`, `app/orthanc/service_main.py`, `installer/VOXEL_ROUTER_SETUP.iss` | Implementado; validar em Windows |
| Instalador único e silencioso | Instala Router + Orthanc, detecta instalação, preserva ProgramData, registra serviços, executa diagnóstico e aceita `/S` | `installer/`, `scripts/build_windows.ps1`, `scripts/diagnose_install.py` | Implementado; compilar/homologar em Windows |
| Integração Philips Non-DICOM | Parsers `submission/document` e `WTT_ITEM`, diretórios persistentes, fila irmã, polling, retry, logs e cliente cloud parametrizado | `app/non_dicom/`, `tests/test_non_dicom.py`, `docs/non-dicom-integration.md` | Implementado; contrato remoto real do VOXEL PACS permanece configurável |
| Backup/configuração e update manager | Diretórios de backup, proteção de segredos e fluxo de atualização preservando ProgramData | `app/config/settings.py`, `docs/installation.md` | Parcial: exportação/restauração assinada e feed de update requerem definição de produto |
| Regras de roteamento avançadas/multi-destino | Modelo de destinos e `CloudConnector` permitem evolução; regras declarativas ainda não possuem UI/engine | `destinations`, `CloudConnector` | Preparado para expansão |
| Registro VOXEL Cloud, tokens e renovação | Interface desacoplada; contrato de API cloud ainda não definido | `app/cloud/connectors.py` | Preparado para integração |

## Validação automatizada entregue

| Cenário | Evidência |
|---|---|
| Argon2id, sessão, troca obrigatória e lockout | `tests/test_authentication.py` |
| Checksum, deduplicação e modalidades por séries | `tests/test_dicom_ingest.py` |
| Completude, fila, retry e reinício | `tests/test_queue.py` |
| C-ECHO e C-STORE reais contra SCP em rede local | `tests/test_dicom_network.py` |
| Provisionamento/login/troca de senha/API local | `tests/test_api.py` |
| XML Philips, WTT_ITEM, path traversal, MIME, duplicidade, polling, fila, sucesso, falha, retry e cloud indisponível | `tests/test_non_dicom.py` |

## Dependências de homologação

O instalador não deve incluir Orthanc sem validação de versão, licença, plugins e assinatura. A integração VOXEL Cloud e a entrega Non-DICOM não podem ser consideradas conectadas até receber contrato de endpoints, credenciais, política de confirmação e certificados. A função de exportar/restaurar configurações e o Update Manager devem ser finalizados após haver assinatura/criptografia, formato de backup e origem de atualizações aprovados. Esses pontos estão explicitamente fora da afirmação de aceite até que suas dependências estejam disponíveis.

## Referências

[1]: https://github.com/ASOARESBH/VOXEL_ROUTER_DESKTOP "Repositório VOXEL Router Desktop"
[2]: https://pydicom.github.io/pynetdicom/stable/ "pynetdicom documentation"
[3]: https://www.orthanc-server.com/static.php?page=users-manual "Orthanc Book — User Manual"
