# Teste local do instalador (máquina de desenvolvimento) — antes da clínica

Objetivo: gerar o primeiro `VOXEL_ROUTER_SETUP.exe`, instalar numa máquina Windows da equipe e validar
recepção, envio e a página VOXEL VPN **sem a célula real**. Um destino DICOM de teste simula o VOXEL.
Tudo com estudo **sintético**.

> Atenção: a instalação cria os serviços `VOXELRouter` e `VOXELOrthanc`, instala o WireGuard for Windows
> (se ausente) e abre a `4242/TCP` no firewall (perfis Domínio/Privada). Portas usadas: 4242, 8042 (só
> loopback) e 8765 (só loopback). Confira que nada mais na máquina usa essas portas.

## 1. Gerar o instalador (PowerShell normal, sem administrador)

```powershell
cd C:\xampp\htdocs\dashboard\voxel_router_desktop-1
git fetch origin
git checkout -f feature/router-fases-5-8
powershell -ExecutionPolicy Bypass -File .\scripts\build_local_test.ps1
```

O script:
1. confere Python 3.12 e Inno Setup 6 (se faltar, mostra o `winget install` correspondente);
2. baixa o **Orthanc 1.13.0** oficial (`orthanc.uclouvain.be`) e o **WireGuard 1.1** MSI oficial (`download.wireguard.com`), confere a assinatura do MSI e mostra os SHA-256 — responda **S** para aprovar;
3. roda `ruff` e `pytest` no Windows;
4. gera `dist\VOXEL_ROUTER_SETUP.exe` e `dist\VOXEL_ROUTER_SETUP.exe.sha256`.

Log completo: `logs-local\build-local-<data>.log`. Os hashes aprovados ficam em `vendor\HASHES-homologacao.txt`.

## 2. Liberar as portas (máquina com outro Orthanc)

O instalador recusa instalação nova se a `4242` ou a `8042` já estiverem ocupadas. No server-dell o serviço
**Orthanc** (instalação própria) escuta nas duas. Para o teste, em PowerShell **como administrador**:

```powershell
sc.exe stop Orthanc
sc.exe config Orthanc start= demand   # não volta sozinho no reboot do teste de resiliência
```

Ao terminar os testes (passo 8), devolva: `sc.exe config Orthanc start= auto; sc.exe start Orthanc`
(depois de desinstalar o VOXEL Router ou parar `VOXELRouter`/`VOXELOrthanc`, que usam as mesmas portas).

## 2b. Instalar

Clique com o botão direito em `dist\VOXEL_ROUTER_SETUP.exe` → Executar como administrador
(o SmartScreen avisa porque o setup não é assinado: "Mais informações" → "Executar assim mesmo").

**Aceite:** o setup termina sem "requer diagnóstico"; `sc query VOXELRouter` e `sc query VOXELOrthanc` = RUNNING;
`C:\Program Files\WireGuard\wireguard.exe` existe; `http://127.0.0.1:8765` abre.

## 3. Primeira configuração

1. Provisionar o admin e trocar a senha.
2. **DICOM local:** AE de saída = `VXTESTE_RT`.
3. **Modalidades:** cadastrar `MODALIDADE_TESTE`, IP `127.0.0.1`.

## 4. Destino de teste (simula a célula VOXEL)

Num PowerShell separado, deixe rodando:

```powershell
cd C:\xampp\htdocs\dashboard\voxel_router_desktop-1
.\.venv-test\Scripts\python.exe scripts\fake_voxel_destination.py --port 11190 --ae VOXEL_TESTE --calling-ae VXTESTE_RT
```

No Router: **VOXEL Cloud e destinos → ADICIONAR DESTINO** → DICOM, AE `VOXEL_TESTE`, host `127.0.0.1`, porta `11190` (11112 e 11113 já estão ocupadas nesta máquina)
→ nasce PAUSADO → **TESTAR C-ECHO** (a janela do destino mostra "C-ECHO recebido") → **HABILITAR**.

## 5. Envio fim a fim

```powershell
.\.venv-test\Scripts\python.exe scripts\send_synthetic_study.py --host 127.0.0.1 --port 4242 --called-ae VOXEL_ROUTER --calling-ae MODALIDADE_TESTE --instances 3
```

**Aceite:** os 3 C-STORE com `0x0000`; em ~30 s o estudo aparece em Monitoramento e a Fila mostra ENVIADO;
a janela do destino de teste mostra 3 instâncias recebidas.

Negativo: repetir com `--calling-ae DESCONHECIDO` → "Associação recusada" e a recusa aparece em Modalidades.

## 6. Resiliência

1. Fechar a janela do destino de teste (simula VOXEL fora do ar) e enviar outro sintético → Fila em NOVA TENTATIVA (nunca ERRO).
2. Reiniciar a máquina → serviços voltam sozinhos, item continua na fila.
3. Abrir de novo o destino de teste → o item é entregue sozinho (espera de até 15 min; ou **REENVIAR** na fila).

## 7. Página VOXEL VPN (sem concentrador)

1. **GERAR CHAVES** → aparece só a chave pública.
2. Importar um kit **fictício**, com rede que não colide com nada da máquina:
   - Endereço `10.250.250.10/32` · Chave do concentrador: gere qualquer chave pública de teste com
     `& 'C:\Program Files\WireGuard\wg.exe' genkey | & 'C:\Program Files\WireGuard\wg.exe' pubkey`
   - Endpoint `192.0.2.1:51820` (endereço de documentação, não existe) · AllowedIPs `10.250.250.1/32`
   - Host DICOM `10.250.250.1`, porta `4242`, Called AE `VXTESTE`, Calling AE `VXTESTE_RT`.
3. **Aceite:** status **SEM HANDSHAKE** (esperado, não há concentrador); serviço `WireGuardTunnel$VOXELVPN` RUNNING;
   `C:\ProgramData\VOXEL\Router\vpn\VOXELVPN.conf` **não abre** para usuário comum (só administrador);
   o destino `VOXEL PACS (VPN)` aparece PAUSADO e o C-ECHO falha com `DESTINATION_UNAVAILABLE`.
4. **REMOVER TÚNEL VOXELVPN** → serviço some; o WireGuard continua instalado.
5. O kit fictício trocou o AE de saída para `VXTESTE_RT` (o mesmo); se usar outro, ajuste de volta em DICOM local.

## 8. Desinstalar (opcional)

Painel de Controle → desinstalar VOXEL Router. **Aceite:** serviços removidos; `C:\ProgramData\VOXEL\Router`
preservado; WireGuard continua instalado; pergunta sobre o túnel só aparece se ele existir.

## Resultado

Registrar cada passo como OK/FALHA com o código mostrado na UI. Com 1–7 OK, o instalador está pronto para a clínica
(`docs/homologacao-H1-H5.md`).
