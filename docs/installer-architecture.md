# Arquitetura de instalação — VOXEL Router Desktop

## Objetivo

O artefato único `VOXEL_ROUTER_SETUP.exe` instala, configura e valida dois componentes independentes: `VOXELRouter.exe`/`VOXELRouterService.exe` e `Orthanc.exe`/`VOXELOrthancService.exe`. O Orthanc permanece um processo separado, controlado pelo Windows Service Control Manager; ele não é incorporado ao processo Python do Router.

| Componente | Processo instalado | Serviço Windows | Interfaces |
|---|---|---|---|
| VOXEL Router | `VOXELRouter.exe` e `VOXELRouterService.exe` | `VOXELRouter` / **VOXEL Router** | DICOM SCP `4242/TCP`; administração `127.0.0.1:8765` |
| Orthanc | `orthanc\Orthanc.exe` e `VOXELOrthancService.exe` | `VOXELOrthanc` / **VOXEL Orthanc** | DICOM `4243/TCP`; REST `127.0.0.1:8042` |

## Fluxo idempotente

O instalador detecta previamente binários, configuração persistente, storage e ambos os serviços. Durante atualização, os arquivos de programa são atualizados, enquanto `C:\ProgramData\VOXEL\Router` permanece intacto. O arquivo `orthanc.json` só é gerado quando inexistente ou quando for solicitada uma reconfiguração explícita; a configuração e o banco do Orthanc não são sobrescritos em atualização.

```text
Instalar Router → validar binários → instalar Orthanc → validar binário
→ criar diretórios persistentes → gerar configuração ausente
→ registrar serviço Orthanc → iniciar Orthanc → validar REST/porta 8042
→ registrar serviço Router → iniciar Router → validar saúde/porta 8765
→ executar diagnóstico de portas, storage e serviços → concluir
```

O serviço Orthanc é registrado antes do Router. O Router continua independente: quando o Orthanc está indisponível, o monitor de saúde expõe `OFFLINE`, sem que o processo Python tente iniciar ou incorporar o binário Orthanc.

## Dados, atualização e rollback

Os diretórios persistentes são `config`, `database`, `logs`, `queue`, `orthanc\storage` e `orthanc\database`, todos sob `C:\ProgramData\VOXEL\Router`. A instalação e a atualização usam `onlyifdoesntexist` para a configuração inicial e nunca removem dados clínicos. A desinstalação normal para ambos os serviços e preserva os dados; exclusão exige opção explícita do operador.

Caso a validação crítica falhe, o instalador registra uma mensagem diagnóstica, apresenta a opção de repetir a inicialização e não sinaliza conclusão. O rollback operacional consiste em parar apenas o Router, preservar integralmente `ProgramData`, restaurar binários homologados e iniciar novamente o Orthanc antes do Router.

## Checks finais

A conclusão somente é exibida após confirmar presença dos binários, serviços em execução, diretórios persistentes, listeners `4242`, `4243`, `8042` e `8765`, `/health` do Router e `/system` do Orthanc. O check Orthanc consulta a REST API real autenticada; não há estado mock no endpoint de saúde.

## Segurança e limites

A REST do Orthanc permanece limitada a `127.0.0.1`, e o firewall só expõe as portas DICOM explicitamente necessárias. Credenciais internas permanecem no armazenamento seguro já existente e não aparecem no instalador, arquivos versionados ou logs. A validação completa requer estação Windows homologada, binário Orthanc aprovado, certificados e privilégios de administrador.
