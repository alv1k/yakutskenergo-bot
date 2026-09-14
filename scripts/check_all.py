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
ykt_tz = timezone(timedelta(hours=9))
now_ykt = datetime.now(ykt_tz)
today = now_ykt.date()

print(f"=== Проверка для ВСЕХ пользователей ===")
print(f"Сейчас: {now_ykt}")
print(f"Сегодня: {today}")
print()

schedules = scraper.get_all_recent_schedules()
all_addresses = database.get_all_addresses()

print(f"Записей: {len(schedules)}, Адресов: {len(all_addresses)}")

user_addresses = defaultdict(list)
for chat_id, aid, dist, street in all_addresses:
    user_addresses[chat_id].append((aid, dist, street))

print(f"Пользователей: {len(user_addresses)}")
print()

total_sent = 0
for chat_id, addresses in user_addresses.items():
    if database.is_blocked(chat_id):
        print(f"  chat_id={chat_id} -> BLOCKED, skip")
        continue
    
    all_matches = []
    for s in schedules:
        s_date = parse_russian_date(s['date'], ref_date=now_ykt)
        if not s_date or s_date < today:
            continue
        
        addr_groups = split_schedule_addresses(s['addresses'])
        for aid, district, street in addresses:
            ua = parse_user_address(street)
            norm_user_district = normalize_district(district)
            if match_address_against_schedule(ua, norm_user_district, s, addr_groups):
                s_hash = hashlib.md5(f"{s['date']}{s['time']}{s['addresses']}{s['reason']}".encode()).hexdigest()
                if not database.is_notified(chat_id, aid, s_hash):
                    all_matches.append((s, street, aid, s_hash))
    
    if all_matches:
        by_address = defaultdict(list)
        for m, street, aid, s_hash in all_matches:
            by_address[street].append((m, aid, s_hash))
        
        msg = "⚠️ *Внимание! Обнаружены плановые работы:*\\n\\n"
        seen_hashes = set()
        for street, entries in by_address.items():
            for m, aid, s_hash in entries:
                if s_hash in seen_hashes:
                    continue
                seen_hashes.add(s_hash)
                msg += f"📍 *Адрес:* {street}\\n📅 *Дата:* {m['date']}\\n🕒 *Время:* {m['time']}\\n🏠 *Где:* {m['addresses']}\\n🛠 *Причина:* {m['reason']}\\n\\n"
                database.mark_as_notified(chat_id, aid, s_hash)
        
        bot = Bot(token=TOKEN)
        try:
            asyncio.get_event_loop().run_until_complete(
                bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
            )
            print(f"  chat_id={chat_id} -> OK ({len(by_address)} адр, {len(seen_hashes)} записей)")
            total_sent += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "deactivated" in err:
                database.set_blocked(chat_id)
                print(f"  chat_id={chat_id} -> BLOCKED")
            else:
                print(f"  chat_id={chat_id} -> ERROR: {e}")
    # else:
    #     print(f"  chat_id={chat_id} -> нет совпадений")

print(f"\n=== Итого отправлено: {total_sent} ===")
