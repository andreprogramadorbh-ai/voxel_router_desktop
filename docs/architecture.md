# Arquitetura — VOXEL Router Desktop

**Versão:** 1.0.0
**Estado:** MVP implementável para Windows, com execução local compatível com desenvolvimento em outros sistemas.

> **Princípio de operação:** a modalidade entrega o estudo uma única vez; o Router persiste metadados e estado de transmissão até uma confirmação positiva e validada do destino.

## Escopo e limites

O VOXEL Router Desktop é um *DICOM Edge Router*. Ele recebe objetos DICOM por C-STORE, delega a retenção temporária ao Orthanc local quando configurado, mantém uma fila administrativa persistente em SQLite e transmite estudos completos para destinos DICOM por C-STORE. A interface administrativa é servida somente em `127.0.0.1` por padrão. O produto não substitui o VOXEL PACS nem publica a administração na rede.

| Camada | Responsabilidade | Processo de produção |
|---|---|---|
| Router UI | Login, monitoramento, configuração e operações administrativas | Navegador local/atalho Windows |
| Router API | API FastAPI local, autenticação, validação e recursos estáticos | `VOXELRouter.exe` |
| Router Engine | Reconciliação, completude de estudo, fila, retry, transmissão, retenção e *health checks* | `VOXELRouterService.exe` |
| DICOM SCP | C-ECHO/C-STORE local, checksum, deduplicação e descoberta de estudos | Processo da Engine |
| Orthanc local | Armazenamento temporário DICOM, consulta e REST API | `VOXEL Orthanc Service` |
| SQLite | Estado administrativo, auditoria, sessões, fila e metadados | Arquivo em `ProgramData` |

## Fluxo de dados

```text
Modalidade --C-STORE--> DICOM SCP --REST--> Orthanc local
                              |                 |
                              +--SHA-256---------+
                                        |
                         SQLite (studies / instances / queue)
                                        |
                            TransferManager / CloudConnector
                                        |
                              Destino DICOM ou VOXEL Cloud
```

O objeto recebido é validado por `pydicom`, recebe SHA-256 e é deduplicado por `SOPInstanceUID` e checksum. A Engine considera um estudo pronto somente após a janela configurável de silêncio sem novas instâncias. Depois disso cria, em transação, uma entrada de fila persistente. Ao reiniciar, entradas `SENDING` são devolvidas a `RETRY` e a fila é reconstruída a partir de estudos elegíveis.

## Persistência e estados

| Entidade | Chave/índices relevantes | Objetivo |
|---|---|---|
| `users` | `username` único | Administrador com senha Argon2id e troca obrigatória no primeiro acesso |
| `sessions` | hash do token, expiração | Sessões revogáveis, nunca tokens em texto puro |
| `settings` | `key` único | Configurações não secretas; segredos ficam em DPAPI no Windows |
| `dicom_nodes`, `destinations` | nomes únicos | Modalidades permitidas e destinos configurados |
| `studies`, `series`, `instances` | UIDs únicos | Inventário operacional e checksum sem registrar dados clínicos nos logs |
| `queue`, `queue_attempts`, `transfers` | estudo/destino + timestamps | Fila persistente, tentativas e histórico de transmissão |
| `audit_logs`, `system_events` | timestamp/categoria | Auditoria e diagnóstico com sanitização de PHI/segredos |

Os estados suportados são `RECEIVED`, `PROCESSING`, `READY_TO_SEND`, `QUEUED`, `SENDING`, `SENT`, `VALIDATED`, `RETRY`, `PAUSED`, `CANCELLED` e `ERROR`. As transições são aplicadas somente por métodos de serviço que registram evento e auditoria.

## Segurança

A senha de bootstrap declarada na especificação é material operacional sensível e **não é codificada**. O instalador deve solicitá-la ou o primeiro processo deve recebê-la pelo Windows Credential Manager/DPAPI; se nenhuma credencial for provisionada, a interface bloqueia o login e exibe a instrução local de provisionamento. O hash é Argon2id. Tentativas inválidas são limitadas por usuário/IP, a mensagem de login é sempre genérica e as sessões usam cookie `HttpOnly`, `SameSite=Strict` e expiração. A API valida dados por Pydantic, executa SQL parametrizado e normaliza caminhos de armazenamento para impedir *path traversal*.

Segredos de cloud nunca integram o frontend, o banco de configuração ou logs. Em Windows, `WindowsSecretStore` protege valores via DPAPI. Em outros sistemas, o backend exige variável de ambiente de desenvolvimento, claramente marcada como não adequada para produção. Comunicação DICOM TLS e HTTPS/TLS são opcionais por destino e exigem certificados explicitamente configurados.

## Resiliência e recuperação

A ordem de inicialização é: verificar banco; verificar/iniciar Orthanc; verificar storage; reconciliar itens pendentes; reconstruir fila; verificar destinos; iniciar workers. A política padrão de retry é `30 s`, `120 s`, `300 s` e `900 s`, limitada a quatro tentativas. O estudo só entra em retenção elegível após `VALIDATED`; a remoção exige política explícita de retenção, e nunca ocorre na desinstalação sem escolha do operador.

## Integrações substituíveis

`CloudConnector` é uma interface assíncrona. O MVP fornece `DicomCloudConnector`, que usa associação C-STORE, e `UnavailableCloudConnector` para assegurar que a ausência de integração da VOXEL Cloud mantenha o estudo em fila. Adaptadores futuros podem implementar registro do Router, token/renovação, confirmação REST ou DICOMweb sem acoplar a Engine a um endpoint específico.

## Implantação Windows

O artefato de instalação provisiona o runtime empacotado, Orthanc, configuração gerada, serviços Windows, regras mínimas de firewall na porta DICOM e atalhos. Dados operacionais são mantidos em `C:\ProgramData\VOXEL\Router`; binários em `C:\Program Files\VOXEL\Router`. O instalador Inno Setup oferece modo silencioso `/S` e a desinstalação preserva dados salvo consentimento explícito.

## Impacto, risco e rollback

Como DICOM, Orthanc e credenciais são componentes clínico-operacionais, toda implantação deve começar sem excluir estudos e com os limites de storage ajustados ao cliente. O rollback consiste em parar somente a Engine, preservar `ProgramData`, restaurar o binário anterior e executar a reconciliação da fila. Alterações em schema são versionadas e idempotentes; nenhuma migração remove dados.

## Referências

[1]: https://github.com/ASOARESBH/VOXEL_ROUTER_DESKTOP "Repositório do VOXEL Router Desktop"
[2]: https://www.orthanc-server.com/static.php?page=users-manual "Orthanc Book — User Manual"
[3]: https://pydicom.github.io/pynetdicom/stable/ "pynetdicom documentation"
[4]: https://fastapi.tiangolo.com/ "FastAPI documentation"
[5]: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html "OWASP Authentication Cheat Sheet"
