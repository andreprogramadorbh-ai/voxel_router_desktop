# Instalação Windows — VOXEL Router Desktop

Este guia é destinado ao time de TI da VOXEL. A implantação requer estação Windows x64 homologada, privilégios de administrador **somente durante instalação** e conectividade com as modalidades e destinos necessários. Após a instalação, os processos de operação devem usar contas de serviço com privilégio mínimo.

## Pré-validação

| Verificação | Critério de aceite |
|---|---|
| Sistema operacional | Windows x64 suportado pela política VOXEL e atualizado |
| Disco | Capacidade reservada para retenção local, mais margem para incidentes de conectividade |
| Rede DICOM | A modalidade alcança o IP do Router na porta SCP configurada; padrão `4242/TCP` |
| Rede cloud | O Router alcança somente os endpoints/destinos aprovados; TLS/certificados homologados quando aplicável |
| Orthanc | Pacote binário e plugins homologados, licenças verificadas, sem configuração de teste |
| Segurança | Senha de bootstrap fornecida por canal autorizado; nunca anexada a ticket, script versionado ou log |

## Instalação interativa

Execute `VOXEL_ROUTER_SETUP.exe` como administrador. O instalador instala os binários em `C:\Program Files\VOXEL\Router` e cria os dados persistentes em `C:\ProgramData\VOXEL\Router`. A separação é intencional: banco, filas, estudos, logs, certificados e backups **não** devem ficar em `Program Files`.

Após a cópia, o instalador registra `VOXEL Router Engine`, instala ou configura `VOXEL Orthanc Service`, habilita reinício do Router Engine após falha e cria somente a regra privada de firewall `VOXEL Router DICOM SCP` para a porta 4242. Não há regra de firewall para a interface administrativa: a API é publicada em loopback.

## Instalação silenciosa

Para ferramentas de gestão corporativa, execute:

```text
VOXEL_ROUTER_SETUP.exe /S
```

O uso do modo silencioso exige pré-provisionamento controlado. Depois de instalar, utilize o mecanismo organizacional de segredos para definir `VOXEL_ROUTER_BOOTSTRAP_PASSWORD` no processo de provisionamento e remova a variável logo após sua utilização. Nunca passe senhas em argumento de linha de comando.

```powershell
$env:VOXEL_ROUTER_BOOTSTRAP_PASSWORD = '<segredo aprovado>'
& 'C:\Program Files\VOXEL\Router\VOXELRouter.exe' # inicia a interface local
# Executar provisionamento pelo mecanismo de deployment aprovado e remover o segredo imediatamente.
Remove-Item Env:VOXEL_ROUTER_BOOTSTRAP_PASSWORD
```

## Primeira configuração

Na primeira abertura em [http://127.0.0.1:8765](http://127.0.0.1:8765), a interface exibe a tela de provisionamento local. Informe `voxeladmin` ou o usuário de administração aprovado e a senha de bootstrap. O primeiro login exige a troca imediata da senha. A senha é protegida por Argon2id; o produto não possui senha de operação codificada.

Configure o AE Title e porta locais, em seguida cadastre modalidades permitidas e destino DICOM/VOXEL Cloud. Use C-ECHO para validar as associações antes de liberar C-STORE em produção. Quando TLS estiver habilitado, instale certificados sob `C:\ProgramData\VOXEL\Router\certificates` com ACL restritiva e valide a cadeia de confiança.

## Serviços e dados

| Item | Nome/caminho | Verificação |
|---|---|---|
| Engine | `VOXEL Router Engine` | `sc query VOXELRouterEngine` |
| Orthanc | `VOXEL Orthanc Service` | `sc query "VOXEL Orthanc Service"` |
| API administrativa | `127.0.0.1:8765` | `http://127.0.0.1:8765/health` |
| SCP DICOM | `0.0.0.0:4242` padrão | C-ECHO a partir de nó autorizado |
| SQLite | `ProgramData\VOXEL\Router\database\voxel_router.db` | Não copiar com a Engine em execução sem backup consistente |
| Logs | `ProgramData\VOXEL\Router\logs\router.jsonl` | Confirmar que não contém senha/token/PHI desnecessário |
| Dados temporários | `ProgramData\VOXEL\Router\storage` | Confirmar limites e retenção |

## Atualização e rollback

Interrompa novas alterações administrativas, confirme a saúde da fila e faça backup consistente de `config`, `database` e `certificates`. Instale a versão assinada sobre a anterior sem remover `ProgramData`. Após o serviço iniciar, valide `/health`, C-ECHO e a presença dos itens pendentes da fila. Caso seja necessário rollback, pare somente a Engine, preserve integralmente `ProgramData`, reinstale o binário anterior aprovado e execute a reconciliação de fila. Não exclua estudos para tentar corrigir uma atualização.

## Desinstalação

A desinstalação deve perguntar se os dados locais serão preservados. A opção padrão é manter dados. Só autorize remoção após confirmar que estudos pendentes foram enviados e validados ou após executar a retenção institucional aprovada. A eliminação de estudos sem confirmação explícita é vedada.

## Referências

[1]: https://www.orthanc-server.com/static.php?page=users-manual "Orthanc Book — User Manual"
[2]: https://learn.microsoft.com/windows-server/administration/windows-commands/sc-query "sc query — Microsoft Learn"
[3]: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html "OWASP Secrets Management Cheat Sheet"
