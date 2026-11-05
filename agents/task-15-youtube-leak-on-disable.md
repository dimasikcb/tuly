# Задача 15 — YouTube открываем, но трафик «стекает» при выключении обхода

## Проблема

Обход включён — YouTube открывается (идёт через Happ). Обход выключен — YouTube **всё равно открывается**. Это значит, что после выключения VPN часть трафика всё равно идёт через прокси/Happ, а не напрямую.

## Установленная причина (после диагностики)

**Главное:** код сбрасывал не тот ключ реестра. Happ хранит настройки в
`HKCU\Software\Happ\OrganizationDefaults\Preferences\AdvancedSettings`:

| Ключ | Тип | Что это |
|------|-----|---------|
| `tun` | REG_SZ `"true"` / `"false"` | TUN-адаптер |
| `systemProxy` | REG_SZ `"true"` / `"false"` | **реальный** ключ системного прокси |
| `system_proxy` | REG_DWORD | legacy-ключ нашей тулзы, **Happ его игнорирует** |

`happ_mode_read()` читал `system_proxy` через `int()`, а `happ_mode_write()`
писал туда же DWORD. Happ при этом видел `systemProxy = "true"` и после
`happ://disconnect` снова включал системный прокси `127.0.0.1:10809` — поэтому
YouTube открывался при выключенном обходе, а Яндекс (ушедший на зарубежный IP)
не открывался.

Вторая причина: `route.final: proxy` + `strict_route/auto_route` в
`%LOCALAPPDATA%\Happ\config.json` гнали **весь** трафик через зарубежный
SOCKS — Яндекс/Госуслуги/банки с такого IP не отвечают.

## Цель

1. Переупорядочить выключение в `bypass_stop()`: сначала **гарантированно отключить VPN в Happ**, убедиться, что `happ_connected()` → `False`, и только потом гасить TUN, прокси, ядра.
2. Добавить явную проверку после остановки: если TUN-адаптер активен или системный прокси смотрит на 127.0.0.1 — выключить принудительно.
3. В `happ_tun_disable()` делать **wait-поллинг**: после `netsh … disable` проверять, действительно ли интерфейс неактивен, и при необходимости `delete` адаптер/сброс маршрутов.
4. Добавить диагностику в `bypass_status` (поля `leak_tun`, `leak_proxy`, `leak_cores`): если обход выключен, но что-то из вышеперечисленного живо — сообщать пользователю, а не молчать.
5. Кнопка «🌐 Проверить IP» должна реально показывать разницу: при выключенном обходе IP должен быть прямым, а не проксированным (проверка через публичный API + DNS-leak тест: запросить dnsleaktest.com/api.php).

## Реализованное решение

`bypass_core.py`:
- **Ключевой фикс — реестр Happ:**
  - `_reg_bool()` / `_reg_set_bool()` — читают и пишут булевы настройки в
    исходном типе ключа (REG_SZ `"true"`/`"false"` или DWORD).
  - `happ_mode_read()` — читает **реальный** `systemProxy`, при отсутствии
    откатывается на legacy `system_proxy`.
  - `happ_mode_write()` — пишет `tun` и `systemProxy`, плюс дублирует в
    legacy `system_proxy` (DWORD) для совместимости.
- **Маршрутизация Яндекса:**
  - `happ_direct_ru_ensure()` — добавляет в `%LOCALAPPDATA%\Happ\config.json`
    два правила `direct` (перед `sniff`/`hijack-dns`): `domain_suffix` для
    Яндекса/Mail.ru/VK/Госуслуг/банков и `ip_cidr` для приватных сетей.
    Бэкап — `config.json.tools.bak`. Вызывается из `bypass_apply(scope="all")`
    до старта ядра; если правила добавились, туннель перезапускается.
  - Константы `HAPP_DIRECT_DOMAINS` / `HAPP_DIRECT_CIDR` — список легко расширить.
- `bypass_happ_stop()`:
  - После штатного disconnect (`happ://disconnect`) добавлен
    `happ_mode_write(tun=False, system_proxy=False)` — гасим "автоподключение"
    в реестре, иначе Happ вернётся через 30 сек.
  - Таймаут ожидания увеличен с 10 до 25 сек (ждем, пока Happ сам выключит
    TUN/прокси); условие успеха: `happ_connected()==False`, `happ_tun_active()==False`,
    `ProxyEnable==0`.
  - При таймауте — kill `xray.exe`/`sing-box.exe`/`happd.exe`, `happ_tun_disable()`,
    `set_windows_system_proxy(False)`, сброс настроек Happ в реестре.
- `happ_tun_disable(wait=4.0)`: после `netsh … disable` ждёт поллингом, пока
  интерфейс реально не станет неактивным (~0.25 сек); при неудаче — `netsh
  interface delete route ... purge`.
- `bypass_stop(key="happ")`:
  - Порядок: `proxifyre_stop()` → `bypass_happ_stop()` → `set_windows_system_proxy(False)`
    → `happ_tun_disable()` → kill `happd.exe` → kill ядер → `ipconfig /flushdns`.
  - Добавлен явный kill `happd.exe` и сброс настроек Happ в конце.
- `_bypass_leak_diagnosis(scope)`: новые поля `leak` — `leak_tun` (`happ_tun_active()`),
  `leak_proxy` (системный прокси на 127.0.0.1/localhost, режим apps), `leak_cores`
  (живы ли ядра).
- `bypass_check_ip()`: 3 сервиса + DNS-leak тест (dnsleaktest.com/api.php).

`tools.pyw`:
- `_state_ok()`: выводит предупреждение в лог, если `state["leak"]` что-то
  показывает.

## Тест

1. Включить обход → открыть YouTube (через прокси).
2. Выключить обход.
3. Подождать 60 сек.
4. Проверить: `happ_connected()` → `False`, `happ_tun_active()` → `False`,
   `ProxyEnable` → 0, `xray.exe`/`sing-box.exe`/`happd.exe` → нет в процессе.
5. Открыть YouTube — открывается напрямую, без прокси.
6. Запустить тесты: `python -m unittest discover -s tests`.

## Примечания

- Не трогать логику включения (большинство проверок зелёные).
- `bypass_apply(scope, mode)` трогать только в части порядка: сначала Happ/VPN, потом периферия.
- Код писать так, чтобы работал и из исходников (`tools.pyw`), и из собранного `Tools.exe`.
- **Важно:** `happ://disconnect` отключает VPN, но Happ через ~30 сек включается
  обратно из-за `tun=true`/`systemProxy=true` в реестре — поэтому сброс настроек
  Happ обязателен.
