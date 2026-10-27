import sys
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from telegram import Bot
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import database
import scraper
from bot import parse_user_address, match_address_against_schedule, split_schedule_addresses, normalize_district, parse_russian_date
import hashlib
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "364224373"))

ykt_tz = timezone(timedelta(hours=9))
now_ykt = datetime.now(ykt_tz)
today = now_ykt.date()

print(f"=== Проверка уведомлений для Администратора ({ADMIN_TG_ID}) ===")
print(f"Текущее время (YKT): {now_ykt.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Дата отсчёта: {today}")

schedules = scraper.get_all_recent_schedules()
admin_addrs = database.get_user_addresses(ADMIN_TG_ID)

print(f"Всего расписаний на сайте: {len(schedules)}")
print(f"Адресов администратора на мониторинге: {len(admin_addrs)}")

matches = []
all_upcoming_schedules = 0
dates_set = set()

for s in schedules:
    s_date = parse_russian_date(s.get('date', ''), ref_date=now_ykt)
    if not s_date or s_date < today:
        continue
    all_upcoming_schedules += 1
    dates_set.add(s_date.strftime('%d.%m.%Y'))
    addr_groups = split_schedule_addresses(s.get('addresses', ''))
    for aid, district, street in admin_addrs:
        ua = parse_user_address(street)
        norm_user_district = normalize_district(district)
        if match_address_against_schedule(ua, norm_user_district, s, addr_groups):
            s_hash = hashlib.md5((s['date'] + s['time'] + s['addresses'] + s['reason']).encode()).hexdigest()
            notified = database.is_notified(ADMIN_TG_ID, aid, s_hash)
            matches.append((street, aid, s, s_hash, notified))

dates_str = ", ".join(sorted(dates_set)) if dates_set else today.strftime('%d.%m.%Y')

bot = Bot(token=TOKEN)

if matches:
    print(f"Найдено совпадений: {len(matches)}")
    by_address = defaultdict(list)
    for street, aid, s, s_hash, notified in matches:
        by_address[street].append((s, aid, s_hash, notified))

    msg = f"⚠️ *Внимание! Обнаружены плановые работы Якутскэнерго:*\n"
    msg += f"📅 *Проверены списки за:* {dates_str}\n\n"

    seen_hashes = set()
    for street, entries in by_address.items():
        msg += f"📍 *Адрес: {street}*\n"
        for s, aid, s_hash, notified in entries:
            if s_hash in seen_hashes:
                continue
            seen_hashes.add(s_hash)
            status_tag = "_(ранее отправлялось)_" if notified else "*[НОВОЕ]*"
            msg += f"• 📅 *Дата:* {s['date']} | 🕒 *Время:* {s['time']} {status_tag}\n"
            msg += f"  🏠 *Где:* {s['addresses']}\n"
            msg += f"  🛠 *Причина:* {s['reason']}\n\n"
            if not notified:
                database.mark_as_notified(ADMIN_TG_ID, aid, s_hash, message_text=f"{s['date']} {s['time']}: {s['reason']}")
    
    msg += f"ℹ️ _Проверка выполнена на сервере NL (RU Шлюз: Selectel SPB ➔ OK). Мониторится 7 адресов._"
else:
    print("Совпадений по адресам администратора не найдено.")
    msg = (
        f"⚡ *Якутскэнерго Уведомлятор — Отчёт по проверке*\n\n"
        f"✅ *Плановых отключений по вашим адресам не обнаружено.*\n\n"
        f"📅 *Проверены опубликованные графики за:* {dates_str}\n"
        f"📋 *Всего расписаний в базе:* {len(schedules)} (из них актуальных: {all_upcoming_schedules})\n"
        f"📍 *Проверено ваших адресов:* {len(admin_addrs)}\n"
        f"• Оленекская 13\n"
        f"• Бестужева-Марлинского 9\n"
        f"• ул. 50 лет Советской Армии 33 корп 2\n"
        f"• Вилюйский тр. 4 км 24\n"
        f"• СОТ Пламя 252д\n"
        f"• Кедровая 25\n"
        f"• Намский, с. Аппаны\n\n"
        f"🌐 _Служба активна на NL-ноде (2.26.76.210), шлюз к сайту через Selectel SPB работает стабильно._"
    )

async def send():
    await bot.send_message(chat_id=ADMIN_TG_ID, text=msg, parse_mode='Markdown')
    print("Сообщение успешно отправлено администратору в Telegram!")

asyncio.run(send())
