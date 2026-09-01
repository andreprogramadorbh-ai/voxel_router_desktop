# Integração Non-DICOM — desenho do módulo

## Escopo confirmado

O módulo é **aditivo** ao VOXEL Router. Ele monitora XMLs de metadados no padrão Philips, cria tarefas persistentes, processa documentos fora da thread de monitoramento e os entrega por um cliente HTTP configurável do VOXEL PACS. Ele não altera o SCP DICOM, AE Title `VOXEL_ROUTER`, porta `4242`, fila DICOM, Orthanc ou regras de transmissão de estudos.

| Fonte | Fato confirmado |
|---|---|
| Documento Philips V2 | Suporta XML `submission/document` com referência a arquivo e `WTT_ITEM` com `REPORT_BASE64`; `task_document_type=11502-2` identifica Report/SR. |
| Apresentação Philips | O serviço amostra um diretório de entrada, enfileira tarefas assíncronas e move XML para concluídos ou falhos. |
| Especificação do produto | Exige diretórios persistentes, proteção de path traversal, retry limitado, API local protegida, cliente de cloud configurável e UI administrativa. |

## Contratos e limites

| Camada | Contrato | Decisão configurável |
|---|---|---|
| Parser Philips | `PhilipsSubmissionParser` aceita `submission/document`; `PhilipsWttItemParser` aceita `WTT_ITEM`; campos desconhecidos são preservados em JSON. | Campos obrigatórios mínimos e MIME permitidos. |
| Armazenamento | Todos os paths de trabalho ficam abaixo de `non_dicom.root_path`; arquivos gerenciados só podem ser resolvidos sob `files/`. | `LOCAL_PATH` permite arquivo absoluto somente em raízes allowlisted; `VOXEL_MANAGED_FILE` é padrão. |
| Fila | `non_dicom_submissions` persiste estado, tentativas, próximo processamento e erros; `non_dicom_events` registra transições sanitizadas. | Intervalo de polling, máximo de tentativas e atrasos de retry. |
| Cloud | `NonDicomCloudClient` consulta status e envia a endpoints de configuração, com token do cofre de segredos. | URL, paths de status/consulta/envio/ack/status, TLS e timeout. |
| UI/API | Endpoints `/api/non-dicom/*` exigem sessão com senha inicial alterada. | Root local e demais parâmetros não secretos. |

## Estados e transições

```text
RECEIVED → VALIDATING → PENDING → PROCESSING → COMPLETED
                                     │
                                     └→ RETRYING → PROCESSING
                                                   │
                                                   └→ FAILED
```

Uma tentativa transitória falha em `RETRYING` até atingir o máximo configurado. A falha terminal move o XML a `failed/`; sucesso move-o a `completed/`. O arquivo é removido somente depois de confirmação de sucesso, apenas se for gerenciado pelo VOXEL e se `delete_file_after_success` estiver habilitado. O rollback é operacional: pausar o worker, preservar banco/XML/arquivo, corrigir configuração ou conectividade e usar reprocessamento explícito.

## Impacto e segurança

| Item atingido | Risco | Controle |
|---|---|---|
| SQLite existente | Concorrência e estados órfãos após reinício. | Tabelas irmãs, transações `IMMEDIATE`, claim atômico e recuperação de `PROCESSING`. |
| Dados identificáveis | PHI em XML e metadados. | Armazenamento somente local, logs sanitizados e auditoria por ID da tarefa. |
| Caminhos de arquivo | Path traversal e UNC não autorizado. | Normalização, raiz allowlisted, filename sanitizado e bloqueio fora do diretório gerenciado. |
| Cloud VOXEL PACS | URL ou credencial não definida. | Cliente desacoplado, sem URL fixa, timeout/TLS e falha controlada em retry. |
| Fluxos clínicos existentes | Regressão DICOM ou Orthanc. | Nenhum módulo DICOM/Orthanc é chamado ou alterado pelo worker Non-DICOM. |

## Operação futura

O worker executa dentro do ciclo de vida atual do Router, mas seus contratos não dependem de navegador. `NonDicomWorker`, parsers, storage, fila e cliente cloud são módulos separados; podem ser hospedados posteriormente por um serviço Windows dedicado sem alterar o schema nem a API.

## Referências

[1]: `DocumentoNaoDicomPhilips_V2.pdf`, Philips, 2024.
[2]: `Algotec_R&D_PACS_Servers_Group_Non_Dicom_Auto_Ingestion.ppt`, Algotec R&D PACS Servers Group.
