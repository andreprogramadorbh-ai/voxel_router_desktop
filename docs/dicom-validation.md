# Validação DICOM e Continuidade Operacional

A homologação deve ocorrer em ambiente de teste que represente a topologia do cliente. Não execute cenários de falha em produção e não use estudos com informações clínicas reais para testes. O Router registra identificadores operacionais; logs não devem conter nome completo, endereço, CPF ou conteúdo clínico.

> A suíte local comprova C-ECHO e C-STORE contra o SCP em porta efêmera. A presente validação comprova a cadeia operacional completa com Orthanc e destino configurados.

## Preparação

Cadastre uma modalidade de teste, o destino DICOM homologado e um estudo sintético. Confirme AE Titles, IPs, portas, política de TLS e certificados antes de iniciar. Ajuste temporariamente a janela de completude para 30 segundos ou valor aprovado, mantendo em mente que o Router só deve criar a transmissão depois que novas instâncias deixarem de chegar.

| Campo | Origem | Evidência |
|---|---|---|
| Calling/Called AE Titles | Cadastro de modalidade, Router e destino | C-ECHO com `0x0000` |
| Porta do SCP | Configuração DICOM local | Regra de firewall mínima e listener ativo |
| Orthanc | Serviço local e `/health/orthanc` | Estado `ONLINE` |
| Destino VOXEL/DICOM | Cadastro de destino | Estado conectado ou C-ECHO aprovado |
| Storage | Dashboard e capacidade reservada | Abaixo do limiar de aviso configurado |

## Cenário A — C-ECHO

Envie C-ECHO da modalidade de teste ao AE Title do Router. O status precisa retornar `0x0000`; o dashboard deve mostrar `DICOM SCP: LISTENING`. Em seguida, execute C-ECHO da Engine ao destino configurado antes de liberar o envio de estudos.

## Cenário B — Cadeia Store-and-Forward

Envie estudo sintético com mais de uma série e instância por C-STORE. Confirme que a associação retorna sucesso, o Orthanc local lista as instâncias, e a tela de monitoramento mostra Study UID, número de séries, instâncias e tamanho. Após a janela de silêncio, valide as seguintes transições:

```text
RECEIVED → READY_TO_SEND → QUEUED → SENDING → VALIDATED
```

O destino deve conter o estudo completo. Compare o total de instâncias e verifique o histórico de transferência e a confirmação C-STORE. Confirme que o checksum SHA-256 existe por objeto e que `ModalitiesInStudy` é composto pelas modalidades distintas das séries.

## Cenário C — Internet indisponível

Envie um exame sintético, bloqueie temporariamente a rota de saída para o destino ou desabilite a conectividade de teste. O Router pode registrar `CLOUD_UNAVAILABLE`, mas não pode excluir o estudo, a instância, o registro SQLite nem a entrada de fila. A recepção local por C-STORE deve continuar disponível enquanto Orthanc e o SCP estiverem saudáveis.

Restaure a conectividade e confirme que o item migra de `RETRY` para `SENDING` e, após confirmação, `VALIDATED`. Compare o checksum local antes e após o período offline.

## Cenário D — Reinício de serviço e Windows

Com pelo menos um item em `QUEUED`, `RETRY` ou `SENDING`, reinicie o serviço `VOXEL Router Engine` ou a estação de teste. Ao voltar, a Engine executa banco, storage, reconciliação e reconstrução de fila. Qualquer item que estivesse em `SENDING` deve voltar a `RETRY`, com nova tentativa controlada; o estudo não pode desaparecer.

Depois, reinicie `VOXEL Orthanc Service` e confirme que a Engine continua em operação, que o status muda temporariamente para `OFFLINE`, e que nova transmissão somente ocorre depois de o Orthanc responder novamente.

## Cenário E — Duplicidade e corrupção

Reenvie uma instância com o mesmo SOP Instance UID. O Router deve retornar sucesso sem criar segunda instância nem aumentar o total indevidamente; o evento de deduplicação deve ser registrado sem PHI em log. Para corrupção, apresente um objeto DICOM sintético inválido ou incompleto; o SCP precisa rejeitar a associação/armazenamento com status de erro e manter o estado existente intacto.

## Cenário F — Disco

Use um volume de teste limitado ou limiares temporariamente baixos para ultrapassar os patamares de aviso, crítico e emergência. O dashboard deve sinalizar `WARNING`, `CRITICAL` e `EMERGENCY` conforme configurado. Nenhuma rotina de retenção deve remover estudo não validado. Depois de concluir a validação, restaure a política institucional e a capacidade de storage.

## Critérios de aceite

| Critério | Resultado esperado |
|---|---|
| C-ECHO local | Sucesso DICOM `0x0000` |
| C-STORE local | Estudo e instâncias armazenados, checksum calculado e Orthanc confirmado |
| Queda de cloud | Estudo preservado, retry limitado e sem perda de fila |
| Reinício | Itens persistentes reconstruídos, nenhum envio fica preso em memória |
| Duplicidade | SOP Instance UID não é duplicado e há evento operacional |
| Storage | Alertas aparecem nos limiares configurados |
| Privacidade | Logs sem senha, token e PHI não necessária |

Registre data, versão, Router ID, operador de TI, IDs de estudo sintético e evidências do resultado em controle interno. Não registre segredos nem dados identificáveis de pacientes.

## Referências

[1]: https://pydicom.github.io/pynetdicom/stable/ "pynetdicom documentation"
[2]: https://www.dicomstandard.org/current/ "DICOM Standard"
[3]: https://www.orthanc-server.com/static.php?page=users-manual "Orthanc Book — User Manual"
