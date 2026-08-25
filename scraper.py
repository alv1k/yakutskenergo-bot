import requests
from bs4 import BeautifulSoup
import re
import logging
import urllib3
import time

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
}

def get_latest_maintenance_urls():
    for attempt in range(2):
        try:
            response = requests.get(NEWS_LIST_URL, headers=HEADERS, timeout=20, verify=False, proxies=PROXIES)
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
            logging.warning(f"Attempt {attempt+1} error fetching news list: {e}")
            if attempt == 0:
                time.sleep(2)
    return []

def parse_maintenance_page(url):
    for attempt in range(2):
        try:
            logging.info(f"Parsing page: {url}")
            response = requests.get(url, headers=HEADERS, timeout=20, verify=False, proxies=PROXIES)
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
            
            lines = [BeautifulSoup(line, 'html.parser').get_text(strip=True) for line in content_text.split('\n')]

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
            logging.warning(f"Attempt {attempt+1} error parsing page {url}: {e}")
            if attempt == 0:
                time.sleep(2)
    return []

def get_all_recent_schedules():
    global _cache
    now = time.time()
    
    # Проверяем кеш
    if _cache['data'] is not None and (now - _cache['timestamp']) < _cache['ttl']:
        logging.info(f"Returning cached schedules ({len(_cache['data'])} entries, age={int(now - _cache['timestamp'])}s)")
        return _cache['data']
    
    # Парсим свежие данные
    urls = get_latest_maintenance_urls()
    all_schedules = []
    for url in urls:
        all_schedules.extend(parse_maintenance_page(url))
    
    # Сохраняем в кеш (только если успешно распарсили данные)
    if all_schedules:
        _cache['data'] = all_schedules
        _cache['timestamp'] = now
        logging.info(f"Fresh schedules parsed: {len(all_schedules)} entries")
    elif _cache['data'] is not None:
        logging.warning(f"Failed to fetch fresh schedules, falling back to previous cache ({len(_cache['data'])} entries)")
        return _cache['data']
    else:
        logging.warning("No schedules parsed and no cache available")
    
    return all_schedules

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
