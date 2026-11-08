import unittest
from unittest.mock import AsyncMock, MagicMock
import asyncio
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

class TestNotificationChunking(unittest.IsolatedAsyncioTestCase):

    async def test_safe_send_message_markdown_error_fallback(self):
        from bot import safe_send_message
        
        bot = MagicMock()
        # First call raises Markdown parsing error, second succeeds
        bot.send_message = AsyncMock(side_effect=[
            Exception("Can't parse entities: character '*' is reserved"),
            MagicMock(message_id=123)
        ])

        res = await safe_send_message(bot, chat_id=1005097965, text="*Test* _invalid markdown")
        self.assertEqual(bot.send_message.call_count, 2)
        # Fallback called with stripped markdown characters
        second_call_text = bot.send_message.call_args_list[1].kwargs.get('text')
        self.assertNotIn('*', second_call_text)
        self.assertNotIn('_', second_call_text)

    def test_chunking_logic(self):
        # Simulate building chunks with 5 large outage entries
        unique_entries = []
        for i in range(10):
            entry = {
                'district': 'ЯКУТСК',
                'date': '30 октября',
                'time': '09:00-17:00',
                'addresses': f'Длинная улица {i}, дом ' + ', '.join(str(x) for x in range(1, 150)),
                'reason': 'Капитальный ремонт электросетей'
            }
            unique_entries.append((f'Улица {i}', entry, i, f'hash_{i}'))

        chunks = []
        date_str = "30.10.2026"
        current_header = f"⚠️ *Внимание! Обнаружены плановые работы:*\n📅 *Проверены списки за:* {date_str}\n\n"
        current_text = current_header
        current_items = []

        for street, m, aid, s_hash in unique_entries:
            addr_text = m.get('addresses', '')
            if len(addr_text) > 2500:
                addr_text = addr_text[:2500] + "... (список адресов сокращен)"
            entry_text = f"📅 Дата: {m['date']}\n🕒 Время: {m['time']}\n🏠 Где: {addr_text}\n🛠 Причина: {m['reason']}"
            block = f"📍 *Адрес:* {street}\n{entry_text}\n\n"

            if len(current_text) + len(block) > 3600:
                if current_items:
                    chunks.append((current_text, current_items))
                current_text = f"⚠️ *Продолжение списка плановых работ:*\n\n" + block
                current_items = [(aid, s_hash, entry_text)]
            else:
                current_text += block
                current_items.append((aid, s_hash, entry_text))

        if current_items:
            chunks.append((current_text, current_items))

        # Check that multiple chunks were created and NONE exceeds 3800 chars
        self.assertGreater(len(chunks), 1)
        for chunk_text, items in chunks:
            self.assertLessEqual(len(chunk_text), 3800)
            self.assertGreater(len(items), 0)

    def test_get_matched_address_snippet(self):
        from bot import parse_user_address, split_schedule_addresses, get_matched_address_snippet
        s = {
            'district': 'ЯКУТСК',
            'addresses': 'Лобановка (Марха мкр.) 1-15/2, Советская 2-80, Оленекская, Абыйская',
            'date': '5 ноября',
            'time': '09:00-12:00',
            'reason': 'тест'
        }
        groups = split_schedule_addresses(s['addresses'])
        ua = parse_user_address('Оленекская 23')
        snip = get_matched_address_snippet(ua, 'ЯКУТСК', s, groups)
        self.assertEqual(snip, 'Оленекская')

    def test_smart_polling_schedule_windows(self):
        from datetime import datetime, timezone, timedelta
        from bot import get_next_schedule_time, is_in_active_window

        ykt_tz = timezone(timedelta(hours=9))
        base = datetime(2026, 11, 5, 0, 0, tzinfo=ykt_tz)

        # 1. Ночь 02:00 -> следующий запуск в 07:00 сегодня (не активно)
        t_night = base.replace(hour=2, minute=0)
        self.assertFalse(is_in_active_window(t_night))
        self.assertEqual(get_next_schedule_time(t_night).strftime("%H:%M"), "07:00")

        # 2. Утреннее окно (07:00 - 11:00 каждые 30 мин)
        t_0715 = base.replace(hour=7, minute=15)
        self.assertTrue(is_in_active_window(t_0715))
        self.assertEqual(get_next_schedule_time(t_0715).strftime("%H:%M"), "07:30")

        t_1045 = base.replace(hour=10, minute=45)
        self.assertTrue(is_in_active_window(t_1045))
        self.assertEqual(get_next_schedule_time(t_1045).strftime("%H:%M"), "11:00")

        # 3. Тихий час (11:00 - 15:00 без проверок -> переход на 15:00)
        t_1101 = base.replace(hour=11, minute=1)
        self.assertFalse(is_in_active_window(t_1101))
        self.assertEqual(get_next_schedule_time(t_1101).strftime("%H:%M"), "15:00")

        t_1330 = base.replace(hour=13, minute=30)
        self.assertFalse(is_in_active_window(t_1330))
        self.assertEqual(get_next_schedule_time(t_1330).strftime("%H:%M"), "15:00")

        # 4. Вечернее окно (15:00 - 21:00 каждый час)
        t_1500 = base.replace(hour=15, minute=0)
        self.assertTrue(is_in_active_window(t_1500))
        self.assertEqual(get_next_schedule_time(t_1500).strftime("%H:%M"), "16:00")

        t_1920 = base.replace(hour=19, minute=20)
        self.assertTrue(is_in_active_window(t_1920))
        self.assertEqual(get_next_schedule_time(t_1920).strftime("%H:%M"), "20:00")

        # 5. Конец дня 21:00 -> пауза до завтра 07:00
        t_2101 = base.replace(hour=21, minute=1)
        self.assertFalse(is_in_active_window(t_2101))
        next_dt = get_next_schedule_time(t_2101)
        self.assertEqual(next_dt.strftime("%d %H:%M"), "06 07:00")

    def test_check_for_new_schedules_caching(self):
        from unittest.mock import patch
        import scraper

        scraper._cache['urls'] = ['https://test/1', 'https://test/2']
        scraper._cache['data'] = [{'dummy': 1}]

        # 1. Если ссылки в ленте те же самые -> has_new=False, парсер страниц не вызывается
        with patch('scraper.get_latest_maintenance_urls', return_value=['https://test/1', 'https://test/2']):
            with patch('scraper.parse_maintenance_page') as mock_parse:
                has_new, data = scraper.check_for_new_schedules()
                self.assertFalse(has_new)
                self.assertEqual(len(data), 1)
                mock_parse.assert_not_called()

        # 2. Если появилась новая ссылка -> has_new=True, парсер вызывается и кеш обновляется
        with patch('scraper.get_latest_maintenance_urls', return_value=['https://test/3', 'https://test/1']):
            with patch('scraper.parse_maintenance_page', return_value=[{'dummy': 2}]):
                with patch('scraper.save_disk_cache') as mock_save:
                    has_new, data = scraper.check_for_new_schedules()
                    self.assertTrue(has_new)
                    self.assertEqual(len(data), 2)
                    self.assertTrue(mock_save.called)

if __name__ == '__main__':
    unittest.main()
