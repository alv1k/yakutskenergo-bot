import unittest
from bs4 import BeautifulSoup
import re
import html
from address_parser_v2 import parse_user_address, split_schedule_addresses_v2, match_address_detailed, normalize_district

class TestEmergencyNewsAndTickets(unittest.TestCase):
    def test_uchur_and_dots_time_parsing(self):
        # Snippet exactly from news 73749
        html_snippet = '''
        <div class="text-block">
            <p align="justify"><u>г. ЯКУТСК:</u></p>
            <p align="justify"><b>9</b><b> ноября</b><b>:</b></p>
            <p align="justify">08.00-10.00 — мкр. Марха: ул. Лобановка 1-15/2, пер. Учур, ул. Дзержинского 1-17 — срочные неотложные работы.</p>
        </div>
        '''
        soup = BeautifulSoup(html_snippet, 'html.parser')
        text_block = soup.find('div', class_='text-block')
        content_text = text_block.decode_contents()
        content_text = re.sub(r'<(p|div|br|li|h[1-6]|tr)[^>]*>', '\n', content_text)
        content_text = re.sub(r'</(p|div|li|h[1-6]|tr)>', '\n', content_text)
        content_text = re.sub(r'<[^>]+>', '', content_text)
        lines = [html.unescape(line).strip() for line in content_text.split('\n') if line.strip()]

        schedules = []
        current_district = "ЯКУТСК"
        current_date = None

        for text in lines:
            if ("РАЙОН" in text.upper() or (text.isupper() and len(text) > 3 and not re.search(r'\d', text)) or text.startswith("г. ") or text.startswith("п. ")):
                if not re.search(r'(\d{1,2}[.:]\d{2}|В ГРАФИКЕ)', text):
                    candidate = text.replace('г. ', '').replace('п. ', '').strip(' :').strip().upper()
                    if len(candidate) < 50:
                        current_district = candidate
                        continue
            date_match = re.search(r'^(\d{1,2})\s+([а-яА-Я]+)', text)
            if date_match and not re.search(r'\d{1,2}[.:]\d{2}', text):
                current_date = date_match.group(0)
                continue
            time_match = re.search(r'(\d{1,2}[.:]\d{2}\s*[-–—]\s*\d{1,2}[.:]\d{2})', text)
            if time_match and current_district and current_date:
                raw_time = time_match.group(1).replace(' ', '').replace('.', ':')
                time_range = re.sub(r'[–—]', '-', raw_time)
                schedules.append({
                    'district': current_district,
                    'date': current_date,
                    'time': time_range,
                    'addresses': text[time_match.end():].strip().lstrip('–-—').strip()
                })

        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0]['district'], 'ЯКУТСК')
        self.assertEqual(schedules[0]['date'], '9 ноября')
        self.assertEqual(schedules[0]['time'], '08:00-10:00')

        # Test address matching
        user_addr = parse_user_address('Марха, переулок Учур 23/1', district='ЯКУТСК')
        groups = split_schedule_addresses_v2(schedules[0]['addresses'])
        res = match_address_detailed(user_addr, normalize_district('ЯКУТСК'), schedules[0], groups)
        self.assertTrue(res.matched)
        self.assertEqual(res.confidence, 'all_street')

    def test_mkr_no_overmatch_unrelated_street(self):
        # User in Марха on Дружба народов should not match Лобановка
        user_addr = parse_user_address('Дружба народов 2 мкр Марха', district='ЯКУТСК')
        sched = {
            'district': 'ЯКУТСК',
            'date': '9 ноября',
            'time': '08:00-10:00',
            'addresses': 'мкр. Марха: ул. Лобановка 1-15/2'
        }
        groups = split_schedule_addresses_v2(sched['addresses'])
        res = match_address_detailed(user_addr, normalize_district('ЯКУТСК'), sched, groups)
        self.assertFalse(res.matched)

if __name__ == '__main__':
    unittest.main()
