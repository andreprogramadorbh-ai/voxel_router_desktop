# VOXEL Router Desktop

O **VOXEL Router Desktop** é um *DICOM Edge Router* para Windows. Ele recebe C-STORE de modalidades locais, persiste o estudo em armazenamento temporário e SQLite, controla uma fila recuperável e transmite a destinos DICOM de forma desacoplada. A interface de administração é local e, por padrão, a API só escuta em `127.0.0.1`.

> **Princípio fundamental:** a modalidade entrega o estudo uma vez; o Router mantém o estado persistido até o destino confirmar o recebimento e a validação configurada.

O repositório começou vazio. Esta entrega estabelece o MVP de código-fonte, com arquitetura, serviços centrais, interface, testes automatizados e receita de empacotamento Windows. O executável e instalador finais precisam ser gerados no ambiente Windows homologado, pois dependem do Orthanc binário aprovado, `pywin32`, assinatura de código e Inno Setup.

## Capacidades do MVP

| Área | Entregue |
|---|---|
| Segurança | Senhas com **Argon2id**, sessão revogável com token armazenado como hash, limite de tentativas, troca obrigatória no primeiro login, auditoria e redaction de segredos/PHI nos logs |
| DICOM | SCP com **C-ECHO** e **C-STORE**, validação de UIDs, checksum SHA-256, deduplicação por SOP Instance UID, modalidades derivadas das séries e SCU C-STORE para destino |
| Persistência | SQLite em WAL/FULL, transações `IMMEDIATE`, estado de estudo/instância/série, fila, tentativas, transferências, erros, auditoria e eventos |
| Resiliência | Fila persistente, recuperação de itens `SENDING` após reinício, janela de completude de estudo, retry limitado configurável, retenção apenas de estudos validados |
| Orthanc | Cliente REST de saúde, ingestão e consulta; gerador de `orthanc.json`; configuração do serviço Windows no instalador |
| Interface | Login, provisionamento inicial, dashboard de saúde/fila/storage, estudos, fila, SCP DICOM, modalidades, destinos, logs, auditoria e configurações |
| Windows | Build PyInstaller, host de serviço `VOXEL Router Engine`, Inno Setup, diretórios corretos e regra de firewall limitada à porta DICOM |

## Estrutura

```text
app/                 Backend FastAPI e Router Engine
  api/               API local protegida
  auth/              Argon2id, sessões e proteção contra força bruta
  cloud/             Contrato CloudConnector e destino DICOM SCU
  config/            Configuração e caminhos operacionais
  core/              Banco, logs e orquestração da Engine
  dicom/             Ingestor SHA-256 e SCP C-ECHO/C-STORE
  monitoring/        Health checks e disco
  orthanc/           REST API e controle de serviço
  queue/             Fila persistente e recuperação
  security/          DPAPI e cofre de segredos
  transfer/          Worker concorrente de transmissão
frontend/            SPA local administrativa
installer/           Script Inno Setup
scripts/             Build, provisionamento e configuração Orthanc
tests/               Testes unitários e integração DICOM
config/default.json  Configuração padrão segura
docs/                Arquitetura, instalação, DICOM e troubleshooting
```

## Pré-requisitos de desenvolvimento

Use Python **3.12+**. Em Linux/macOS, a execução é somente para desenvolvimento e testes; o DPAPI e serviços são específicos do Windows.

```powershell
py -3.12 -m pip install ".[dev]"
$env:VOXEL_ROUTER_DATA_DIR = "$PWD\.local-data"
python -m pytest -q
```

Em desenvolvimento fora do Windows, somente para testar a geração de configuração Orthanc, gere uma chave Fernet transitória:

```powershell
$env:VOXEL_ROUTER_DEV_SECRET_KEY = python -c "from app.security.secrets import create_development_key; print(create_development_key())"
python scripts/configure_orthanc.py
```

> A variável `VOXEL_ROUTER_DEV_SECRET_KEY` não é mecanismo de produção. Em Windows, `WindowsSecretStore` usa DPAPI e mantém o segredo fora da interface e do SQLite.

## Provisionamento de acesso

A especificação define o usuário inicial `voxeladmin`. Por segurança, nenhuma senha inicial é incluída no fonte, no instalador, na configuração ou nos logs. O time de TI deve provisionar a senha de bootstrap no primeiro acesso pela tela local ou pelo utilitário abaixo. A aplicação força a troca dessa senha no primeiro login.

```powershell
python scripts/provision_admin.py --username voxeladmin
```

Para implantação controlada e silenciosa, o orquestrador de TI deve fornecer a senha em memória/processo e removê-la do ambiente após a execução:

```powershell
$env:VOXEL_ROUTER_BOOTSTRAP_PASSWORD = '<senha provisionada pelo time de TI>'
python scripts/provision_admin.py --username voxeladmin --non-interactive
Remove-Item Env:VOXEL_ROUTER_BOOTSTRAP_PASSWORD
```

## Execução local

A API local usa `127.0.0.1:8765` por padrão. A Engine inicia SCP DICOM em `0.0.0.0:4242` para possibilitar o envio pelas modalidades. A porta, AE Title, timeout, associações e janela de completude são configuráveis no arquivo local ou pela interface, com reinício controlado do serviço quando necessário.

```powershell
python -m app.main
```

Abra [http://127.0.0.1:8765](http://127.0.0.1:8765). Sem administrador provisionado, a página mostra exclusivamente a primeira configuração.

## Orthanc

Coloque somente o pacote **Orthanc homologado pela VOXEL** em `vendor/orthanc/` no ambiente de build. Gere a configuração antes de instalar o serviço:

```powershell
python scripts/configure_orthanc.py
```

O arquivo gerado restringe o acesso HTTP ao host local, habilita autenticação do usuário interno do Router, aplica `DicomCheckCalledAet` e não contém senha pré-fixada. O binário exato, plugins, licenças, certificados DICOM TLS e política de versões devem ser homologados no ambiente clínico antes de produção.

## Build e instalação Windows

Em uma máquina de build Windows x64 com Python 3.12, Inno Setup 6, Orthanc homologado e ferramentas de assinatura:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

O resultado esperado é `dist\VOXEL_ROUTER_SETUP.exe`. A instalação provisiona binários em `C:\Program Files\VOXEL\Router` e dados em `C:\ProgramData\VOXEL\Router`. Para implantação sem interface:

```text
VOXEL_ROUTER_SETUP.exe /S
```

A desinstalação deve preservar os dados por padrão e requer escolha explícita para apagar estudos e estado local.

## Testes e cenários validados

A suíte automatizada cobre autenticação, troca obrigatória de senha, limitação de tentativas, checksum, deduplicação, modalidades por série, fila e recuperação pós-reinício. Há também integração de rede com **C-ECHO e C-STORE reais** por `pynetdicom` contra o SCP local em porta efêmera.

```powershell
pytest -q
```

Antes da liberação clínica, execute o protocolo completo em uma estação Windows homologada descrito em [docs/dicom-validation.md](docs/dicom-validation.md), incluindo Orthanc real, destino real ou simulador validado, perda de conectividade, reinício do serviço e reconciliação posterior.

## Documentação

| Documento | Conteúdo |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Camadas, fluxo de dados, persistência, segurança, rollback e implantação |
| [docs/installation.md](docs/installation.md) | Processo de instalação Windows, configuração, serviços e firewall |
| [docs/dicom-validation.md](docs/dicom-validation.md) | Protocolo de C-ECHO/C-STORE e cenários de continuidade operacional |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Diagnóstico seguro de Orthanc, DICOM, fila, cloud e storage |

## Referências

[1]: https://www.orthanc-server.com/static.php?page=users-manual "Orthanc Book — User Manual"
[2]: https://pydicom.github.io/pynetdicom/stable/ "pynetdicom documentation"
[3]: https://fastapi.tiangolo.com/ "FastAPI documentation"
[4]: https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html "OWASP Authentication Cheat Sheet"
[5]: https://github.com/ASOARESBH/VOXEL_ROUTER_DESKTOP "Repositório VOXEL Router Desktop"
