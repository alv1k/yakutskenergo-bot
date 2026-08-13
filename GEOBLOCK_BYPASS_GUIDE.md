# Руководство по обходу GeoIP / DDoS блокировок Yakutskenergo.ru

## Описание проблемы
Сайт `https://www.yakutskenergo.ru/press/news/news-remont/` и связанные эндпоинты блокируют входящий HTTP/HTTPS-трафик с зарубежных и хостинговых подсетей (GeoIP / анти-DDoS файрвол).
При этом ICMP (ping) до IP `37.18.40.165` проходит, но порты `80` и `443` сбрасываются по таймауту (`Connection timed out`).

---

## Способы решения

### Вариант 1. Использование стороннего российского HTTP/SOCKS5 прокси (Рекомендуется для бота)
Купить или поднять прокси-сервер с IP-адресом РФ и прописать его в scraper/bot:

В `requests` / `aiohttp`:
```python
proxies = {
    'http': 'http://user:pass@ru_proxy_ip:port',
    'https': 'http://user:pass@ru_proxy_ip:port',
}
response = requests.get('https://www.yakutskenergo.ru/press/news/news-remont/', proxies=proxies)
```

---

### Вариант 2. Использование домашнего роутера TP-Link TL-MR6400 (v5 / v5.20)

#### Особенности модели TL-MR6400:
1. **В стоковой прошивке TP-Link**:
   * Есть только **OpenVPN Server** и **PPTP Server**.
   * Функция **OpenVPN Client** отсутствует.
   * У сотовых операторов (LTE) IP-адрес «серый» (CGNAT), поэтому напрямую подключиться с VPS к роутеру по VPN нельзя.

#### Способы использования TL-MR6400:

##### А) Прошивка роутера на OpenWrt (для добавления OpenVPN / WireGuard Client)
Для ревизии **v5 / v5.20** есть официальная поддержка OpenWrt.
1. Скачать прошивку для v5: `openwrt-...-tplink_tl-mr6400-v5-squashfs-tftp-recovery.bin`
2. Переименовать в `tp_recovery.bin`.
3. Подключить ПК к LAN-порту роутера, задать IP ПК: `192.168.0.225` (маска `255.255.255.0`).
4. Запустить TFTP-сервер на ПК.
5. Зажать кнопкой `Reset / WPS` при включении роутера (удерживать 5-10 сек).
6. После прошивки установить в OpenWrt пакет `luci-app-openvpn` или `luci-app-wireguard` и подключиться к VPS.

##### Б) Использование ПК/устройства в домашней сети роутера
Если не прошивать роутер:
1. Запустить на ПК/сервере в домашней сети WireGuard / OpenVPN клиент.
2. Подключить этот ПК к VPS.
3. На VPS настроить маршрутизацию для IP `37.18.40.165` через этот туннель.
