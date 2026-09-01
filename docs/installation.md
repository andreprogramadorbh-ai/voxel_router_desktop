# Instalação Windows — VOXEL Router Desktop

Este guia é destinado ao time de TI da VOXEL. A implantação requer estação Windows x64 homologada, privilégios de administrador **somente durante instalação** e conectividade com as modalidades e destinos necessários. Após a instalação, os processos operam como serviços Windows separados, com privilégios mínimos.

## Pré-validação

| Verificação | Critério de aceite |
|---|---|
| Sistema operacional | Windows x64 suportado pela política VOXEL e atualizado. |
| Disco | Capacidade reservada para retenção local, Orthanc e margem para indisponibilidade de conectividade. |
| Rede DICOM | Modalidades alcançam o Router em `4242/TCP`; o Orthanc utiliza `4243/TCP` somente quando aprovado no cenário local. |
| Rede cloud | O Router alcança exclusivamente os endpoints/destinos aprovados; TLS e certificados são homologados quando aplicável. |
| Orthanc | Binário e plugins homologados estão incluídos no instalador; não é necessária instalação manual. |
| Segurança | Senha de bootstrap é fornecida por canal autorizado; nunca por ticket, script versionado ou log. |

## Instalador único

Execute `VOXEL_ROUTER_SETUP.exe` como administrador. O instalador inclui dois binários independentes: `VOXELRouter.exe`/`VOXELRouterService.exe` e `Orthanc.exe`/`VOXELOrthancService.exe`. O Orthanc nunca é incorporado ao processo Python do Router, mas é instalado pelo mesmo pacote.

A instalação detecta Router, Orthanc, `orthanc.json`, storage e serviços já existentes. Em atualização, os binários são atualizados sem reinstalação desnecessária do Orthanc válido e sem remoção de dados persistentes.

```text
Instalar Router → confirmar binários → instalar Orthanc → confirmar binário
→ criar storage/configuração → criar serviço Orthanc → iniciar e verificar Orthanc
→ criar serviço Router → iniciar e verificar Router → diagnóstico final → conclusão
```

O instalador só declara conclusão após verificar binários, serviços, armazenamento, portas e health checks. Se o Orthanc falhar, o operador pode tentar novamente ou abrir o atalho **Diagnóstico de instalação**. O Dashboard não é aberto automaticamente enquanto uma validação crítica estiver pendente.

## Instalação silenciosa

Para ferramentas de gestão corporativa, execute:

```text
VOXEL_ROUTER_SETUP.exe /S
```

A instalação silenciosa cria a configuração inicial e serviços, mas o provisionamento da credencial administrativa continua sob controle do processo de TI aprovado. Nunca passe senhas na linha de comando.

```powershell
$env:VOXEL_ROUTER_BOOTSTRAP_PASSWORD = '<segredo aprovado>'
python 'C:\Program Files\VOXEL\Router\scripts\provision_admin.py' --username voxeladmin --non-interactive
Remove-Item Env:VOXEL_ROUTER_BOOTSTRAP_PASSWORD
```

## Primeira configuração

Após a saúde do Router ser aprovada, abra [http://127.0.0.1:8765](http://127.0.0.1:8765). Na primeira abertura, a interface exibe somente o provisionamento local. Informe `voxeladmin` ou o usuário de administração aprovado e a senha de bootstrap. O primeiro login exige a troca imediata da senha; senhas são protegidas por Argon2id e nunca são gravadas em texto puro.

## Serviços, portas e dados

| Item | Serviço/caminho | Verificação |
|---|---|---|
| Router | `VOXELRouter` / **VOXEL Router** | `sc query VOXELRouter` |
| Orthanc | `VOXELOrthanc` / **VOXEL Orthanc** | `sc query VOXELOrthanc` |
| API administrativa | `127.0.0.1:8765` | `http://127.0.0.1:8765/health` |
| SCP DICOM Router | `0.0.0.0:4242` padrão | C-ECHO a partir de nó autorizado. |
| DICOM Orthanc | `4243/TCP` | Listener local do serviço Orthanc. |
| REST Orthanc | `127.0.0.1:8042` | `http://127.0.0.1:8042/system`, autenticado internamente. |
| Configuração | `ProgramData\VOXEL\Router\config` | `router.json` e `orthanc.json` preservados em update. |
| Storage Orthanc | `ProgramData\VOXEL\Router\orthanc\storage` | Nunca excluir em atualização ou desinstalação normal. |
| Índice Orthanc | `ProgramData\VOXEL\Router\orthanc\database` | Nunca excluir em atualização ou desinstalação normal. |
| Fila e logs | `ProgramData\VOXEL\Router\queue` e `logs` | Preservar para recuperação e auditoria. |

Não há regra de firewall para a interface administrativa ou REST Orthanc, que usam loopback. As regras de firewall são limitadas às portas DICOM privadas necessárias.

## Atualização, rollback e desinstalação

Interrompa alterações administrativas, valide a fila e faça backup consistente de `config`, `database` e `certificates`. Instale a versão assinada sobre a anterior sem remover `ProgramData`. Após reinício, consulte `/health`, valide Orthanc em `/health/orthanc`, teste C-ECHO e confirme os itens pendentes de fila.

O rollback consiste em parar somente o Router, preservar integralmente `ProgramData`, restaurar binários homologados e iniciar primeiro o Orthanc, depois o Router. A desinstalação normal remove serviços e binários, mas preserva por padrão `config`, `orthanc\storage`, `orthanc\database`, `queue`, `logs` e quaisquer estudos DICOM. A remoção desses dados exige processo operacional separado e autorização explícita.

## Referências

[1]: https://orthanc.uclouvain.be/book/users/configuration.html "Orthanc Book — Configuration"
[2]: https://learn.microsoft.com/windows-server/administration/windows-commands/sc-query "sc query — Microsoft Learn"
[3]: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html "OWASP Secrets Management Cheat Sheet"
