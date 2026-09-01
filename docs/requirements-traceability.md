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
| Orthanc | Cliente REST, health, ingestão, configuração gerada e serviço no instalador | `app/orthanc/client.py`, `scripts/configure_orthanc.py`, `installer/` | Implementado; requer binário homologado |
| CloudConnector | Interface assíncrona, conector DICOM SCU e fallback que preserva a fila | `app/cloud/connectors.py`, `app/transfer/manager.py` | Implementado |
| Retry e transmissão | Limite de concorrência, quatro intervalos padrão configuráveis, máximo de tentativas, persistência de erros | `app/transfer/manager.py`, `app/queue/manager.py` | Implementado |
| Health checks | `/health`, `/health/orthanc`, `/health/cloud`, `/health/dicom`, `/health/storage` | `app/api/server.py` | Implementado |
| Segurança de API | Loopback por padrão, Trusted Host, validação Pydantic, SQL parametrizado, cookies `HttpOnly`/`SameSite`, DPAPI | `app/api/server.py`, `app/security/secrets.py` | Implementado |
| Privacidade | Redaction de senha/token/PHI nos logs; guia de coleta sanitizada | `app/core/logging.py`, `docs/troubleshooting.md` | Implementado |
| Windows Service e firewall | Host pywin32, serviços definidos, recuperação e regra de firewall DICOM privada | `app/service_main.py`, `installer/VOXEL_ROUTER_SETUP.iss` | Implementado; validar em Windows |
| Instalador EXE e silencioso | Receita Inno Setup e script de build, com `/S` | `installer/`, `scripts/build_windows.ps1` | Implementado; compilar em Windows |
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

## Dependências de homologação

O instalador não deve incluir Orthanc sem validação de versão, licença, plugins e assinatura. A integração VOXEL Cloud não pode ser considerada conectada até receber contrato de endpoints, credenciais, política de confirmação e certificados. A função de exportar/restaurar configurações e o Update Manager devem ser finalizados após haver assinatura/criptografia, formato de backup e origem de atualizações aprovados. Esses pontos estão explicitamente fora da afirmação de aceite até que suas dependências estejam disponíveis.

## Referências

[1]: https://github.com/ASOARESBH/VOXEL_ROUTER_DESKTOP "Repositório VOXEL Router Desktop"
[2]: https://pydicom.github.io/pynetdicom/stable/ "pynetdicom documentation"
[3]: https://www.orthanc-server.com/static.php?page=users-manual "Orthanc Book — User Manual"
