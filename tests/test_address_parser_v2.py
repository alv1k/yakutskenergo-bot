#!/usr/bin/env python3
"""
Unit tests for Address Parser V2.
"""

import sys
import os
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from address_parser_v2 import (
    parse_user_address,
    split_schedule_addresses,
    split_schedule_addresses_v2,
    match_address_against_schedule,
    match_address_detailed,
    normalize_district,
    normalize_address,
    GAZETTEER
)


class TestAddressParserV2(unittest.TestCase):

    def setUp(self):
        # Гарантируем инициализацию справочника
        self.assertTrue(len(GAZETTEER.tracts) > 0)

    def test_parse_user_address_numbered_streets(self):
        # 1. 50 лет Советской Армии
        ua1 = parse_user_address("ул. 50 лет Советской Армии 33 корпус 2")
        self.assertIn("50 лет советской армии", ua1.street_name)
        self.assertEqual(ua1.main_house, 33)
        self.assertEqual(ua1.korpus, "2")

        # 2. 1-й Академический переулок
        ua2 = parse_user_address("пер. 1-й Академический, 5")
        self.assertIn("1-й академический", ua2.street_name)
        self.assertEqual(ua2.main_house, 5)

        # 3. 2-я Березовая
        ua3 = parse_user_address("2 Березовая 1")
        self.assertIn("2 березовая", ua3.street_name)
        self.assertEqual(ua3.main_house, 1)

    def test_parse_user_address_microdistricts(self):
        # 203 мкр
        ua1 = parse_user_address("203 мкр 17")
        self.assertEqual(ua1.mkr, "203")
        self.assertEqual(ua1.main_house, 17)

        # 202 микрорайон корпус
        ua2 = parse_user_address("202 микрорайон, 13")
        self.assertEqual(ua2.mkr, "202")
        self.assertEqual(ua2.main_house, 13)

        # 203 с корпусом и квартирой
        ua3 = parse_user_address("203 КОРПУС 25 КВ 152")
        self.assertEqual(ua3.mkr, "203")
        self.assertEqual(ua3.main_house, 25)
        self.assertEqual(ua3.flat, "152")

    def test_parse_user_address_tracts_and_km(self):
        # Сергеляхское шоссе 5 км, 40/8
        ua1 = parse_user_address("сергеляхское шоссе 5 км, 40/8")
        self.assertTrue(ua1.is_km_address)
        self.assertEqual(ua1.km_value, 5)
        self.assertEqual(ua1.main_house, 40)
        self.assertEqual(ua1.sub_house, "8")

        # Вилюйский тракт 4 км 24
        ua2 = parse_user_address("Вилюйский тр. 4 км 24")
        self.assertTrue(ua2.is_km_address)
        self.assertEqual(ua2.km_value, 4)
        self.assertEqual(ua2.main_house, 24)

    def test_parse_user_address_complex_houses(self):
        # Литера 158А
        ua1 = parse_user_address("Лермонтова 158А")
        self.assertEqual(ua1.main_house, 158)
        self.assertEqual(ua1.sub_house.lower(), "а")

        # Строение 96 стр3
        ua2 = parse_user_address("Ильменская 96 стр3")
        self.assertEqual(ua2.main_house, 96)
        self.assertEqual(ua2.structure, "3")

        # СОТ Пламя 252д
        ua3 = parse_user_address("СОТ Пламя 252д")
        self.assertTrue(ua3.is_sot)
        self.assertEqual(ua3.sot_name, "пламя")
        self.assertEqual(ua3.main_house, 252)
        self.assertEqual(ua3.sub_house.lower(), "д")

        # Дробь + корпус: Проспект Ленина 5/2 к2
        ua4 = parse_user_address("Проспект Ленина 5/2 к2")
        self.assertEqual(ua4.main_house, 5)
        self.assertEqual(ua4.sub_house, "2")
        self.assertEqual(ua4.korpus, "2")

    def test_initials_canonicalization(self):
        # Семена Данилова <-> С. Данилова
        ua_short = parse_user_address("С. Данилова 15")
        ua_long = parse_user_address("Семена Данилова 15")
        self.assertEqual(ua_short.canonical_name, "семена данилова")
        self.assertEqual(ua_long.canonical_name, "семена данилова")

        # Федора Попова <-> Ф. Попова
        ua_popov_short = parse_user_address("Ф. Попова 4")
        ua_popov_long = parse_user_address("Федора Попова 4")
        self.assertEqual(ua_popov_short.canonical_name, "федора попова")
        self.assertEqual(ua_popov_long.canonical_name, "федора попова")

    def test_split_schedule_addresses(self):
        raw = (
            "Чехова 77, 77/1, Лермонтова 100/2, "
            "пер. 1-й Академический 1-10, "
            "ул. 50 лет Советской Армии 20-30"
        )
        groups = split_schedule_addresses_v2(raw)
        self.assertTrue(len(groups) >= 4)

        # Проверка, что 1-й Академический не обрезан до пустого "пер"
        acad_grp = next((g for g in groups if "1-й академический" in g.street_norm), None)
        self.assertIsNotNone(acad_grp)
        self.assertIn((1, 10), acad_grp.house_ranges)

        # Проверка 50 лет Советской Армии
        sovet_grp = next((g for g in groups if "50 лет советской армии" in g.street_norm), None)
        self.assertIsNotNone(sovet_grp)
        self.assertIn((20, 30), sovet_grp.house_ranges)

    def test_matching_initials_cross_match(self):
        # Пользователь ввел Семена Данилова 15, в графике С. Данилова 1- 34
        ua = parse_user_address("Семена Данилова 15")
        sched = {
            'district': 'ЯКУТСК',
            'addresses': 'С. Данилова 1- 34, Пионерская 11-47/1'
        }
        groups = split_schedule_addresses(sched['addresses'])
        matched = match_address_against_schedule(ua.to_legacy_dict(), "ЯКУТСК", sched, groups)
        self.assertTrue(matched)

        # Наоборот: пользователь ввел С. Данилова 15, в графике Семена Данилова 3-30
        ua2 = parse_user_address("С. Данилова 15")
        sched2 = {
            'district': 'ЯКУТСК',
            'addresses': 'Семена Данилова 3-30, Пионерская 11-45'
        }
        groups2 = split_schedule_addresses(sched2['addresses'])
        matched2 = match_address_against_schedule(ua2.to_legacy_dict(), "ЯКУТСК", sched2, groups2)
        self.assertTrue(matched2)

    def test_matching_parity_even_odd(self):
        # График: Лермонтова 20-30 (четные)
        sched = {
            'district': 'ЯКУТСК',
            'addresses': 'Лермонтова 20-30 (четные)'
        }
        groups = split_schedule_addresses(sched['addresses'])

        ua_even = parse_user_address("Лермонтова 22")
        ua_odd = parse_user_address("Лермонтова 23")

        self.assertTrue(match_address_against_schedule(ua_even.to_legacy_dict(), "ЯКУТСК", sched, groups))
        self.assertFalse(match_address_against_schedule(ua_odd.to_legacy_dict(), "ЯКУТСК", sched, groups))

    def test_matching_tracts_and_km_range(self):
        # В графике: Сергеляхское шоссе 4-6 км
        sched = {
            'district': 'ЯКУТСК',
            'addresses': 'Сергеляхское шоссе 4-6 км частично'
        }
        groups = split_schedule_addresses(sched['addresses'])

        # Дом на 5 км должен совпасть
        ua_in = parse_user_address("сергеляхское шоссе 5 км, 40/8")
        self.assertTrue(match_address_against_schedule(ua_in.to_legacy_dict(), "ЯКУТСК", sched, groups))

        # Дом на 9 км НЕ должен совпасть
        ua_out = parse_user_address("сергеляхское шоссе 9 км, 12")
        self.assertFalse(match_address_against_schedule(ua_out.to_legacy_dict(), "ЯКУТСК", sched, groups))

    def test_matching_all_street_when_no_houses_specified(self):
        # В графике: Чиряева (частично) без указания домов
        sched = {
            'district': 'ЯКУТСК',
            'addresses': 'Чиряева (частично), Хабарова 11'
        }
        groups = split_schedule_addresses(sched['addresses'])

        # Житель любого дома по Чиряева должен получить уведомление
        ua = parse_user_address("Чиряева 5")
        self.assertTrue(match_address_against_schedule(ua.to_legacy_dict(), "ЯКУТСК", sched, groups))

        # Пользователь подписанный просто на улицу Чиряева
        ua_whole = parse_user_address("Чиряева")
        self.assertTrue(match_address_against_schedule(ua_whole.to_legacy_dict(), "ЯКУТСК", sched, groups))

    def test_matching_sot(self):
        sched = {
            'district': 'ЯКУТСК',
            'addresses': 'СОТ «Пламя», СОТ «Золотинка», ДСК «Победа»'
        }
        groups = split_schedule_addresses(sched['addresses'])

        ua = parse_user_address("СОТ Пламя 252д")
        self.assertTrue(match_address_against_schedule(ua.to_legacy_dict(), "ЯКУТСК", sched, groups))


if __name__ == '__main__':
    unittest.main()
