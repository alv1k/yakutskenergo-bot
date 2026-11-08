#!/usr/bin/env python3
import sys
import os
import re
import html
import json
from bs4 import BeautifulSoup

def main():
    crash_core = '/tmp/crash_unpacked/CoreDump'
    if not os.path.exists(crash_core):
        print(f"Error: {crash_core} does not exist")
        return 1

    print("Reading core dump...")
    with open(crash_core, 'rb') as f:
        core = f.read()
    print(f"Read {len(core)} bytes.")

    tb_matches = [m.start() for m in re.finditer(rb'class="[^"]*text-block', core)]
    print(f"Found {len(tb_matches)} text-block candidates")

    schedules = []
    seen = set()

    for idx, tb in enumerate(tb_matches):
        hs = core.rfind(b'<!DOCTYPE html', 0, tb)
        he = core.find(b'</html>', tb)
        if hs == -1 or he == -1:
            continue
        he += len(b'</html>')
        try:
            soup = BeautifulSoup(core[hs:he], 'html.parser')
        except Exception as e:
            continue
        tb_elem = soup.find('div', class_='text-block')
        if not tb_elem:
            continue

        content_text = tb_elem.decode_contents()
        content_text = re.sub(r'<(p|div|br|u|b|li)[^>]*>', '\n', content_text)
        content_text = re.sub(r'</(p|div|u|b|li)>', '\n', content_text)
        content_text = re.sub(r'<[^>]+>', '', content_text)

        lines = [html.unescape(line).strip() for line in content_text.split('\n') if line.strip()]
        current_district = "ЯКУТСК"
        current_date = None

        for text in lines:
            if not text:
                continue
            if ("РАЙОН" in text.upper() or 
                (text.isupper() and len(text) > 3 and not re.search(r'\d', text)) or
                text.startswith("г. ") or text.startswith("п. ")):
                if not re.search(r'(\d{2}:\d{2}|В ГРАФИКЕ)', text):
                    candidate = text.replace('г. ', '').replace('п. ', '').strip().upper()
                    if len(candidate) < 50:
                        current_district = candidate
                        continue

            date_match = re.search(r'^(\d{1,2})\s+([а-яА-Я]+)', text)
            if date_match and not re.search(r'\d{2}:\d{2}', text):
                current_date = date_match.group(0)
                continue

            time_match = re.search(r'(\d{2}:\d{2}\s*[-–]\s*\d{2}:\d{2})', text)
            if time_match and current_district and current_date:
                time_range = time_match.group(1).replace(' ', '')
                rest = text[time_match.end():].strip().lstrip('–-—').strip()
                reason_markers = (
                    r'(ремонтн|допуск|проверк|техническ|капитальн|текущ'
                    r'|срочн|планов|техприсоединени|монтаж|очистк|кратковремен)'
                )
                reason_match = re.search(r'\s*[–-—]\s+' + reason_markers, rest)
                if reason_match:
                    reason = rest[reason_match.start():].strip().lstrip('–-—').strip()
                    addresses = rest[:reason_match.start()].strip()
                else:
                    addresses = rest
                    reason = ""
                key = (current_district, current_date, time_range, addresses)
                if key not in seen:
                    seen.add(key)
                    schedules.append({
                        'district': current_district,
                        'date': current_date,
                        'time': time_range,
                        'addresses': addresses,
                        'reason': reason
                    })

    print(f"Extracted {len(schedules)} unique schedules from core dump.")
    if schedules:
        out_file = '/home/alvik/yakutskenergo-bot/data/schedules_cache.json'
        payload = {
            "timestamp": 1793168892,  # Oct 27 06:28
            "updated_at": "2026-10-27T06:28:12",
            "count": len(schedules),
            "data": schedules
        }
        with open(out_file, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"Saved to {out_file}")
    return 0

if __name__ == '__main__':
    sys.exit(main())
