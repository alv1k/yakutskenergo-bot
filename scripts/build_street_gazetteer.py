#!/usr/bin/env python3
"""
Скрипт сбора и актуализации справочника топонимов (Gazetteer)
на основе реальных публикаций плановых отключений ПАО «Якутскэнерго».

Скрипт парсит архив новостей сайта https://www.yakutskenergo.ru/press/news/news-remont/,
извлекает все упоминания улиц, микрорайонов, трактов, СОТ/ДСК и районов,
выполняет их нормализацию, выявляет синонимы/сокращения и сохраняет
структурированную базу знаний в `data/yakutskenergo_streets.json`.
"""

import os
import sys
import re
import json
import time
import html
import logging
from bs4 import BeautifulSoup
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "yakutskenergo_streets.json")

BASE_URL = "https://www.yakutskenergo.ru"
NEWS_LIST_URL = f"{BASE_URL}/press/news/news-remont/"
RU_PROXY = "http://127.0.0.1:10809"
PROXIES = {
    'http': RU_PROXY,
    'https': RU_PROXY,
}
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Базовые известные синонимы имен/инициалов Якутска
KNOWN_INITIAL_ALIASES = {
    "с данилова": "семена данилова",
    "ф попова": "федора попова",
    "п алексеева": "петра алексеева",
    "б чижика": "богдана чижика",
    "м николаева": "михаила николаева",
    "г титова": "германа титова",
    "и кулаковского": "ивана кулаковского",
    "и крафта": "ивана крафта",
    "ю шафера": "юрия шафера",
    "г притузова": "генерала притузова",
    "автодорожная": "михаила николаева",
}

# Известные тракты и шоссе (км-адреса)
KNOWN_TRACTS = [
    "сергеляхское шоссе",
    "хатын-юряхское шоссе",
    "вилюйский тракт",
    "покровский тракт",
    "намский тракт",
    "маганский тракт",
    "старый покровский тракт",
    "шоссе отдыха",
]

# Известные номерные улицы и микрорайоны
KNOWN_NUMBERED_STREETS = [
    "50 лет советской армии",
    "50 лет октября",
    "40 лет победы",
    "1-й академический",
    "2-й академический",
    "3-й академический",
    "1-я набережная",
    "2-я набережная",
    "2 березовая",
    "три сосны",
    "202 микрорайон",
    "203 микрорайон",
    "202 мкр",
    "203 мкр",
    "борисовка 1",
    "борисовка-1",
    "спутник 1",
    "спутник-1",
]


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.proxies.update(PROXIES)
    session.verify = False
    return session


def fetch_all_news_urls(session, max_pages=5):
    """Сбор ссылок на статьи за несколько страниц пагинации."""
    all_links = []
    for page in range(1, max_pages + 1):
        url = NEWS_LIST_URL if page == 1 else f"{NEWS_LIST_URL}?PAGEN_1={page}"
        logging.info(f"Запрос страницы новостей {page}/{max_pages}: {url}")
        try:
            resp = session.get(url, timeout=25)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.content, 'html.parser')
            for a in soup.find_all('a', href=re.compile(r'/press/news/news-remont/\d+/')):
                href = a['href']
                full_url = href if href.startswith('http') else BASE_URL + href
                if full_url not in all_links:
                    all_links.append(full_url)
        except Exception as e:
            logging.warning(f"Ошибка загрузки страницы {page}: {e}")
            break
        time.sleep(1.0)
    logging.info(f"Всего найдено статей: {len(all_links)}")
    return all_links


def extract_raw_entries_from_article(session, url):
    """Парсинг блоков текста из статьи графиков Якутскэнерго."""
    try:
        resp = session.get(url, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, 'html.parser')
        text_block = soup.find('div', class_='text-block')
        if not text_block:
            return []

        content_text = text_block.decode_contents()
        content_text = re.sub(r'<(p|div|br|u|b|li)[^>]*>', '\n', content_text)
        content_text = re.sub(r'</(p|div|u|b|li)>', '\n', content_text)
        content_text = re.sub(r'<[^>]+>', '', content_text)

        lines = [html.unescape(line).strip() for line in content_text.split('\n') if line.strip()]
        entries = []
        current_district = "ЯКУТСК"
        current_date = None

        for text in lines:
            if not text:
                continue

            # Район
            if ("РАЙОН" in text.upper() or 
                (text.isupper() and len(text) > 3 and not re.search(r'\d', text)) or
                text.startswith("г. ") or text.startswith("п. ")):
                if not re.search(r'(\d{2}:\d{2}|В ГРАФИКЕ)', text):
                    candidate = text.replace('г. ', '').replace('п. ', '').strip().upper()
                    if len(candidate) < 50:
                        current_district = candidate.rstrip(':').strip()
                        continue

            # Дата
            date_match = re.search(r'^(\d{1,2})\s+([а-яА-Я]+)', text)
            if date_match and not re.search(r'\d{2}:\d{2}', text):
                current_date = date_match.group(0)
                continue

            # Строка со временем и адресами
            time_match = re.search(r'(\d{2}:\d{2}\s*[-–]\s*\d{2}:\d{2})', text)
            if time_match and current_district:
                rest = text[time_match.end():].strip()
                rest = re.sub(r'^[–-—]\s*', '', rest)

                reason_markers = (
                    r'(ремонтн|допуск|проверк|техническ|капитальн|текущ'
                    r'|срочн|планов|техприсоединени|монтаж|очистк|кратковремен)'
                )
                reason_match = re.search(r'\s*[–-—]\s+' + reason_markers, rest)
                if reason_match:
                    addresses = rest[:reason_match.start()].strip()
                else:
                    last_sep = re.search(r'\s*[–-—]\s*[а-я][^–-—]*$', rest)
                    if last_sep:
                        addresses = rest[:last_sep.start()].strip()
                    else:
                        addresses = rest

                entries.append({
                    'district': current_district,
                    'date': current_date,
                    'addresses': addresses
                })
        return entries
    except Exception as e:
        logging.warning(f"Ошибка парсинга статьи {url}: {e}")
        return []


def clean_raw_token(token):
    token = token.strip().strip('«»"\'.,;()')
    token = re.sub(r'\s+', ' ', token)
    return token


def harvest_streets(all_entries):
    """
    Интеллектуальное извлечение названий топонимов из сводок.
    """
    streets_by_district = {}
    sots_and_dsks = set()
    microdistricts = set()
    tracts_and_highways = set()
    all_raw_street_names = set()

    # Предзаполняем проверенными известными объектами
    for tr in KNOWN_TRACTS:
        tracts_and_highways.add(tr)
    for num_st in KNOWN_NUMBERED_STREETS:
        if "мкр" in num_st or "микрорайон" in num_st:
            microdistricts.add(num_st)
        else:
            all_raw_street_names.add(num_st)

    for entry in all_entries:
        dist = entry['district'].upper()
        if dist not in streets_by_district:
            streets_by_district[dist] = set()

        raw_addr = entry['addresses']
        # Выделяем кавычки СОТ «...», ДСК «...»
        for quote_m in re.finditer(r'(?:СОТ|ДСК|СОПК|ДСПК|СНТ)\s*[«"]([^»"]+)[»"]', raw_addr, re.IGNORECASE):
            sots_and_dsks.add(quote_m.group(1).strip().lower())

        # Выделяем микрорайоны
        for mkr_m in re.finditer(r'(?:мкр\.?|микрорайон)\s*([А-Яа-я0-9-]+(?:\s+[0-9]+)?)', raw_addr, re.IGNORECASE):
            microdistricts.add(mkr_m.group(1).strip().lower())

        # Делим строку на сегменты
        # Предварительно маскируем диапазоны номеров 1-10, чтобы дефис не резал
        chunks = re.split(r',\s*(?=[А-Яа-я«])', raw_addr)
        for chunk in chunks:
            chunk = chunk.strip().rstrip('.,;')
            if not chunk:
                continue

            # Убираем пометки типа (частично), (Ботанического сада р-н)
            chunk_cleaned = re.sub(r'\([^)]*\)', '', chunk).strip()

            # Проверяем СОТ/ДСК
            sot_match = re.search(r'\b(СОТ|ДСК|СОПК|ДСПК|СНТ)\b\s*[«"]?([А-Яа-яA-Za-z0-9-]+)[»"]?', chunk_cleaned, re.IGNORECASE)
            if sot_match:
                sots_and_dsks.add(sot_match.group(2).strip().lower())

            # Проверяем тракты / шоссе с километрами
            km_match = re.search(r'([А-Яа-я-]+\s+(?:тракт|шоссе|тр|ш))\s+(?:\d+(?:-\d+)?\s*км)?', chunk_cleaned, re.IGNORECASE)
            if km_match:
                tr_name = km_match.group(1).strip().lower()
                tr_name = re.sub(r'\b(тр|ш)\b', lambda m: 'тракт' if m.group(1) == 'тр' else 'шоссе', tr_name)
                tracts_and_highways.add(tr_name)

            # Извлечение названия улицы
            # Срезаем номера домов в конце (например: Чехова 77, 77/1 -> Чехова; Лонгинова 16-87А -> Лонгинова)
            m_street = re.match(
                r'^(?:ул\.?|пер\.?|пр-кт\.?|пр\.?|проезд|шоссе|тракт)?\s*'
                r'([А-Яа-я0-9\s-]+?)'
                r'(?:\s+\d+(?:[-–/]\d+)?.*)?$',
                chunk_cleaned
            )
            if m_street:
                cand = m_street.group(1).strip()
                # Фильтруем слишком короткие или чисто цифровые
                if len(cand) >= 3 and not cand.isdigit():
                    cand_lower = cand.lower()
                    # Исключаем служебные слова
                    if not re.match(r'^(дом|д|кв|корп|участок|частично|район)$', cand_lower):
                        streets_by_district[dist].add(cand_lower)
                        all_raw_street_names.add(cand_lower)

    # Формируем структуру базы
    result = {
        "metadata": {
            "source": "https://www.yakutskenergo.ru/press/news/news-remont/",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_districts": len(streets_by_district),
            "total_streets_harvested": len(all_raw_street_names),
            "total_sots": len(sots_and_dsks),
            "total_microdistricts": len(microdistricts),
            "total_tracts": len(tracts_and_highways),
        },
        "tracts_and_highways": sorted(list(tracts_and_highways)),
        "sots_and_dsks": sorted(list(sots_and_dsks)),
        "microdistricts": sorted(list(microdistricts)),
        "aliases": KNOWN_INITIAL_ALIASES,
        "districts": {k: sorted(list(v)) for k, v in streets_by_district.items() if v}
    }
    return result


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    session = create_session()

    logging.info("Начало сбора архива новостей Якутскэнерго...")
    urls = fetch_all_news_urls(session, max_pages=6)

    all_entries = []
    for idx, url in enumerate(urls):
        logging.info(f"[{idx+1}/{len(urls)}] Обработка {url}...")
        entries = extract_raw_entries_from_article(session, url)
        all_entries.extend(entries)
        time.sleep(0.5)

    logging.info(f"Собрано {len(all_entries)} пунктов отключений.")

    gazetteer = harvest_streets(all_entries)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(gazetteer, f, ensure_ascii=False, indent=2)

    logging.info(f"Справочник топонимов сохранен в {OUTPUT_FILE}")
    logging.info(f"Улиц: {gazetteer['metadata']['total_streets_harvested']}, "
                 f"Трактов: {gazetteer['metadata']['total_tracts']}, "
                 f"СОТ: {gazetteer['metadata']['total_sots']}, "
                 f"Микрорайонов: {gazetteer['metadata']['total_microdistricts']}")


if __name__ == "__main__":
    main()
