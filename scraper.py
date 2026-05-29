import requests
from bs4 import BeautifulSoup
import re
import logging
import urllib3

# Suppress insecure request warnings if we disable SSL verify
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://www.yakutskenergo.ru"
NEWS_LIST_URL = f"{BASE_URL}/press/news/news-remont/"

# Headers to mimic a real browser
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def get_latest_maintenance_urls():
    try:
        # verify=False handles the SSL certificate issue
        response = requests.get(NEWS_LIST_URL, headers=HEADERS, timeout=15, verify=False)
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
        logging.error(f"Error fetching news list: {e}")
        return []

def parse_maintenance_page(url):
    try:
        logging.info(f"Parsing page: {url}")
        response = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        text_block = soup.find('div', class_='text-block')
        if not text_block:
            logging.warning(f"No text-block found on page {url}")
            return []

        schedules = []
        current_district = "ЯКУТСК" # Default
        current_date = None

        # Process the entire content of the text block as a string to handle nested or broken tags
        # We'll split by common line breaks
        content_text = text_block.decode_contents()
        # Replace common tags with newlines for easier parsing
        content_text = re.sub(r'<(p|div|br|u|b|li)[^>]*>', '\n', content_text)
        content_text = re.sub(r'</(p|div|u|b|li)>', '\n', content_text)
        
        lines = [BeautifulSoup(line, 'html.parser').get_text(strip=True) for line in content_text.split('\n')]

        for text in lines:
            if not text: continue

            # District detection
            # Check for city or district names in uppercase or with specific keywords
            if ("РАЙОН" in text.upper() or 
                (text.isupper() and len(text) > 3 and not re.search(r'\d', text)) or
                text.startswith("г. ") or text.startswith("п. ")):
                # Filter out obvious non-district strings
                if not re.search(r'(\d{2}:\d{2}|В ГРАФИКЕ)', text):
                    candidate = text.replace('г. ', '').replace('п. ', '').strip().upper()
                    if len(candidate) < 50:
                        current_district = candidate
                        continue

            # Date detection: e.g., "29 мая:"
            date_match = re.search(r'^(\d{1,2})\s+([а-яА-Я]+)', text)
            if date_match and not re.search(r'\d{2}:\d{2}', text):
                current_date = date_match.group(0)
                continue

            # Entry detection: TIME - ADDRESSES [- REASON]
            # Time pattern: XX:XX-XX:XX
            time_match = re.search(r'(\d{2}:\d{2}\s*[-–]\s*\d{2}:\d{2})', text)
            if time_match and current_district and current_date:
                time_range = time_match.group(1).replace(' ', '')
                # The rest of the string after the time is addresses and reason
                # Addresses are usually between the time and the last dash or the end
                rest = text[time_match.end():].strip()
                # Clean up leading dashes
                rest = re.sub(r'^[–-]\s*', '', rest)
                
                # Split by the last "–" or "-" which usually indicates the reason
                parts = re.split(r'\s*[–-]\s*', rest)
                if len(parts) > 1:
                    reason = parts[-1]
                    addresses = " – ".join(parts[:-1])
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
        logging.error(f"Error parsing page {url}: {e}")
        return []

def get_all_recent_schedules():
    urls = get_latest_maintenance_urls()
    all_schedules = []
    for url in urls:
        all_schedules.extend(parse_maintenance_page(url))
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
