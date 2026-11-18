"""Rede local da máquina: IPv4 da LAN (para as modalidades) e perfil de rede do Windows (firewall)."""

from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
from collections.abc import Callable
from pathlib import Path

import psutil

# Adaptadores que não são a LAN da clínica: VPN (inclusive o túnel WireGuard "VOXELVPN"),
# virtualização, contêineres e loopback. Comparação por trecho do nome, sem diferenciar caixa.
VIRTUAL_ADAPTER_MARKERS = (
    "voxelvpn",
    "wireguard",
    "wg",
    "vpn",
    "tap",
    "tun",
    "virtual",
    "vethernet",
    "hyper-v",
    "vmware",
    "vbox",
    "docker",
    "wsl",
    "loopback",
    "npcap",
    "tailscale",
    "zerotier",
    "bluetooth",
)


def is_virtual_adapter(name: str) -> bool:
    lowered = name.lower()
    return any(marker in lowered for marker in VIRTUAL_ADAPTER_MARKERS)


def local_ipv4_addresses() -> list[str]:
    """IPv4 dos adaptadores físicos ativos; exclui loopback, APIPA (169.254/16) e adaptadores virtuais/VPN."""
    stats = psutil.net_if_stats()
    found: set[ipaddress.IPv4Address] = set()
    for name, addresses in psutil.net_if_addrs().items():
        if is_virtual_adapter(name) or not (stats.get(name) and stats[name].isup):
            continue
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            try:
                ip = ipaddress.IPv4Address(address.address)
            except ValueError:
                continue
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
                continue
            found.add(ip)
    return [str(ip) for ip in sorted(found)]


# Perfil de rede do Windows: a regra de firewall da 4242 vale só em Domínio/Privada, nunca em Pública.
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
PROFILE_COMMAND = "Get-NetConnectionProfile | ForEach-Object { $_.InterfaceAlias + '|' + $_.NetworkCategory }"
PROFILE_TIMEOUT_SECONDS = 15


def _run_powershell(command: str) -> str:
    completed = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=PROFILE_TIMEOUT_SECONDS,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout


def network_profiles(runner: Callable[[str], str] = _run_powershell) -> dict[str, str]:
    """Categoria (Public, Private, DomainAuthenticated) por adaptador físico; ignora VPN/virtuais.

    O túnel WireGuard costuma ficar como Pública, o que é esperado: o Router só faz conexões de saída por ele.
    """
    profiles: dict[str, str] = {}
    for line in runner(PROFILE_COMMAND).splitlines():
        alias, _, category = line.strip().rpartition("|")
        if alias and category and not is_virtual_adapter(alias):
            profiles[alias] = category
    return profiles
