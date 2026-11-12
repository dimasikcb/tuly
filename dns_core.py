#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DNS: движок — смена DNS активного адаптера, DoH (Quad9 / Xbox), туннель happ-xray.

Модуль без tkinter: вся работа идёт через PowerShell (`ps_run` из winbase),
поэтому логику можно проверять тестами без графической библиотеки.

Что здесь живёт (перенесено verbatim из tools.pyw, секция «DNS: движок»):
  * PS-скрипты `_PS_ADAPTER` / `_PS_TUNNEL` / `_PS_APPLY` / `_PS_STATE` —
    шаблоны PowerShell для поиска адаптера, туннеля, применения и опроса DNS;
  * константы профилей `QUAD9_DNS`, `QUAD9_DOH`, `XBOX_DNS`, `XBOX_DOH`,
    `TUNNEL_DNS`, `DNS_TEST_NAME`;
  * `DnsState` — снимок состояния (адаптер, индекс, DNS, туннель, DNS туннеля);
  * `dns_state` / `dns_apply` / `dns_dhcp` / `dns_clear_tunnel` /
    `dns_restore_tunnel` / `dns_test` — операции, которые вызывает GUI-панель.

Дубль кода в tools.pyw остаётся до интеграции — вычистит интегратор.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from winbase import ps_run

_PS_ADAPTER = """
$a = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' } | Select-Object -First 1
if (-not $a) {
    $a = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -notmatch 'Tunnel|Loopback' } | Select-Object -First 1
}
if (-not $a) { throw 'Не найден активный сетевой адаптер' }
"""

_PS_TUNNEL = """
$t = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'happ-xray' -or $_.InterfaceDescription -like '*Happ*' } | Select-Object -First 1
if (-not $t) { throw 'Туннель happ-xray не найден' }
"""

_PS_APPLY = _PS_ADAPTER + """
Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses @(__SERVERS__)
foreach ($s in @(__SERVERS__)) {
    $ok = $false
    if (Get-Command Set-DnsClientDohServerAddress -ErrorAction SilentlyContinue) {
        try {
            Set-DnsClientDohServerAddress -ServerAddress $s -DohTemplate '__TEMPLATE__' -AutoUpgrade $true -AllowFallbackToUdp $true -ErrorAction Stop
            $ok = $true
        } catch { }
    }
    if (-not $ok) {
        netsh dns add encryption server=$s dohtemplate=__TEMPLATE__ autoupgrade=yes udpfallback=yes | Out-Null
        if ($LASTEXITCODE -ne 0) {
            netsh dns set encryption server=$s dohtemplate=__TEMPLATE__ autoupgrade=yes udpfallback=yes | Out-Null
        }
    }
}
Clear-DnsClientCache
Write-Output ('Адаптер [' + $a.Name + '] · DNS: ' + ((Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4).ServerAddresses -join ', '))
"""

_PS_STATE = _PS_ADAPTER + """
$dns = (Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses -join ', '
$t = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'happ-xray' -or $_.InterfaceDescription -like '*Happ*' } | Select-Object -First 1
$tn = ''
$td = ''
if ($t) {
    $tn = $t.Name
    $td = (Get-DnsClientServerAddress -InterfaceIndex $t.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses -join ', '
}
[pscustomobject]@{ name = $a.Name; index = $a.ifIndex; dns = $dns; tunnel = $tn; tunnel_dns = $td } | ConvertTo-Json -Compress
"""

QUAD9_DNS = ["9.9.9.9", "149.112.112.112"]
QUAD9_DOH = "https://dns.quad9.net/dns-query"
XBOX_DNS = ["111.88.96.50", "111.88.96.51"]
XBOX_DOH = "https://xbox-dns.ru/dns-query"
TUNNEL_DNS = "172.19.0.2"
DNS_TEST_NAME = "www.workbuddy.ai"


@dataclass
class DnsState:
    name: str = ""
    index: int = 0
    dns: str = ""
    tunnel: str = ""
    tunnel_dns: str = ""


def dns_state() -> DnsState:
    out = ps_run(_PS_STATE)
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(out or "PowerShell не вернул состояние DNS")
    data = json.loads(out[start:end + 1])
    return DnsState(
        name=str(data.get("name") or ""),
        index=int(data.get("index") or 0),
        dns=str(data.get("dns") or ""),
        tunnel=str(data.get("tunnel") or ""),
        tunnel_dns=str(data.get("tunnel_dns") or ""),
    )


def dns_apply(servers: list[str], template: str) -> str:
    joined = ",".join(f"'{s}'" for s in servers)
    script = _PS_APPLY.replace("__SERVERS__", joined).replace("__TEMPLATE__", template)
    return ps_run(script)


def dns_dhcp() -> str:
    return ps_run(_PS_ADAPTER + """
Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ResetServerAddresses
Clear-DnsClientCache
Write-Output ('DNS сброшен на DHCP для [' + $a.Name + ']')
""")


def dns_clear_tunnel() -> str:
    return ps_run(_PS_TUNNEL + """
Set-DnsClientServerAddress -InterfaceIndex $t.ifIndex -ResetServerAddresses
Clear-DnsClientCache
Write-Output ('DNS туннеля [' + $t.Name + '] очищен')
""")


def dns_restore_tunnel() -> str:
    return ps_run(_PS_TUNNEL + """
Set-DnsClientServerAddress -InterfaceIndex $t.ifIndex -ServerAddresses @('__DNS__')
Write-Output ('DNS туннеля [' + $t.Name + '] = __DNS__')
""".replace("__DNS__", TUNNEL_DNS))


def dns_test() -> str:
    return ps_run("""
try {
    $r = Resolve-DnsName -Name '__NAME__' -ErrorAction Stop
    $rows = @($r | Where-Object { $_.Type -eq 'A' -or $_.Type -eq 'CNAME' } | ForEach-Object {
        if ($_.Type -eq 'A') { '  A      ' + $_.Name + ' -> ' + $_.IPAddress }
        else { '  CNAME  ' + $_.Name + ' -> ' + $_.NameHost }
    })
    if ($rows.Count -eq 0) { Write-Output '  записей A и CNAME нет' } else { Write-Output ($rows -join \"`n\") }
} catch {
    Write-Output ('  ошибка разрешения: ' + $_.Exception.Message)
}
""".replace("__NAME__", DNS_TEST_NAME))
