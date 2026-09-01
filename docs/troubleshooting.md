# Troubleshooting — VOXEL Router Desktop

Este guia orienta diagnóstico operacional sem apagar estudos, expor credenciais ou registrar dados clínicos em tickets. Sempre anote **Router ID**, versão, horário, código de erro e Study UID sintético/operacional. Preserve `C:\ProgramData\VOXEL\Router` antes de qualquer rollback.

| Sintoma | Verificação segura | Ação inicial |
|---|---|---|
| Interface indisponível | `http://127.0.0.1:8765/health` | Verificar processo/serviço da API e logs locais; não abrir porta administrativa na rede |
| SCP offline | `/health/dicom` e `sc query VOXELRouterEngine` | Confirmar porta configurada, listener e conflito de porta; reiniciar a Engine de forma controlada |
| Orthanc offline | `/health/orthanc` e serviço Orthanc | Consultar logs do Orthanc, armazenamento e configuração local; não apagar índice ou storage |
| Cloud indisponível | `/health/cloud`, DNS e rota ao destino | Manter fila; validar TLS/AE Title/porta; não reenviar manualmente em massa sem causa conhecida |
| Fila em retry | Tela Fila e `queue_attempts` | Examinar código do último erro, restaurar destino e permitir retry controlado |
| Disco crítico | Dashboard de storage | Ampliar capacidade ou aplicar retenção aprovada apenas a estudos validados |
| Login negado | Auditoria e hora do host | Usar mensagem genérica; aguardar janela de bloqueio ou seguir procedimento administrativo aprovado |

## DICOM SCP sem associação

Confirme se a modalidade usa o AE Title local correto, padrão `VOXEL_ROUTER`, e porta TCP configurada, padrão `4242`. Valide `C-ECHO` antes de C-STORE. Revise a regra de firewall `VOXEL Router DICOM SCP` e assegure que ela se aplica somente ao perfil privado aprovado. Não abra a porta da API de administração, que deve permanecer em loopback.

Se a associação chega, mas C-STORE falha, revise o código de retorno DICOM e os logs das categorias `DICOM` e `ORTHANC`. UIDs inválidos, arquivo corrompido ou Orthanc indisponível devem impedir a confirmação indevida. Preserve o objeto original e não altere sua codificação manualmente.

## Orthanc indisponível

Verifique primeiro `sc query "VOXEL Orthanc Service"`, o caminho de storage e espaço livre. Confirme que `orthanc.json` foi gerado pela ferramenta do Router e que o serviço HTTP continua limitado ao host local. Credenciais internas do Orthanc ficam no DPAPI; não substitua o arquivo por configuração de exemplo com usuário e senha conhecidos.

Após a recuperação do Orthanc, reinicie somente o serviço afetado ou a Engine conforme procedimento de mudança, então valide `/health/orthanc`, C-ECHO e uma transmissão sintética. A Engine deve reconstruir itens pendentes; não altere registros SQLite diretamente.

## Cloud ou destino DICOM indisponível

O comportamento esperado é `RETRY` com espera crescente e número finito de tentativas. Confirme DNS, conectividade TCP, AE Title chamado, certificação TLS, horário do sistema e se o destino continua habilitado. `CLOUD_UNAVAILABLE` significa que o estudo permanece localmente armazenado; não significa perda de estudo.

Depois de corrigir a conectividade, use **Reenviar** somente no item necessário ou aguarde o retry. Evite cancelar itens sem confirmar que o estudo já existe e foi validado no destino.

## Fila parada ou após reinício

A Engine muda itens `SENDING` para `RETRY` no boot porque não pode provar que uma transmissão interrompida foi concluída. Essa escolha prioriza confiabilidade, podendo gerar novo envio; a deduplicação do destino e as confirmações devem lidar com essa condição. Verifique `queue_attempts`, `transfers` e `system_events` antes de qualquer ação manual.

Quando houver inconsistência entre SQLite e Orthanc, pare novas transmissões, crie backup de `database`, `config` e `certificates`, colete logs e execute o procedimento de reconciliação homologado. Não apague diretórios de objetos para “limpar” a fila.

## Storage crítico

Nos limiares padrão, o Router sinaliza aviso a 70%, crítico a 85% e emergência a 95%. O ajuste desses valores deve ser auditado. A exclusão automática só pode alcançar instâncias pertencentes a estudos `VALIDATED` e depois do período de retenção aprovado. Se for necessário liberar espaço imediatamente, siga o processo clínico-operacional institucional e registre a autorização.

## Coleta de evidência

Anexe somente logs sanitizados de `C:\ProgramData\VOXEL\Router\logs\router.jsonl`, status de serviços, resultados de health checks e metadados operacionais. Remova ou masque qualquer nome de paciente, CPF, endereço, token, cookie, senha, certificado e conteúdo DICOM antes de compartilhar.

## Referências

[1]: https://www.orthanc-server.com/static.php?page=users-manual "Orthanc Book — User Manual"
[2]: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html "OWASP Logging Cheat Sheet"
[3]: https://www.dicomstandard.org/current/ "DICOM Standard"
