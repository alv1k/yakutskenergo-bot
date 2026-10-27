"""
Address Parser V2 — Интеллектуальный парсер и матчер адресов для Yakutskenergo Bot.

Особенности V2:
1. Интеграция базы топонимов Якутскэнерго (`data/yakutskenergo_streets.json`) + встроенная офлайн-база.
2. Поддержка номерных улиц (не обрезает '1-й Академический', '50 лет Советской Армии', '2-я Березовая').
3. Двусторонний маппинг синонимов и инициалов (С. Данилова <-> Семена Данилова <-> Данилова).
4. Поддержка четности/нечетности домов: '(четные)', '(нечетные)'.
5. Двухуровневый матчинг трактов и километров: 'Сергеляхское шоссе 5 км, 40/8' и 'Вилюйский тракт 5-7 км'.
6. Нативная поддержка микрорайонов без улиц (202, 203, Марха, Борисовка).
7. Поддержка СОТ, ДСК, СОПК с кавычками и без, сопоставление участков.
8. Поддержка литер, корпусов, строений ('33 к2', '96 стр3', '158А', '252д').
9. 100% обратная совместимость по интерфейсу с функциями V1 из `bot.py`.
"""

import os
import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Set, Tuple, Dict, Any

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
GAZETTEER_FILE = os.path.join(DATA_DIR, "yakutskenergo_streets.json")


# ---------------------------------------------------------------------------
# Встроенный справочник топонимов (работает автономно даже без внешнего json)
# ---------------------------------------------------------------------------
DEFAULT_KNOWN_TRACTS = [
    "сергеляхское шоссе",
    "хатын-юряхское шоссе",
    "вилюйский тракт",
    "покровский тракт",
    "намский тракт",
    "маганский тракт",
    "старый покровский тракт",
    "шоссе отдыха",
]

DEFAULT_NUMBERED_STREETS = [
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

DEFAULT_ALIASES = {
    # Инициалы <-> Полное имя
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
    # Шоссе и сокращения
    "хатын-юряхское": "хатын-юряхское шоссе",
    "хатын юряхское": "хатын-юряхское шоссе",
    "хатын юряхское шоссе": "хатын-юряхское шоссе",
    "хатынюряхское": "хатын-юряхское шоссе",
    "сергеляхское": "сергеляхское шоссе",
    "сергеляхское ш": "сергеляхское шоссе",
    "вилюйский тр": "вилюйский тракт",
    "покровский тр": "покровский тракт",
    "намский тр": "намский тракт",
    "маганский тр": "маганский тракт",
    "ш отдыха": "шоссе отдыха",
    # Известные микрорайоны
    "202": "202 мкр",
    "202 микрорайон": "202 мкр",
    "203": "203 мкр",
    "203 микрорайон": "203 мкр",
    # Орфографические вариации
    "бестужева марлинского": "бестужева-марлинского",
    "ново сайсарская": "ново-сайсарская",
    "ново карьерная": "ново-карьерная",
}


class Gazetteer:
    """Хранилище знаний о топонимах Якутска и улусов."""
    def __init__(self, json_path: str = GAZETTEER_FILE):
        self.tracts: Set[str] = set(DEFAULT_KNOWN_TRACTS)
        self.numbered_streets: Set[str] = set(DEFAULT_NUMBERED_STREETS)
        self.aliases: Dict[str, str] = dict(DEFAULT_ALIASES)
        self.sots: Set[str] = set()
        self.microdistricts: Set[str] = {"202", "203", "марха", "борисовка", "прометей", "птицефабрика", "северный", "ытык-кюель"}
        self.all_streets: Set[str] = set()
        self.load_from_json(json_path)

    def load_from_json(self, path: str):
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.tracts.update(data.get("tracts_and_highways", []))
            self.sots.update(data.get("sots_and_dsks", []))
            self.microdistricts.update(data.get("microdistricts", []))
            if "aliases" in data:
                self.aliases.update(data["aliases"])
            for dist_streets in data.get("districts", {}).values():
                for st in dist_streets:
                    st_lower = st.lower()
                    self.all_streets.add(st_lower)
                    if any(num in st_lower for num in ["1", "2", "3", "4", "5", "50", "40"]):
                        self.numbered_streets.add(st_lower)
        except Exception as e:
            logger.warning(f"Ошибка загрузки справочника топонимов: {e}")

    def canonicalize(self, name: str) -> str:
        """Приводит название улицы к канонической форме."""
        if not name:
            return ""
        norm = name.lower().replace("ё", "е").strip()
        norm = re.sub(r'[^а-я0-9\s-]', '', norm)
        norm = re.sub(r'\s+', ' ', norm).strip()

        # Проверка прямого синонима
        if norm in self.aliases:
            return self.aliases[norm]

        # Проверка инверсии: "семена данилова" -> каноническая "семена данилова"
        # "данилова" -> если есть "семена данилова", возвращаем полное
        for alias_k, canonical_v in self.aliases.items():
            if norm == alias_k:
                return canonical_v
            # Если введено просто одно слово (фамилия), а в каноне она есть
            tokens = canonical_v.split()
            if len(tokens) >= 2 and norm == tokens[-1]:
                return canonical_v

        return norm


GAZETTEER = Gazetteer()


# ---------------------------------------------------------------------------
# Модели данных
# ---------------------------------------------------------------------------
@dataclass
class ParsedAddress:
    raw_input: str
    street_name: str
    canonical_name: str
    core_name: str
    house_num: Optional[str] = None
    main_house: Optional[int] = None
    sub_house: Optional[str] = None
    korpus: Optional[str] = None
    structure: Optional[str] = None
    flat: Optional[str] = None
    mkr: Optional[str] = None
    is_km_address: bool = False
    km_value: Optional[int] = None
    km_from: Optional[int] = None
    km_to: Optional[int] = None
    is_sot: bool = False
    sot_name: Optional[str] = None
    district: Optional[str] = None

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Возвращает совместимый словарь для кода V1."""
        return {
            'street_name': self.street_name,
            'core_name': self.core_name,
            'house_num': self.house_num,
            'main_house': self.main_house,
            'sub_house': self.sub_house,
            'korpus': self.korpus,
            'mkr': self.mkr,
            'is_km_address': self.is_km_address,
            'km_value': self.km_value,
            'km_from': self.km_from,
            'km_to': self.km_to,
        }


@dataclass
class ScheduleAddressGroup:
    street_norm: str
    canonical_name: str
    full_norm: str
    houses_raw: str
    houses_part: str
    is_km: bool = False
    km_value: Optional[int] = None
    km_from: Optional[int] = None
    km_to: Optional[int] = None
    is_sot: bool = False
    sot_name: Optional[str] = None
    mkr: Optional[str] = None
    parity: Optional[str] = None  # "even", "odd", or None
    house_ranges: List[Tuple[int, int]] = field(default_factory=list)
    explicit_houses: Set[str] = field(default_factory=set)
    is_partial: bool = False
    all_street_affected: bool = False

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Возвращает совместимый словарь для кода V1."""
        return {
            'street_norm': self.street_norm,
            'km_value': self.km_value,
            'km_from': self.km_from,
            'km_to': self.km_to,
            'houses_part': self.houses_part,
            'full_norm': self.full_norm,
            'houses': self.houses_raw,
        }


@dataclass
class MatchResult:
    matched: bool
    confidence: str  # "exact_house", "house_range", "all_street", "km_exact", "km_range", "sot_match", "none"
    reason: str
    matched_group: Optional[ScheduleAddressGroup] = None


# ---------------------------------------------------------------------------
# Вспомогательные функции нормализации
# ---------------------------------------------------------------------------
_ADDR_PREFIXES = {
    "улица": "ул", "ул": "ул", "переулок": "пер", "пер": "пер",
    "проспект": "пр", "пр": "пр", "пр-кт": "пр", "пр-т": "пр",
    "проезд": "пр-д", "пр-д": "пр-д", "микрорайон": "мкр", "мкр": "мкр",
    "шоссе": "ш", "ш": "ш", "тупик": "туп", "туп": "туп",
    "тракт": "тр", "тр": "тр", "бульвар": "б-р", "б-р": "б-р",
    "набережная": "наб", "наб": "наб", "площадь": "пл", "пл": "пл",
}

_PREFIX_REGEX = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in sorted(_ADDR_PREFIXES.keys(), key=len, reverse=True)) + r')\b\.?',
    re.IGNORECASE
)


def normalize_district(text: str) -> str:
    """Нормализует название района (улуса)."""
    if not text:
        return ""
    text = text.upper().replace('Ё', 'Е')
    prefixes = ["Г.", "ПГТ", "ПОС.", "ПОСЕЛОК", "С.", "СЕЛО", "УЛУС", "РАЙОН", "Р-Н"]
    for p in prefixes:
        text = re.sub(rf'\b{re.escape(p)}\b', ' ', text)
    text = re.sub(r'[^А-Я0-9\s-]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def normalize_address(text: str) -> str:
    """Базовая очистка текста адреса с сохранением структуры номеров."""
    if not text:
        return ""
    text = text.lower().replace('ё', 'е')
    # Отделяем точки и запятые пробелами (ул.депутатская -> ул депутатская)
    text = text.replace('.', ' ').replace(',', ' ')
    # Удаляем служебную информацию о квартирах, подъездах, этажах
    text = re.sub(r'\s*(?:кв|квартира|подъезд|этаж)\s*\d+.*$', '', text)
    # Отделяем прилипшие буквы префиксов домов/корпусов: д3 -> дом 3, к2 -> корп 2
    text = re.sub(r'\bд(\d+)\b', r' \1 ', text)
    text = re.sub(r'\b(дом|уч|участка|уч-к|стр|строение)\b', ' ', text)
    text = re.sub(r'\bд\b', ' ', text)
    # Нормализуем префиксы улиц
    text = _PREFIX_REGEX.sub(lambda m: _ADDR_PREFIXES.get(m.group(1).lower(), m.group(1).lower()), text)
    # Корпуса
    text = re.sub(r'\bкорп(?:ус)?\b', ' корп ', text)
    text = re.sub(r'(\d+)\s*к(\d+)\b', r'\1 корп \2', text)
    # Очищаем спецсимволы, оставляя буквы, цифры, дефис, слэш
    text = re.sub(r'[^а-я0-9\s\/\-–]', '', text)
    # Склеиваем слипшиеся литеры: 158 а -> 158а
    text = re.sub(r'(\d+)\s+([а-я])(?=[\s,;\-–/]|$)', r'\1\2', text)
    return re.sub(r'\s+', ' ', text).strip()


# ---------------------------------------------------------------------------
# Парсинг адреса пользователя
# ---------------------------------------------------------------------------
def parse_user_address(street_raw: str, district: Optional[str] = None) -> ParsedAddress:
    """
    Разбирает строку адреса, введенную пользователем.
    """
    original = street_raw.strip()
    norm = original.lower().replace('ё', 'е')

    # 1. Извлечение квартиры/комнаты (для очистки)
    flat_val = None
    flat_m = re.search(r'\bкв(?:артира)?\.?\s*(\d+)', norm)
    if flat_m:
        flat_val = flat_m.group(1)
        norm = norm[:flat_m.start()] + ' ' + norm[flat_m.end():]

    # 2. Проверка СОТ / ДСК / кооперативов
    is_sot = False
    sot_name = None
    sot_m = re.search(r'\b(сот|дск|сопк|дспк|снт)\b\s*[«"]?([а-яa-z0-9-]+)[»"]?', norm)
    if sot_m:
        is_sot = True
        sot_name = sot_m.group(2).strip()

    # 3. Микрорайоны (202, 203, Марха, Борисовка и т.д.)
    mkr_val = None
    # 202/203 могут быть указаны без слова "мкр" (например "203 32")
    mkr_match = re.search(r'\b(202|203)\b(?:\s*(?:мкр|микрорайон))?', norm)
    if mkr_match:
        mkr_val = mkr_match.group(1)
    else:
        # Для остальных микрорайонов слово "мкр" обязательно
        mkr_explicit = re.search(r'\b(?:мкр|микрорайон)\.?\s*([а-я0-9-]+)', norm)
        if mkr_explicit:
            mkr_val = mkr_explicit.group(1).strip()
            # Проверяем, не указана ли конкретная улица внутри микрорайона:
            # например "мкр. Марха ул. депутатская д3"
            mkr_street_m = re.search(r'\b(?:мкр|микрорайон)\.?\s*[а-я0-9-]+\s+(?:ул\.?|улица|пер\.?|переулок)\s*([а-я0-9-]+)', norm)
            if mkr_street_m:
                # В приоритете сама улица внутри микрорайона
                norm = re.sub(r'\b(?:мкр|микрорайон)\.?\s*[а-я0-9-]+\s*', '', norm).strip()
                mkr_val = None

    # 4. Корпус и строение
    korpus_val = None
    structure_val = None
    korp_m = re.search(r'\b(?:корп|корпус|к)\.?\s*(\d+)', norm)
    if korp_m:
        korpus_val = korp_m.group(1)
        norm = norm[:korp_m.start()] + ' ' + norm[korp_m.end():]

    str_m = re.search(r'\b(?:стр|строение|с)\.?\s*(\d+)', norm)
    if str_m:
        structure_val = str_m.group(1)
        norm = norm[:str_m.start()] + ' ' + norm[str_m.end():]

    # Нормализуем остаток строки
    norm_cleaned = normalize_address(norm)

    # 5. Проверка километровых адресов (тракты и шоссе)
    is_km = False
    km_val = None
    km_from = None
    km_to = None

    km_range_m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*км\b', norm_cleaned)
    km_single_m = re.search(r'(\d+)\s*км\b', norm_cleaned)
    has_tract = any(tr in norm_cleaned for tr in GAZETTEER.tracts) or bool(re.search(r'\b(тр|ш|тракт|шоссе)\b', norm_cleaned))

    if (km_range_m or km_single_m) and has_tract:
        is_km = True
        if km_range_m:
            km_from = int(km_range_m.group(1))
            km_to = int(km_range_m.group(2))
            km_str = km_range_m.group(0)
        else:
            km_val = int(km_single_m.group(1))
            km_str = km_single_m.group(0)

        km_idx = norm_cleaned.find(km_str)
        street_part = norm_cleaned[:km_idx].strip()
        rest_part = norm_cleaned[km_idx + len(km_str):].strip()
    else:
        street_part = norm_cleaned
        rest_part = ""

    # 6. Извлечение номера дома и дроби
    house_num = None
    main_house = None
    sub_house = None

    if rest_part:
        # Номер дома после км
        h_match = re.search(r'^([\d]+(?:[\/][\d\w]+)?|[а-я0-9\/\-]+)', rest_part)
        if h_match:
            house_num = h_match.group(1).replace(" ", "")
    else:
        if mkr_val and not any(tr in street_part for tr in GAZETTEER.tracts):
            # В микрорайоне номер дома часто идет сразу после номера мкр или слова мкр
            rest_mkr = re.sub(r'\b(?:мкр|микрорайон)\b\.?', ' ', street_part)
            rest_mkr = re.sub(r'\b' + re.escape(mkr_val) + r'\b', ' ', rest_mkr).strip()
            h_match = re.search(r'(\d+[\w\/-]*)', rest_mkr)
            if h_match:
                house_num = h_match.group(1).replace(" ", "")
                street_part = f"{mkr_val} мкр"
            else:
                street_part = f"{mkr_val} мкр"
        else:
            # Обычная улица: ищем дом в конце строки, НО с защитой для номерных улиц
            is_known_numbered = any(st in street_part for st in GAZETTEER.numbered_streets)
            if is_known_numbered:
                # Находим конец известной номерной улицы
                for num_st in sorted(GAZETTEER.numbered_streets, key=len, reverse=True):
                    if num_st in street_part:
                        idx = street_part.find(num_st) + len(num_st)
                        rest_after_st = street_part[idx:].strip()
                        if rest_after_st:
                            h_m = re.search(r'([\d]+(?:[\/][\d\w]+)?|[а-я0-9\/\-]+)$', rest_after_st)
                            if h_m:
                                house_num = h_m.group(1)
                                street_part = street_part[:idx].strip()
                        break
            else:
                h_match = re.search(r'(\d+[\w\/-]*)$', street_part)
                if h_match:
                    house_num = h_match.group(1).replace(" ", "")
                    street_part = street_part[:h_match.start()].strip()

    # Декомпозиция номера дома: 40/8, 158а, 4
    if house_num:
        m_parts = re.match(r'^(\d+)(?:[/]([\d\w]+)|([а-яa-z]))?$', house_num, re.IGNORECASE)
        if m_parts:
            main_house = int(m_parts.group(1))
            sub_house = m_parts.group(2) or m_parts.group(3)
        else:
            first_digits = re.match(r'^(\d+)', house_num)
            if first_digits:
                main_house = int(first_digits.group(1))
                sub_house = house_num[len(first_digits.group(1)):].lstrip('/') or None

    # Если номер дома был вынесен в корпус для 202/203
    if mkr_val and not house_num and korpus_val:
        house_num = korpus_val
        main_house = int(korpus_val) if korpus_val.isdigit() else None

    # Очистка названия улицы от служебных префиксов
    street_name = street_part.strip()
    core_name = re.sub(r'\b(пр-д|б-р|ул|пер|пр|ш|наб|пл|туп|тр|мкр|корп|снт|сот|днт|гсп|км)\b', '', street_name).strip()
    if not core_name:
        core_name = mkr_val or street_name

    canonical_name = GAZETTEER.canonicalize(core_name)

    return ParsedAddress(
        raw_input=original,
        street_name=street_name,
        canonical_name=canonical_name,
        core_name=core_name,
        house_num=house_num,
        main_house=main_house,
        sub_house=sub_house,
        korpus=korpus_val,
        structure=structure_val,
        flat=flat_val,
        mkr=mkr_val,
        is_km_address=is_km,
        km_value=km_val,
        km_from=km_from,
        km_to=km_to,
        is_sot=is_sot,
        sot_name=sot_name,
        district=district
    )


# ---------------------------------------------------------------------------
# Парсинг адресов из сводок Якутскэнерго
# ---------------------------------------------------------------------------
_HOUSE_RANGE_RE = re.compile(
    r'(?<![\d/])(\d+)(?:[а-я]|\s*/\s*[\d\w]+)?\s*[-–]\s*(\d+)(?:[а-я]|\s*/\s*[\d\w]+)?(?!\s*км\b)',
    re.IGNORECASE
)


def split_schedule_addresses(addr_string: str) -> List[Dict[str, Any]]:
    """
    Разбивает составную строку адресов из графика Якутскэнерго
    на структурированные группы.
    Возвращает список словарей, совместимый с форматом V1.
    """
    raw_groups = split_schedule_addresses_v2(addr_string)
    return [grp.to_legacy_dict() for grp in raw_groups]


def split_schedule_addresses_v2(addr_string: str) -> List[ScheduleAddressGroup]:
    """
    Парсер сводок V2 с поддержкой словаря топонимов Якутскэнерго.
    """
    if not addr_string:
        return []

    # Предварительное разбиение по запятым с учетом известных топонимов
    # Делим только перед новыми названиями улиц (буквы или известные номерные улицы),
    # но не делим перед обычными номерами домов текущей улицы
    split_pat = re.compile(
        r',\s*(?=[А-Яа-я«]|(?:50\s*лет|40\s*лет|1-й|2-й|3-й|2-я|3-я|2\s*березовая|три\s*сосны|202|203))'
    )
    raw_parts = split_pat.split(addr_string)
    groups: List[ScheduleAddressGroup] = []

    for part in raw_parts:
        part = part.strip().rstrip('.,;')
        if not part:
            continue

        part_norm = normalize_address(part)
        part_norm = re.sub(r'(\d+)\s*[–-]\s*(?=\d)', r'\1-', part_norm)
        if not part_norm:
            continue

        # Четность / нечетность
        parity = None
        if re.search(r'\b(?:четные|чётные|чет|чёт)\b', part, re.IGNORECASE):
            parity = "even"
        elif re.search(r'\b(?:нечетные|нечётные|нечет|нечёт)\b', part, re.IGNORECASE):
            parity = "odd"

        # Частичное отключение
        is_partial = "частич" in part.lower()

        # Километровые отметки
        is_km = False
        km_val = None
        km_from = None
        km_to = None

        km_range_m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*км\b', part_norm)
        km_single_m = re.search(r'(\d+)\s*км\b', part_norm)
        if km_range_m:
            is_km = True
            km_from = int(km_range_m.group(1))
            km_to = int(km_range_m.group(2))
        elif km_single_m:
            is_km = True
            km_val = int(km_single_m.group(1))

        # СОТ / ДСК
        is_sot = False
        sot_name = None
        sot_m = re.search(r'\b(?:сот|дск|сопк|дспк|снт)\b\s*[«"]?([а-я0-9-]+)[»"]?', part, re.IGNORECASE)
        if sot_m:
            is_sot = True
            sot_name = sot_m.group(1).lower().strip()

        # Разделение улицы и номеров домов
        street_tokens, house_tokens = _extract_street_and_houses_v2(part_norm)
        street_norm = ' '.join(street_tokens).strip()
        houses_part = ' '.join(house_tokens).strip()

        # Диапазоны домов: 1-34, 22-24, 65-79
        house_ranges: List[Tuple[int, int]] = []
        for rm in _HOUSE_RANGE_RE.finditer(houses_part):
            try:
                lo, hi = int(rm.group(1)), int(rm.group(2))
                if lo > hi:
                    lo, hi = hi, lo
                house_ranges.append((lo, hi))
            except Exception:
                pass

        # Точные номера домов: 77, 77/1, 100/2, 158а
        explicit_houses: Set[str] = set()
        for tok in house_tokens:
            tok_clean = tok.strip(',;.')
            if re.match(r'^\d+[\w\/-]*$', tok_clean) and not tok_clean.endswith('км'):
                explicit_houses.add(tok_clean)

        # Если номеров домов нет вообще — отключена вся улица/район
        all_street_affected = (len(explicit_houses) == 0 and len(house_ranges) == 0 and not is_km)

        core_st = re.sub(r'\b(пр-д|б-р|ул|пер|пр|ш|наб|пл|туп|тр|мкр|корп|снт|сот|днт|гсп|км)\b', '', street_norm).strip()
        canonical_name = GAZETTEER.canonicalize(core_st) if core_st else street_norm

        groups.append(ScheduleAddressGroup(
            street_norm=street_norm,
            canonical_name=canonical_name,
            full_norm=part_norm,
            houses_raw=houses_part,
            houses_part=houses_part,
            is_km=is_km,
            km_value=km_val,
            km_from=km_from,
            km_to=km_to,
            is_sot=is_sot,
            sot_name=sot_name,
            parity=parity,
            house_ranges=house_ranges,
            explicit_houses=explicit_houses,
            is_partial=is_partial,
            all_street_affected=all_street_affected
        ))

    return groups


def _extract_street_and_houses_v2(norm: str) -> Tuple[List[str], List[str]]:
    """
    Интеллектуальное разделение названия улицы и списка домов.
    Не режет номерные улицы ('1-й академический', '50 лет советской армии').
    """
    tokens = norm.split()
    if not tokens:
        return [], []

    # Проверка совпадения с известными номерными улицами
    for num_st in sorted(GAZETTEER.numbered_streets, key=len, reverse=True):
        if num_st in norm:
            idx = norm.find(num_st)
            end_idx = idx + len(num_st)
            street_str = norm[:end_idx].strip()
            houses_str = norm[end_idx:].strip()
            return street_str.split(), houses_str.split()

    # Проверка трактов и шоссе
    for tr in sorted(GAZETTEER.tracts, key=len, reverse=True):
        if tr in norm:
            idx = norm.find(tr)
            end_idx = idx + len(tr)
            street_str = norm[:end_idx].strip()
            houses_str = norm[end_idx:].strip()
            return street_str.split(), houses_str.split()

    # Обычный поиск границы домов
    house_start = len(tokens)
    for i, tok in enumerate(tokens):
        # Пропускаем порядковые суффиксы улиц (1-й, 2-я, 50-летия)
        if re.match(r'^\d+-(?:й|я|е|го|летия)$', tok):
            continue

        # Диапазоны или дроби домов (77/1, 10-25)
        if re.match(r'^\d+[а-я]?[/–-]', tok):
            house_start = i
            break
        # Одиночный номер с литерой (158а, 40)
        if re.match(r'^\d+[а-я]$', tok):
            if i == len(tokens) - 1 or re.match(r'^[\d/–-]+[а-я]?$', tokens[i + 1]):
                house_start = i
                break
        # Чистое число (дом)
        if re.match(r'^\d+$', tok):
            if i == len(tokens) - 1:
                house_start = i
                break
            next_tok = tokens[i + 1]
            if re.match(r'^[\d/–-]+[а-я]?$', next_tok) or next_tok in ('корп', 'к', 'стр'):
                house_start = i
                break

    return tokens[:house_start], tokens[house_start:]


# ---------------------------------------------------------------------------
# Сопоставление адреса с графиком отключений (Matcher)
# ---------------------------------------------------------------------------
def match_address_against_schedule(
    user_addr_dict: Dict[str, Any],
    norm_user_district: str,
    schedule_item: Dict[str, Any],
    addr_groups: List[Dict[str, Any]]
) -> bool:
    """
    Совместимый фасад матчера для V1.
    Принимает словари и возвращает bool.
    """
    res = match_address_detailed(user_addr_dict, norm_user_district, schedule_item, addr_groups)
    return res.matched


def match_address_detailed(
    user_addr_input: Any,
    norm_user_district: str,
    schedule_item: Dict[str, Any],
    addr_groups_input: Any
) -> MatchResult:
    """
    Детальный матчер V2 с объяснением причины и диагностикой.
    """
    # 1. Проверка района (улуса)
    sched_dist = normalize_district(schedule_item.get('district', ''))
    if norm_user_district and sched_dist:
        if norm_user_district not in sched_dist and sched_dist not in norm_user_district:
            return MatchResult(matched=False, confidence="none", reason=f"Несовпадение района: '{norm_user_district}' != '{sched_dist}'")

    # Приведение пользователя к ParsedAddress
    if isinstance(user_addr_input, ParsedAddress):
        ua = user_addr_input
    elif isinstance(user_addr_input, dict):
        # Восстанавливаем объект из V1 словаря
        ua = ParsedAddress(
            raw_input=user_addr_input.get('street_name', ''),
            street_name=user_addr_input.get('street_name', ''),
            canonical_name=GAZETTEER.canonicalize(user_addr_input.get('core_name', '')),
            core_name=user_addr_input.get('core_name', ''),
            house_num=user_addr_input.get('house_num'),
            main_house=user_addr_input.get('main_house'),
            sub_house=user_addr_input.get('sub_house'),
            korpus=user_addr_input.get('korpus'),
            mkr=user_addr_input.get('mkr'),
            is_km_address=user_addr_input.get('is_km_address', False),
            km_value=user_addr_input.get('km_value'),
            km_from=user_addr_input.get('km_from'),
            km_to=user_addr_input.get('km_to'),
            district=norm_user_district
        )
    else:
        return MatchResult(matched=False, confidence="none", reason="Некорректный формат адреса пользователя")

    if not ua.core_name and not ua.mkr:
        return MatchResult(matched=False, confidence="none", reason="Не удалось выделить основу названия улицы")

    # Преобразование групп расписания
    groups_v2: List[ScheduleAddressGroup] = []
    if addr_groups_input and isinstance(addr_groups_input[0], ScheduleAddressGroup):
        groups_v2 = addr_groups_input
    else:
        groups_v2 = split_schedule_addresses_v2(schedule_item.get('addresses', ''))

    user_core = ua.core_name.lower()
    user_canonical = ua.canonical_name.lower()

    for grp in groups_v2:
        # Сравнение улицы с учетом синонимов и инициалов
        grp_canonical = grp.canonical_name.lower()
        grp_street = grp.street_norm.lower()

        street_matched = _check_street_match(user_core, user_canonical, grp_street, grp_canonical, ua.mkr, grp.mkr)
        if not street_matched:
            continue

        # 2. Километровые адреса (Тракты и шоссе)
        if ua.is_km_address:
            km_match, reason = _check_km_match(ua, grp)
            if km_match:
                return MatchResult(matched=True, confidence="km_exact" if grp.km_value else "km_range", reason=reason, matched_group=grp)
            continue

        # 3. СОТ / ДСК адреса
        if ua.is_sot or grp.is_sot:
            sot_match, reason = _check_sot_match(ua, grp)
            if sot_match:
                return MatchResult(matched=True, confidence="sot_match", reason=reason, matched_group=grp)
            continue

        # 4. Проверка четности / нечетности стороны улицы
        if grp.parity and ua.main_house is not None:
            if grp.parity == "even" and (ua.main_house % 2 != 0):
                continue  # дом нечетный, а работы на четной
            if grp.parity == "odd" and (ua.main_house % 2 == 0):
                continue  # дом четный, а работы на нечетной

        # 5. Проверка конкретного номера дома
        if ua.main_house is not None:
            user_main = str(ua.main_house)
            user_h_clean = (ua.house_num or user_main).lower()

            # 5.1 Прямое совпадение номера с литерой или дробью (например 19г, 19/1, 158а)
            if ua.sub_house:
                sub_pat = re.compile(rf'\b{user_main}\s*[-/]?\s*{re.escape(ua.sub_house)}\b', re.IGNORECASE)
                if user_h_clean in grp.explicit_houses or sub_pat.search(grp.houses_raw):
                    return MatchResult(matched=True, confidence="exact_house", reason=f"Точное совпадение дома {user_h_clean}", matched_group=grp)

            # 5.2 Совпадение базового номера дома (например график указывает "19" или отключен весь дом)
            base_pat = re.compile(rf'\b{user_main}\b(?![0-9а-яА-Яa-zA-Z/])')
            if user_main in grp.explicit_houses or base_pat.search(grp.houses_raw):
                # Проверка корпуса при наличии
                if ua.korpus:
                    korp_pat = re.escape(user_main) + r'\s*корп\s*' + re.escape(ua.korpus)
                    if re.search(korp_pat, grp.houses_raw) or not re.search(re.escape(user_main) + r'\s*корп', grp.houses_raw):
                        return MatchResult(matched=True, confidence="exact_house", reason=f"Совпадение дома {user_main} (корп. {ua.korpus})", matched_group=grp)
                else:
                    return MatchResult(matched=True, confidence="exact_house", reason=f"Точное совпадение дома {user_main}", matched_group=grp)

            # 5.2 Попадание в диапазон домов (22-24, 65-79, 1-34)
            for lo, hi in grp.house_ranges:
                if lo <= ua.main_house <= hi:
                    return MatchResult(matched=True, confidence="house_range", reason=f"Дом {ua.main_house} в диапазоне {lo}-{hi}", matched_group=grp)

            # 5.3 Если для улицы дома не конкретизированы (отключена вся улица)
            if grp.all_street_affected or not re.search(r'\d', grp.houses_part):
                return MatchResult(matched=True, confidence="all_street", reason="Улица отключена целиком (дома не конкретизированы)", matched_group=grp)

        else:
            # Пользователь подписан на улицу без указания номера дома
            return MatchResult(matched=True, confidence="all_street", reason="Подписка на всю улицу (без указания дома)", matched_group=grp)

    return MatchResult(matched=False, confidence="none", reason="Совпадений в текущем графике не обнаружено")


def _check_street_match(user_core: str, user_canonical: str, grp_street: str, grp_canonical: str, user_mkr: Optional[str], grp_mkr: Optional[str]) -> bool:
    """Проверка эквивалентности названий улиц с учетом синонимов."""
    if user_mkr and (user_mkr in grp_street or user_mkr in grp_canonical):
        return True

    # 1. Прямое совпадение канонических имен
    if user_canonical == grp_canonical:
        return True

    # 2. Совпадение основ
    if user_core and user_core in grp_street:
        return True

    # 3. Фамилия / имя (Данилова <-> Семена Данилова)
    u_tokens = [t for t in user_core.split() if len(t) > 2]
    g_tokens = [t for t in grp_canonical.split() if len(t) > 2]
    if u_tokens and g_tokens:
        # Если ключевая фамилия совпадает
        if u_tokens[-1] == g_tokens[-1]:
            return True

    # 4. Проверка через регулярное выражение с дефисами
    pat = re.compile(r'(?:^|[\s,/])' + re.escape(user_core) + r'(?:\s|$|[^\wа-яё])', re.IGNORECASE)
    if pat.search(grp_street) or pat.search(grp_canonical):
        return True

    return False


def _check_km_match(ua: ParsedAddress, grp: ScheduleAddressGroup) -> Tuple[bool, str]:
    """Проверка совпадения километровых адресов."""
    grp_has_km = (grp.km_value is not None or grp.km_from is not None)
    if not grp_has_km:
        if grp.is_partial or "частич" in grp.full_norm:
            return True, "Тракт отключен частично (затрагивает км-адреса)"
        return False, ""

    km_target = ua.km_value or ua.km_from
    if km_target is None:
        return True, "Отключение на тракте"

    # Точный километр
    if grp.km_value is not None and grp.km_value == km_target:
        if ua.main_house is not None and grp.houses_part:
            if re.search(r'\b' + str(ua.main_house) + r'\b', grp.houses_part):
                return True, f"Точное совпадение: {km_target} км, дом {ua.main_house}"
            elif not re.search(r'\b\d+\b', grp.houses_part):
                return True, f"Совпадение: {km_target} км (вся ветка)"
            return False, ""
        return True, f"Совпадение по километру: {km_target} км"

    # Попадание в диапазон километров (например, 5-7 км покрывает 6 км)
    if grp.km_from is not None and grp.km_to is not None:
        if grp.km_from <= km_target <= grp.km_to:
            return True, f"Километр {km_target} попадает в диапазон {grp.km_from}-{grp.km_to} км"

    return False, ""


def _check_sot_match(ua: ParsedAddress, grp: ScheduleAddressGroup) -> Tuple[bool, str]:
    """Проверка совпадения СОТ/ДСК кооперативов."""
    sot_u = ua.sot_name or ua.core_name
    sot_g = grp.sot_name or grp.canonical_name
    if not sot_u or not sot_g:
        return False, ""

    if sot_u in sot_g or sot_g in sot_u:
        if ua.main_house is not None and grp.explicit_houses:
            if str(ua.main_house) in grp.explicit_houses or re.search(r'\b' + str(ua.main_house) + r'\b', grp.houses_raw):
                return True, f"СОТ '{sot_u}', участок {ua.main_house}"
            return False, ""
        return True, f"СОТ '{sot_u}' (весь кооператив)"
    return False, ""
