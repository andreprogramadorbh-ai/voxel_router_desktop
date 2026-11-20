# Instalação e homologação na clínica — VOXEL Router + VOXEL VPN (H1–H5)

Roteiro para instalar o VOXEL Router **direto na máquina da clínica piloto** e validar a operação
antes de liberar para outros clientes. Cada etapa tem critério de aceite; só avance com o anterior OK.
Nenhum passo usa estudo real até a H5.

## Fluxo

```text
Modalidade ──C-STORE 4242/TCP (LAN)──▶ VOXEL Router (Windows da clínica)
                                         │ guarda: Router + Orthanc local (store-and-forward)
                                         ▼
                           túnel WireGuard VOXELVPN (UDP de saída)
                                         ▼
                 Concentrador VOXEL (endpoint do kit) ──▶ gateway 10.200.10.1 ──▶ célula do cliente
```

## H0 — Pré-requisitos (antes da visita)

| Item | Quem | Critério |
|---|---|---|
| Máquina | TI clínica | Windows 10/11 x64 atualizado, ligada 24 h, **IP fixo na LAN** (reserva DHCP), relógio sincronizado, sem hibernar. |
| Disco | TI clínica | Livre ≥ 3× o volume diário de exames × dias de autonomia desejados (sem internet). |
| Rede | TI clínica | Perfil de rede **Privada** ou Domínio (nunca Pública). Saída **UDP** liberada para o endpoint do kit (ex.: `51820/UDP`). |
| Modalidades | TI clínica | Lista com nome, **AE Title** e **IP** de cada equipamento. |
| Instalador | VOXEL | `VOXEL_ROUTER_SETUP.exe` + arquivo `.sha256` gerados por `scripts/build_windows.ps1`. Conferir o hash na máquina (`Get-FileHash`). Setup não assinado: SmartScreen vai alertar (piloto). |
| Célula | VOXEL | Lado servidor pronto (prompt `claude/prompt-manus-concentrador-vpn-celula-router.md`): correções da célula em produção. Sem isso, a H4 não acontece; H1–H3 podem rodar antes. |

## H1 — Instalação

1. Conferir o hash do setup e executar como administrador.
2. O setup instala Router, Orthanc local e **WireGuard for Windows** (só se ausente), cria os serviços e roda o diagnóstico.
3. Abrir `http://127.0.0.1:8765`, provisionar o admin e trocar a senha.

**Aceite:** `sc query VOXELRouter` e `sc query VOXELOrthanc` = RUNNING; Dashboard abre; diagnóstico sem falha;
`C:\Program Files\WireGuard\wireguard.exe` existe; firewall com regra só da `4242/TCP` (Domínio/Privada).

## H2 — Recepção local (modalidade de teste)

1. **DICOM local:** conferir AE do Router (padrão `VOXEL_ROUTER`) e porta `4242`.
2. **Modalidades:** cadastrar cada equipamento (AE + IP). Opcional: modo aprendizado por até 24 h para descobrir AEs; cadastrar com um clique.
3. Da modalidade (ou de um notebook na LAN com `scripts/send_synthetic_study.py`):
   - C-ECHO para o Router → sucesso;
   - estudo **sintético** (`--instances 3`) → aparece em Monitoramento.
4. Teste negativo: AE ou IP não cadastrado → associação recusada (aparece em "últimas recusas").

**Aceite:** estudo sintético recebido e listado; recusa do desconhecido registrada; nada enviado (destino ainda inexistente).

## H3 — Resiliência (sem internet)

1. Com destino ainda pausado/ausente, enviar outro sintético e **reiniciar a máquina**.
2. Depois do boot: serviços voltam sozinhos; estudos continuam no Monitoramento (nada perdido).
3. (Depois da H4) Repetir com o túnel: desligar a internet 1 h, enviar sintético, religar → item fica em NOVA TENTATIVA (nunca ERRO) e é entregue sozinho ao voltar (backoff até 15 min).

**Aceite:** zero perda após reboot e após queda de rede; alerta de pendência > 60 min aparece no Dashboard durante a queda.

## H4 — VOXEL VPN e célula de homologação

1. **VOXEL VPN → GERAR CHAVES.** Copiar a **chave pública** e enviar ao VOXEL (a privada nunca sai da máquina).
2. VOXEL cadastra o servidor `vpn_only` do tenant com essa chave e o Calling AE combinado, provisiona e envia o **kit**.
3. **VOXEL VPN → Importar kit:** preencher os campos exatamente como no kit (seção "VOXEL Router — campos para importar") → APLICAR KIT.
4. Aguardar o status **ATIVO** (handshake < 3 min). Se ficar **SEM HANDSHAKE**: saída UDP bloqueada ou chave pública ainda não cadastrada no VOXEL.
5. **VOXEL Cloud e destinos:** o destino `VOXEL PACS (VPN)` aparece PAUSADO → **TESTAR C-ECHO** → sucesso.
6. No PACS, **Verificar C-ECHO** → `echo_validated`.

**Aceite:** handshake ativo; C-ECHO OK nos dois lados; destino ainda PAUSADO (C-STORE só na H5).

## H5 — Liberação e primeiro envio real

1. VOXEL libera C-STORE da célula (confirmação no PACS).
2. No Router: **HABILITAR** o destino.
3. Enviar **um estudo sintético** → Fila: ENVIADO → aparece na worklist do tenant (paciente `VOXEL^TESTE^SINTETICO`). Apagar o sintético no PACS depois.
4. Primeiro estudo real acompanhado: conferir número de imagens no Router × PACS.
5. Configurar retenção (Configurações): ligar "Excluir somente estudos validados" com a retenção combinada (padrão 168 h).

**Aceite:** sintético e primeiro real entregues e completos; nenhum item em ERRO.

## Checklist para o TI da clínica (enviar antes)

- [ ] IP fixo para a máquina do Router e perfil de rede Privada/Domínio.
- [ ] AE Title e IP de todas as modalidades.
- [ ] Saída UDP para o endpoint do kit liberada no firewall/roteador.
- [ ] Enviar ao VOXEL **somente a chave pública** do Router.
- [ ] Nunca colar chave privada em lugar nenhum.
- [ ] C-ECHO antes de liberar qualquer envio.

## Rollback

- **Parar envio:** PAUSAR o destino no Router (os estudos continuam guardados na máquina).
- **Cortar o túnel:** VOXEL VPN → REMOVER TÚNEL (o WireGuard permanece instalado).
- **Desinstalar:** dados em `ProgramData\VOXEL\Router` são preservados; o túnel só é removido se o operador confirmar.

## Registro da homologação

Para cada etapa, anotar data/hora, resultado (OK/FALHA) e, se falhar, o código exibido na UI.
Nunca registrar nome de paciente, UID, chave privada ou senha.
