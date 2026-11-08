import requests
from bs4 import BeautifulSoup
import re
import logging
import urllib3
import time
import html
import os
import json
from datetime import datetime, timedelta

# Suppress insecure request warnings if we disable SSL verify
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://www.yakutskenergo.ru"
NEWS_LIST_URL = f"{BASE_URL}/press/news/news-remont/"

# Headers to mimic a real browser
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# Proxy settings (uses local Xray client tunneling to RU server)
RU_PROXY = "http://127.0.0.1:10809"
PROXIES = {
    'http': RU_PROXY,
    'https': RU_PROXY,
}

# Кеш для результатов парсера
_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 1800,  # 30 минут
    'site_online': False,
    'last_successful_fetch': 0,
    'urls': [],
}

def is_site_online():
    """Возвращает True, если официальный сайт доступен и отдает данные."""
    return _cache.get('site_online', False)

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_FILE = os.path.join(CACHE_DIR, "schedules_cache.json")

def load_disk_cache():
    """Загружает расписания и сохраненные URL из локального дискового кеша."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                    return data["data"], data.get("timestamp", 0), data.get("urls", [])
                elif isinstance(data, list):
                    mtime = os.path.getmtime(CACHE_FILE)
                    return data, mtime, []
        except Exception as e:
            logging.error(f"Error reading schedules disk cache from {CACHE_FILE}: {e}")
    return None, 0, []

_MONTHS = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
}

def _parse_entry_date(date_str, ref_date=None):
    if not date_str:
        return None
    ref = ref_date or datetime.now()
    try:
        m = re.search(r'(\d+)\s*([а-яА-Я]+)', date_str)
        if not m:
            return None
        day = int(m.group(1))
        month = _MONTHS.get(m.group(2).lower())
        if not month:
            return None
        year = ref.year + 1 if ref.month == 12 and month == 1 else ref.year
        return datetime(year, month, day).date()
    except Exception:
        return None

def save_disk_cache(schedules, timestamp, urls=None):
    """Атомарно сохраняет расписания и список обработанных URL в локальный дисковый кеш."""
    if not schedules:
        return
    try:
        # Автоматическая очистка записей, прошедших более 2 дней назад
        cutoff_date = datetime.now().date() - timedelta(days=2)
        fresh_schedules = []
        for s in schedules:
            d = _parse_entry_date(s.get('date'))
            if d is None or d >= cutoff_date:
                fresh_schedules.append(s)
        schedules = fresh_schedules

        os.makedirs(CACHE_DIR, exist_ok=True)
        temp_file = f"{CACHE_FILE}.tmp.{os.getpid()}"
        payload = {
            "timestamp": timestamp,
            "updated_at": datetime.now().isoformat(),
            "count": len(schedules),
            "urls": urls or [],
            "data": schedules
        }
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, CACHE_FILE)
        logging.info(f"Saved {len(schedules)} schedules to disk cache: {CACHE_FILE}")
    except Exception as e:
        logging.error(f"Failed to write schedules disk cache: {e}")

def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    session.proxies.update(PROXIES)
    session.verify = False
    return session

def get_latest_maintenance_urls(session=None):
    client = session or requests
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            kwargs = {'timeout': 25}
            if client is requests:
                kwargs.update({'headers': HEADERS, 'verify': False, 'proxies': PROXIES})
            response = client.get(NEWS_LIST_URL, **kwargs)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            news_links = []
            # Look for news links specifically in the news feed area
            for a in soup.find_all('a', href=re.compile(r'/press/news/news-remont/\d+/')):
                url = a['href']
                if not url.startswith('http'):
                    url = BASE_URL + url
                if url not in news_links:
                    news_links.append(url)
            
            return news_links[:3]
        except Exception as e:
            logging.warning(f"Attempt {attempt+1}/{max_attempts} error fetching news list: {e}")
            if attempt < max_attempts - 1:
                time.sleep(2 * (attempt + 1))
    return []

def parse_maintenance_page(url, session=None):
    client = session or requests
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            logging.info(f"Parsing page: {url}")
            kwargs = {'timeout': 25}
            if client is requests:
                kwargs.update({'headers': HEADERS, 'verify': False, 'proxies': PROXIES})
            response = client.get(url, **kwargs)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            text_block = soup.find('div', class_='text-block')
            if not text_block:
                logging.warning(f"No text-block found on page {url}")
                return []

            schedules = []
            current_district = "ЯКУТСК" # Default
            current_date = None

            content_text = text_block.decode_contents()
            content_text = re.sub(r'<(p|div|br|u|b|li)[^>]*>', '\n', content_text)
            content_text = re.sub(r'</(p|div|u|b|li)>', '\n', content_text)
            content_text = re.sub(r'<[^>]+>', '', content_text)
            
            lines = [html.unescape(line).strip() for line in content_text.split('\n') if line.strip()]

            for text in lines:
                if not text: continue

                # District detection
                if ("РАЙОН" in text.upper() or 
                    (text.isupper() and len(text) > 3 and not re.search(r'\d', text)) or
                    text.startswith("г. ") or text.startswith("п. ")):
                    if not re.search(r'(\d{2}:\d{2}|В ГРАФИКЕ)', text):
                        candidate = text.replace('г. ', '').replace('п. ', '').strip().upper()
                        if len(candidate) < 50:
                            current_district = candidate
                            continue

                # Date detection
                date_match = re.search(r'^(\d{1,2})\s+([а-яА-Я]+)', text)
                if date_match and not re.search(r'\d{2}:\d{2}', text):
                    current_date = date_match.group(0)
                    continue

                # Entry detection: TIME - ADDRESSES [- REASON]
                time_match = re.search(r'(\d{2}:\d{2}\s*[-–]\s*\d{2}:\d{2})', text)
                if time_match and current_district and current_date:
                    time_range = time_match.group(1).replace(' ', '')
                    rest = text[time_match.end():].strip()
                    rest = re.sub(r'^[–-—]\s*', '', rest)

                    reason_markers = (
                        r'(ремонтн|допуск|проверк|техническ|капитальн|текущ'
                        r'|срочн|планов|техприсоединени|монтаж|очистк|кратковремен)'
                    )
                    reason_match = re.search(r'\s*[–-—]\s+' + reason_markers, rest)
                    if reason_match:
                        reason = rest[reason_match.start():].strip().lstrip('–-—').strip()
                        addresses = rest[:reason_match.start()].strip()
                    else:
                        last_sep = re.search(r'\s*[–-—]\s*[а-я][^–-—]*$', rest)
                        if last_sep:
                            reason = rest[last_sep.start():].strip().lstrip('–-—').strip()
                            addresses = rest[:last_sep.start()].strip()
                        else:
                            addresses = rest
                            reason = ""
                    
                    schedules.append({
                        'district': current_district,
                        'date': current_date,
                        'time': time_range,
                        'addresses': addresses,
                        'reason': reason
                    })

            return schedules
        except Exception as e:
            logging.warning(f"Attempt {attempt+1}/{max_attempts} error parsing page {url}: {e}")
            if attempt < max_attempts - 1:
                time.sleep(2 * (attempt + 1))
    return []

def check_for_new_schedules(force_parse=False):
    """
    Легкий опрос ленты новостей Якутскэнерго:
    1. Делает ровно 1 запрос к странице новостей со списком ссылок.
    2. Сравнивает последние URL новостей с уже обработанными в кеше.
    3. Если новых ссылок нет — возвращает (False, cached_schedules).
    4. Если есть хотя бы одна новая публикация — парсит ее, обновляет кеш и возвращает (True, fresh_schedules).
    """
    global _cache
    now = time.time()

    # Инициализация кеша из диска при необходимости
    if _cache['data'] is None:
        disk_data, disk_ts, disk_urls = load_disk_cache()
        if disk_data is not None:
            _cache['data'] = disk_data
            _cache['timestamp'] = disk_ts
            _cache['urls'] = disk_urls

    try:
        session = create_session()
        fresh_urls = get_latest_maintenance_urls(session=session)
    except Exception as e:
        logging.error(f"Error checking latest maintenance urls: {e}")
        fresh_urls = []

    if not fresh_urls:
        _cache['site_online'] = False
        logging.warning("Check news feed: failed to fetch news list from site.")
        return False, (_cache['data'] or [])

    _cache['site_online'] = True
    cached_urls = _cache.get('urls') or []

    # Проверяем, появились ли новые ссылки
    has_new_urls = any(u not in cached_urls for u in fresh_urls) if cached_urls else True

    if not force_parse and not has_new_urls and _cache['data']:
        logging.info(f"Check news feed: no new news URLs detected ({len(fresh_urls)} URLs unchanged). Matcher skipped.")
        return False, _cache['data']

    logging.info(f"Check news feed: new news detected (fresh={fresh_urls}, cached={cached_urls})! Parsing pages...")
    all_schedules = []
    try:
        for idx, url in enumerate(fresh_urls):
            if idx > 0:
                time.sleep(1.5)
            all_schedules.extend(parse_maintenance_page(url, session=session))
    except Exception as e:
        logging.error(f"Error while parsing maintenance pages: {e}")

    if all_schedules:
        _cache['data'] = all_schedules
        _cache['timestamp'] = now
        _cache['site_online'] = True
        _cache['last_successful_fetch'] = now
        _cache['urls'] = fresh_urls
        save_disk_cache(all_schedules, now, urls=fresh_urls)
        logging.info(f"Parsed and cached {len(all_schedules)} schedules from {len(fresh_urls)} news pages.")
        return True, all_schedules
    elif _cache['data']:
        logging.warning("Parsing fresh pages returned 0 items, keeping previous cache.")
        return False, _cache['data']

    return False, []

def get_all_recent_schedules():
    global _cache
    now = time.time()
    
    # 1. Проверяем in-memory кеш
    if _cache['data'] is not None and (now - _cache['timestamp']) < _cache['ttl']:
        logging.info(f"Returning cached schedules ({len(_cache['data'])} entries, age={int(now - _cache['timestamp'])}s)")
        return _cache['data']
    
    # 2. Если в памяти пусто, подтягиваем с диска
    if _cache['data'] is None:
        disk_data, disk_ts, disk_urls = load_disk_cache()
        if disk_data is not None:
            _cache['data'] = disk_data
            _cache['timestamp'] = disk_ts
            _cache['urls'] = disk_urls
            logging.info(f"Loaded {len(disk_data)} schedules from disk cache (age={int(now - disk_ts)}s)")
            if (now - disk_ts) < _cache['ttl']:
                return disk_data

    # 3. Запускаем проверку ленты новостей
    has_new, schedules = check_for_new_schedules()
    return schedules or _cache.get('data') or []

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    urls = get_latest_maintenance_urls()
    if urls:
        print(f"Testing with: {urls[0]}")
        results = parse_maintenance_page(urls[0])
        print(f"Parsed {len(results)} entries.")
        found = False
        for res in results:
            if "СЕДИНСКИЙ" in res['addresses'].upper():
                print(f"MATCH FOUND: [{res['district']}] {res['date']} {res['time']}: {res['addresses']}")
                found = True
        if not found:
            print("Target address not found in the parsed results.")
            # Print a few to see what we DID find
            for res in results[:5]:
                 print(f"DEBUG: [{res['district']}] {res['date']} {res['time']}: {res['addresses'][:50]}...")
