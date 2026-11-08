import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, date, timedelta, timezone
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from bot import get_scheduled_check_date, check_updates


class TestNotificationDateFiltering(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.ykt_tz = timezone(timedelta(hours=9))

    def test_get_scheduled_check_date(self):
        # Morning at 08:30 -> today
        dt_morning = datetime(2026, 11, 6, 8, 30, tzinfo=self.ykt_tz)
        self.assertEqual(get_scheduled_check_date(dt_morning), date(2026, 11, 6))

        # Midday boundary 12:59 -> today
        dt_boundary = datetime(2026, 11, 6, 12, 59, tzinfo=self.ykt_tz)
        self.assertEqual(get_scheduled_check_date(dt_boundary), date(2026, 11, 6))

        # Evening at 15:00 -> tomorrow
        dt_evening = datetime(2026, 11, 6, 15, 0, tzinfo=self.ykt_tz)
        self.assertEqual(get_scheduled_check_date(dt_evening), date(2026, 11, 7))

        # Night/Evening at 20:30 -> tomorrow
        dt_night = datetime(2026, 11, 6, 20, 30, tzinfo=self.ykt_tz)
        self.assertEqual(get_scheduled_check_date(dt_night), date(2026, 11, 7))

        # Sunday evening at 18:00 -> Monday
        dt_sunday = datetime(2026, 11, 8, 18, 0, tzinfo=self.ykt_tz) # Sunday
        self.assertEqual(get_scheduled_check_date(dt_sunday), date(2026, 11, 9)) # Monday

    @patch('bot.database')
    @patch('bot.scraper')
    async def test_morning_check_matches_only_today(self, mock_scraper, mock_db):
        mock_scraper.is_site_online.return_value = True
        mock_db.is_blocked.return_value = False
        mock_db.is_notified.return_value = False
        mock_db.get_all_addresses.return_value = [
            (111, 1, 'Якутск', 'Ленина 15')
        ]

        # 3 entries in schedules: today (6 Nov), tomorrow (7 Nov), next week (9 Nov)
        schedules = [
            {
                'district': 'ЯКУТСК',
                'date': '6 ноября',
                'time': '09:00-12:00',
                'addresses': 'Ленина 15, 17',
                'reason': 'Ремонт ТП'
            },
            {
                'district': 'ЯКУТСК',
                'date': '7 ноября',
                'time': '10:00-13:00',
                'addresses': 'Ленина 15',
                'reason': 'Монтаж ВЛ'
            },
            {
                'district': 'ЯКУТСК',
                'date': '9 ноября',
                'time': '09:00-17:00',
                'addresses': 'Ленина 15, 20',
                'reason': 'Капитальный ремонт'
            }
        ]

        app = MagicMock()
        app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))

        # Freeze now_ykt to morning of 6 Nov (09:00 YKT)
        fixed_now = datetime(2026, 11, 6, 9, 0, tzinfo=self.ykt_tz)
        with patch('bot.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            # Automatic run (is_manual=False)
            stats = await check_updates(app, is_manual=False, schedules=schedules)

        # Only 6 Nov should match!
        self.assertEqual(stats['target_date_schedules'], 1)
        self.assertEqual(stats['total_matches'], 1)
        self.assertEqual(stats['users_notified'], 1)
        self.assertEqual(stats['date_str'], '06.11.2026')

        # Check message content sent to user
        call_args = app.bot.send_message.call_args[1]
        self.assertIn('06.11.2026', call_args['text'])
        self.assertIn('Ремонт ТП', call_args['text'])
        self.assertNotIn('Монтаж ВЛ', call_args['text'])
        self.assertNotIn('09.11.2026', call_args['text'])

    @patch('bot.database')
    @patch('bot.scraper')
    async def test_evening_check_matches_only_tomorrow(self, mock_scraper, mock_db):
        mock_scraper.is_site_online.return_value = True
        mock_db.is_blocked.return_value = False
        mock_db.is_notified.return_value = False
        mock_db.get_all_addresses.return_value = [
            (111, 1, 'Якутск', 'Ленина 15')
        ]

        schedules = [
            {
                'district': 'ЯКУТСК',
                'date': '6 ноября',
                'time': '09:00-12:00',
                'addresses': 'Ленина 15',
                'reason': 'Ремонт ТП'
            },
            {
                'district': 'ЯКУТСК',
                'date': '7 ноября',
                'time': '10:00-13:00',
                'addresses': 'Ленина 15',
                'reason': 'Монтаж ВЛ'
            },
            {
                'district': 'ЯКУТСК',
                'date': '9 ноября',
                'time': '09:00-17:00',
                'addresses': 'Ленина 15',
                'reason': 'Капитальный ремонт'
            }
        ]

        app = MagicMock()
        app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=2))

        # Freeze now_ykt to evening of 6 Nov (18:00 YKT) -> tomorrow is 7 Nov
        fixed_now = datetime(2026, 11, 6, 18, 0, tzinfo=self.ykt_tz)
        with patch('bot.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            # Automatic run (is_manual=False)
            stats = await check_updates(app, is_manual=False, schedules=schedules)

        # Only 7 Nov should match!
        self.assertEqual(stats['target_date_schedules'], 1)
        self.assertEqual(stats['total_matches'], 1)
        self.assertEqual(stats['users_notified'], 1)
        self.assertEqual(stats['date_str'], '07.11.2026')

        call_args = app.bot.send_message.call_args[1]
        self.assertIn('07.11.2026', call_args['text'])
        self.assertIn('Монтаж ВЛ', call_args['text'])
        self.assertNotIn('Ремонт ТП', call_args['text'])
        self.assertNotIn('09.11.2026', call_args['text'])

    @patch('bot.database')
    @patch('bot.scraper')
    async def test_manual_check_matches_all_future_dates(self, mock_scraper, mock_db):
        mock_scraper.is_site_online.return_value = True
        mock_db.is_blocked.return_value = False
        mock_db.is_notified.return_value = False
        mock_db.get_user_addresses.return_value = [
            (1, 'Якутск', 'Ленина 15')
        ]

        schedules = [
            {
                'district': 'ЯКУТСК',
                'date': '6 ноября',
                'time': '09:00-12:00',
                'addresses': 'Ленина 15',
                'reason': 'Сегодняшний ремонт'
            },
            {
                'district': 'ЯКУТСК',
                'date': '7 ноября',
                'time': '10:00-13:00',
                'addresses': 'Ленина 15',
                'reason': 'Завтрашний ремонт'
            },
            {
                'district': 'ЯКУТСК',
                'date': '9 ноября',
                'time': '09:00-17:00',
                'addresses': 'Ленина 15',
                'reason': 'Ремонт в понедельник'
            }
        ]

        app = MagicMock()
        app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=3))

        # Freeze now_ykt to morning of 6 Nov (09:00 YKT)
        fixed_now = datetime(2026, 11, 6, 9, 0, tzinfo=self.ykt_tz)
        with patch('bot.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            # Manual check by user (target_chat_id=111, is_manual=True)
            stats = await check_updates(app, target_chat_id=111, is_manual=True, schedules=schedules)

        # All 3 dates must match!
        self.assertEqual(stats['total_matches'], 3)
        self.assertEqual(stats['user_matches_found'], 3)

        call_args = app.bot.send_message.call_args[1]
        msg_text = call_args['text']
        # Message should include all 3 works
        self.assertIn('Сегодняшний ремонт', msg_text)
        self.assertIn('Завтрашний ремонт', msg_text)
        self.assertIn('Ремонт в понедельник', msg_text)

        # Check chronological ordering: 6 ноября before 7 ноября before 9 ноября
        pos_today = msg_text.find('Сегодняшний ремонт')
        pos_tomorrow = msg_text.find('Завтрашний ремонт')
        pos_next = msg_text.find('Ремонт в понедельник')
        self.assertTrue(pos_today < pos_tomorrow < pos_next)

    def test_scraper_cache_filters_old_dates(self):
        import scraper
        import tempfile

        old_date_entry = {'date': '20 октября', 'time': '09:00-12:00', 'addresses': 'Ленина 1', 'reason': 'старое'}
        fresh_entry = {'date': '6 ноября', 'time': '09:00-12:00', 'addresses': 'Ленина 1', 'reason': 'новое'}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_cache = os.path.join(tmpdir, "cache.json")
            with patch('scraper.CACHE_FILE', tmp_cache), patch('scraper.CACHE_DIR', tmpdir):
                scraper.save_disk_cache([old_date_entry, fresh_entry], 12345)
                loaded, ts, _ = scraper.load_disk_cache()
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0]['reason'], 'новое')


if __name__ == '__main__':
    unittest.main()
