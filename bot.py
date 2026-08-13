import logging
import asyncio
import re
import os
import hashlib
from dotenv import load_dotenv
from datetime import datetime, time, timedelta, timezone
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    filters, 
    ConversationHandler,
    CallbackQueryHandler
)
import database
import scraper

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Load environment variables
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID = os.getenv("ADMIN_TG_ID")

# Conversation states
SET_STREET, CONFIRM_YAKUTSK, SET_DISTRICT = range(3)


def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🔍 Проверить сейчас")],
        [KeyboardButton("➕ Добавить адрес"), KeyboardButton("➖ Удалить адрес")],
        [KeyboardButton("📋 Мои адреса"), KeyboardButton("❓ Справка")],
        [KeyboardButton("📩 Поддержка")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    addresses = database.get_user_addresses(chat_id)
    if addresses:
        addr_list = "\n".join([f"  {i+1}. {a[2]}" for i, a in enumerate(addresses)])
        msg = f"У вас отслеживается {len(addresses)} адресов:\n{addr_list}"
    else:
        msg = "У вас пока нет адресов для отслеживания."
    await update.message.reply_text(
        f"Привет! Я бот для уведомления о плановых отключениях электроэнергии Якутскэнерго.\n\n{msg}\n\n"
        "Добавьте адрес кнопкой «➕ Добавить адрес».\n"
        "Проверить — «🔍 Проверить сейчас».",
        reply_markup=get_main_keyboard()
    )

async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    count = database.get_address_count(chat_id)
    if count >= database.MAX_ADDRESSES:
        await update.message.reply_text(
            f"⚠️ Достигнут лимит адресов ({database.MAX_ADDRESSES}).\n"
            "Удалите ненужный адрес кнопкой «➖ Удалить адрес»."
        )
        return ConversationHandler.END
    await update.message.reply_text(
        f"Добавление адреса ({count + 1}/{database.MAX_ADDRESSES}).\n\n"
        "Шаг 1: Введите вашу улицу и номер дома.\n"
        "Примеры: Лермонтова 45, переулок Сединский, Вилюйский тракт 4 км, 203 мкр 17"
    )
    return SET_STREET

async def process_street_first(update: Update, context: ContextTypes.DEFAULT_TYPE):
    street = update.message.text
    valid, error_msg = validate_address_input(street, "Улица")
    if not valid:
        await update.message.reply_text(f"⚠️ {error_msg}")
        return SET_STREET
    context.user_data['temp_street'] = street.strip()

    keyboard = [
        [
            InlineKeyboardButton("✅ Да, Якутск", callback_data="ykt_yes"),
            InlineKeyboardButton("📍 Нет, другой район", callback_data="ykt_no")
        ]
    ]
    await update.message.reply_text(
        f"Вы ввели: {street}\n\nЭто адрес в г. Якутске?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONFIRM_YAKUTSK

async def confirm_yakutsk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    street = context.user_data.get('temp_street')

    if query.data == "ykt_yes":
        district = "ЯКУТСК"
        result = database.add_address(query.message.chat_id, district=district, street=street)
        if result is False:
            await query.edit_message_text(f"⚠️ Достигнут лимит адресов ({database.MAX_ADDRESSES}).")
        else:
            await query.edit_message_text(
                f"✅ Адрес добавлен!\n"
                f"Район: {district}\n"
                f"Улица: {street}\n\n"
                "Уведомления придут в 9:00 или 21:00 по Якутску."
            )
        return ConversationHandler.END
    else:
        await query.edit_message_text(
            f"Улица: {street}\n\n"
            "Теперь введите название вашего района (улуса).\n"
            "Примеры: Жатай, Намский, Хангаласский, Мирнинский"
        )
        return SET_DISTRICT

async def process_district_after(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_district = update.message.text
    valid, error_msg = validate_address_input(raw_district, "Район")
    if not valid:
        await update.message.reply_text(f"⚠️ {error_msg}")
        return SET_DISTRICT
    district = normalize_district(raw_district)
    street = context.user_data.get('temp_street')

    result = database.add_address(update.effective_chat.id, district=district, street=street)
    if result is False:
        await update.message.reply_text(
            f"⚠️ Достигнут лимит адресов ({database.MAX_ADDRESSES}).",
            reply_markup=get_main_keyboard()
        )
    else:
        await update.message.reply_text(
            f"✅ Адрес добавлен!\n"
            f"Район: {district}\n"
            f"Улица: {street}\n\n"
            "Уведомления придут в 9:00 или 21:00 по Якутску.",
            reply_markup=get_main_keyboard()
        )
    return ConversationHandler.END

async def start_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['support_mode'] = True
    await update.message.reply_text(
        "📩 Напишите ваше сообщение — оно будет отправлено администратору.\n\n"
        "Для отмены: /cancel или ❌ Отмена",
        reply_markup=ReplyKeyboardMarkup([["❌ Отмена"]], resize_keyboard=True)
    )

async def process_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "❌ Отмена" or text == "/cancel":
        context.user_data.pop('support_mode', None)
        await update.message.reply_text("Отменено.", reply_markup=get_main_keyboard())
        return

    chat_id = update.effective_chat.id
    user = update.effective_user

    user_info = f"@{user.username}" if user.username else (user.first_name or "")
    username = user.username or None
    admin_text = f"💬 Поддержка от {user_info} (ID: {chat_id}):\n\n{text}"

    ticket_id = database.create_support_ticket(chat_id, text, username)
    admin_text += f"\n\n#ticket{ticket_id}"

    try:
        await context.bot.send_message(chat_id=int(ADMIN_TG_ID), text=admin_text)
    except Exception as e:
        logging.error(f"Support forward error: {e}")
        await update.message.reply_text("⚠️ Ошибка отправки. Попробуйте позже.", reply_markup=get_main_keyboard())
        return

    context.user_data.pop('support_mode', None)
    await update.message.reply_text(
        "✅ Сообщение отправлено. Ответ придёт сюда.",
        reply_markup=get_main_keyboard()
    )

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != int(ADMIN_TG_ID):
        return
    if not update.message.reply_to_message:
        return
    orig = update.message.reply_to_message.text or ""
    m = re.search(r"ID:\s*(\d+)", orig)
    if not m:
        await update.message.reply_text("⚠️ Не найден ID пользователя в сообщении.")
        return
    target_chat_id = int(m.group(1))
    reply_text = update.message.text
    if not reply_text:
        return
    ticket_id = re.search(r"#ticket(\d+)", orig)
    if ticket_id:
        database.reply_support_ticket(int(ticket_id.group(1)), reply_text)
    try:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=f"📩 Ответ от поддержки:\n\n{reply_text}"
        )
        await update.message.reply_text("✅ Ответ отправлен.")
    except Exception as e:
        logging.error(f"Admin reply error: {e}")
        await update.message.reply_text(f"⚠️ Ошибка: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('support_mode', None)
    await update.message.reply_text("Отменено.", reply_markup=get_main_keyboard())
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addresses = database.get_user_addresses(update.effective_chat.id)
    if addresses:
        lines = []
        for i, (aid, district, street) in enumerate(addresses):
            lines.append(f"{i+1}. [{district}] {street}")
        await update.message.reply_text(
            f"📋 Ваши адреса ({len(addresses)}/{database.MAX_ADDRESSES}):\n\n" + "\n".join(lines)
        )
    else:
        await update.message.reply_text("У вас пока нет адресов. Нажмите «➕ Добавить адрес».")

# Rate limiting
user_cooldowns = {}
CHECK_COOLDOWN = 300 

async def check_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.now().timestamp()
    if user_id in user_cooldowns:
        if now - user_cooldowns[user_id] < CHECK_COOLDOWN:
            rem = int(CHECK_COOLDOWN - (now - user_cooldowns[user_id]))
            await update.message.reply_text(f"Пожалуйста, подождите {rem} сек.")
            return
    user_cooldowns[user_id] = now
    await update.message.reply_text("Запускаю проверку обновлений...")
    await check_updates(context.application, target_chat_id=update.effective_chat.id, is_manual=True)
    await update.message.reply_text("Проверка завершена.")

async def cmd_list_addresses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addresses = database.get_user_addresses(update.effective_chat.id)
    if addresses:
        lines = []
        for i, (aid, district, street) in enumerate(addresses):
            lines.append(f"{i+1}. [{district}] {street}")
        await update.message.reply_text(
            f"📋 Ваши адреса ({len(addresses)}/{database.MAX_ADDRESSES}):\n\n" + "\n".join(lines)
        )
    else:
        await update.message.reply_text("У вас пока нет адресов. Нажмите «➕ Добавить адрес».")

async def cmd_remove_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addresses = database.get_user_addresses(update.effective_chat.id)
    if not addresses:
        await update.message.reply_text("У вас нет адресов для удаления.")
        return
    if not context.args:
        lines = []
        for i, (aid, district, street) in enumerate(addresses):
            lines.append(f"{i+1}. [{district}] {street}")
        await update.message.reply_text(
            "📋 Ваши адреса:\n\n" + "\n".join(lines) +
            "\n\nДля удаления: /remove <номер>"
        )
        return
    try:
        arg = context.args[0]
        if arg.isdigit() and len(arg) <= 3:
            idx = int(arg) - 1
            if 0 <= idx < len(addresses):
                aid = addresses[idx][0]
            else:
                await update.message.reply_text("⚠️ Неверный номер.")
                return
        else:
            aid = int(arg)
        if database.remove_address(update.effective_chat.id, aid):
            await update.message.reply_text("✅ Адрес удалён.")
        else:
            await update.message.reply_text("⚠️ Адрес не найден.")
    except (ValueError, IndexError):
        await update.message.reply_text("⚠️ Используйте: /remove <номер> или /remove <ID>")

async def start_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addresses = database.get_user_addresses(update.effective_chat.id)
    if not addresses:
        await update.message.reply_text("У вас нет адресов для удаления.")
        return
    keyboard = []
    for i, (aid, district, street) in enumerate(addresses):
        keyboard.append([InlineKeyboardButton(
            f"{i+1}. [{district}] {street}",
            callback_data=f"remove_{aid}"
        )])
    await update.message.reply_text(
        "Выберите адрес для удаления:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def confirm_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    aid = int(query.data.split("_")[1])
    if database.remove_address(query.message.chat_id, aid):
        await query.edit_message_text("✅ Адрес удалён.")
    else:
        await query.edit_message_text("⚠️ Адрес не найден.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in ("🔍 Проверить сейчас", "➕ Добавить адрес", "➖ Удалить адрес", "📋 Мои адреса", "❓ Справка"):
        context.user_data.pop('support_mode', None)
    if text == "🔍 Проверить сейчас": await check_now(update, context)
    elif text == "➕ Добавить адрес": await start_setup(update, context)
    elif text == "➖ Удалить адрес": await start_remove(update, context)
    elif text == "📋 Мои адреса": await cmd_list_addresses(update, context)
    elif text == "❓ Справка": await start(update, context)
    elif text == "📩 Поддержка": await start_support(update, context)
    elif context.user_data.get('support_mode'):
        await process_support(update, context)

def validate_address_input(text, field_name="Поле"):
    if not text or not text.strip():
        return False, f"{field_name} не может быть пустым. Попробуйте ещё раз."
    text = text.strip()
    if len(text) > 200:
        return False, f"{field_name} слишком длинное (макс. 200 символов). Попробуйте короче."
    if re.search(r'[a-zA-Z]', text):
        return False, f"{field_name} должно быть на русском языке без латинских букв. Попробуйте ещё раз."
    if re.search(r'[^\w\sа-яА-ЯёЁ0-9 ./,\-–()№"«»]', text):
        return False, f"{field_name} содержит недопустимые символы. Используйте только буквы, цифры и знаки препинания."
    if not re.match(r'^[а-яА-ЯёЁ0-9«"[({]', text):
        return False, f"{field_name} должно начинаться с буквы или цифры. Попробуйте ещё раз."
    return True, ""


def normalize_district(text):
    if not text: return ""
    text = text.upper().replace('Ё', 'Е')
    prefixes = ["Г.", "ПГТ", "ПОС.", "ПОСЕЛОК", "С.", "СЕЛО", "УЛУС", "РАЙОН", "Р-Н"]
    for p in prefixes: text = re.sub(rf'\b{re.escape(p)}\b', ' ', text)
    text = re.sub(r'[^А-Я0-9\s-]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def _expand_single_letter(token):
    return re.escape(token)

def build_street_pattern(core_name):
    tokens = re.split(r'([\s-])', core_name)
    pattern_parts = []
    for i, tok in enumerate(tokens):
        if i == 0 and tok == "":
            continue
        if tok in (" ", "-"):
            pattern_parts.append(r"-?\s*")
        else:
            pattern_parts.append(_expand_single_letter(tok))
    pattern = "".join(pattern_parts)
    return re.compile(r'(?:^|[\s,/])' + pattern + r'(?:\s|$|[^\wа-яё])', re.IGNORECASE)

_ADDR_REPLACE = {
    "переулок": "пер", "микрорайон": "мкр", "набережная": "наб",
    "товарищество": "ст", "километр": "км", "проспект": "пр",
    "бульвар": "б-р", "площадь": "пл", "проезд": "пр-д",
    "участок": "уч", "корпус": "корп", "улица": "ул",
    "шоссе": "ш", "тупик": "туп", "тракт": "тр",
    "бул": "б-р", "пр": "пр-д",
}

_ADDR_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in sorted(_ADDR_REPLACE, key=len, reverse=True)) + r')\b'
)


def normalize_address(text):
    if not text: return ""
    text = text.lower()
    text = text.replace('ё', 'е')
    text = re.sub(r'\s*(кв|квартира|подъезд|этаж)\s*\d+.*$', '', text)
    text = text.replace(".", " ").replace(",", " ")
    text = re.sub(r'\b(дом|д|уч|участка)\b', ' ', text)
    text = _ADDR_PATTERN.sub(lambda m: _ADDR_REPLACE[m.group(1)], text)
    text = re.sub(r'\bкорп\b', ' корп ', text)
    text = re.sub(r'[^а-я0-9\s\/\-–]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def parse_russian_date(date_str, ref_date=None):
    if not date_str: return None
    if not ref_date:
        ykt_tz = timezone(timedelta(hours=9))
        ref_date = datetime.now(ykt_tz)
    months = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
        'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
    }
    try:
        match = re.search(r'(\d+)\s*([а-яА-Я]+)', date_str)
        if not match:
            range_match = re.search(r'(\d+)\s*[–-]\s*(\d+)\s+([а-яА-Я]+)', date_str)
            if range_match: day, month_name = int(range_match.group(2)), range_match.group(3).lower()
            else: return None
        else: day, month_name = int(match.group(1)), match.group(2).lower()
        month = months.get(month_name)
        if not month: return None
        year = ref_date.year + 1 if ref_date.month == 12 and month == 1 else ref_date.year
        return datetime(year, month, day).date()
    except: return None

def parse_user_address(street):
    original_lower = street.lower()
    korpus_val = None
    mkr_val = None
    mkr_m = re.match(r'^(\d{3})\s*(?:мкр|микрорайон)?\b', original_lower)
    if mkr_m:
        mkr_val = mkr_m.group(1)
        after_mkr = original_lower[mkr_m.end():].strip()
        korpus_m = re.search(r'[кк]орп(?:ус)?\s*(\d+)', after_mkr)
        if korpus_m:
            korpus_val = korpus_m.group(1)
            after_korpus = (after_mkr[:korpus_m.start()] + ' ' + after_mkr[korpus_m.end():]).strip()
        else:
            korpus_m2 = re.search(r'\bк(\d+)', after_mkr)
            if korpus_m2:
                korpus_val = korpus_m2.group(1)
                after_korpus = (after_mkr[:korpus_m2.start()] + ' ' + after_mkr[korpus_m2.end():]).strip()
            else:
                after_korpus = after_mkr
        norm_rest = normalize_address(after_korpus) if after_korpus else ''
        norm = mkr_val + (' ' + norm_rest if norm_rest else '')
    else:
        korpus_m = re.search(r'(\d+(?:/\d+)?)\s+[кк]орп(?:ус)?\s*(\d+)', original_lower)
        if korpus_m:
            house_from_korpus = korpus_m.group(1)
            korpus_val = korpus_m.group(2)
            original_lower = original_lower[:korpus_m.start()] + ' ' + house_from_korpus + ' ' + original_lower[korpus_m.end():]
        else:
            korpus_m2 = re.search(r'(\d+(?:/\d+)?)\s+к(\d+)', original_lower)
            if korpus_m2:
                house_from_korpus = korpus_m2.group(1)
                korpus_val = korpus_m2.group(2)
                original_lower = original_lower[:korpus_m2.start()] + ' ' + house_from_korpus + ' ' + original_lower[korpus_m2.end():]
        norm = normalize_address(original_lower)
    result = {
        'street_name': norm,
        'km_value': None,
        'km_from': None,
        'km_to': None,
        'house_num': None,
        'main_house': None,
        'sub_house': None,
        'korpus': korpus_val,
        'mkr': mkr_val,
        'is_km_address': False,
    }
    km_range_match = re.search(r'(\d+)\s*-\s*(\d+)\s*км', norm)
    km_single_match = re.search(r'(\d+)\s*км', norm)
    has_tract = bool(re.search(r'\b(тр|ш|шоссе)\b', norm))
    if (km_range_match or km_single_match) and has_tract:
        result['is_km_address'] = True
        if km_range_match:
            result['km_from'] = int(km_range_match.group(1))
            result['km_to'] = int(km_range_match.group(2))
            km_str = km_range_match.group(0)
        else:
            result['km_value'] = int(km_single_match.group(1))
            km_str = km_single_match.group(0)
        km_pos = norm.find(km_str)
        after_km = norm[km_pos + len(km_str):].strip()
        before_km = norm[:km_pos].strip()
        result['street_name'] = before_km
        if after_km:
            house_match = re.match(r'^([\d/]+)', after_km)
            if house_match:
                result['house_num'] = house_match.group(1)
                parts = result['house_num'].split('/')
                result['main_house'] = int(parts[0])
                result['sub_house'] = parts[1] if len(parts) > 1 else None
    else:
        if mkr_val:
            rest = norm[len(mkr_val):].strip()
            house_match = re.search(r'^([\d/]+)', rest)
            if house_match:
                result['house_num'] = house_match.group(1)
                parts = result['house_num'].split('/')
                result['main_house'] = int(parts[0])
                result['sub_house'] = parts[1] if len(parts) > 1 else None
        else:
            house_match = re.search(r'(\d+[\w\/-]*)$', norm)
            if house_match:
                result['house_num'] = house_match.group(1).replace(" ", "")
                result['street_name'] = norm[:house_match.start()].strip()
                m = re.match(r'(\d+)(.*)', result['house_num'])
                if m:
                    result['main_house'] = int(m.group(1))
                    result['sub_house'] = m.group(2) or None
    core = re.sub(r'\b(ул|пер|пр|ш|наб|пл|пр-д|туп|б-р|тр|мкр|корп|снт|сот|днт|гсп|км)\b', '', result['street_name']).strip()
    if mkr_val:
        result['core_name'] = mkr_val
    elif core:
        result['core_name'] = core
    elif result['street_name'] and not result['street_name'].isdigit():
        result['core_name'] = result['street_name']
    else:
        result['core_name'] = None
    return result


def extract_street_houses(norm):
    tokens = norm.split()
    if not tokens:
        return '', ''
    house_start = len(tokens)
    for i, tok in enumerate(tokens):
        if re.match(r'^\d+[/–-]', tok):
            house_start = i
            break
        if re.match(r'^\d+[а-я]$', tok):
            house_start = i
            break
        if re.match(r'^\d+$', tok):
            if i == len(tokens) - 1:
                house_start = i
                break
            next_tok = tokens[i + 1]
            if re.match(r'^[\d/–-]+[а-я]?$', next_tok):
                house_start = i
                break
    street_tokens = tokens[:house_start]
    house_tokens = tokens[house_start:]
    return ' '.join(street_tokens), ' '.join(house_tokens)


def split_schedule_addresses(addr_string):
    norm = normalize_address(addr_string)
    norm = re.sub(r'(\d+)\s*[–-]\s*(?=\d)', r'\1-', norm)
    raw_parts = re.split(r',\s*(?=[А-Яа-яЁё])', addr_string)
    groups = []
    for part in raw_parts:
        part = part.strip().rstrip('.,')
        if not part:
            continue
        part_norm = normalize_address(part)
        part_norm = re.sub(r'(\d+)\s*[–-]\s*(?=\d)', r'\1-', part_norm)
        if not part_norm:
            continue
        street_name, houses = extract_street_houses(part_norm)
        km_range_m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*км', part_norm)
        km_single_m = re.search(r'(\d+)\s*км', part_norm)
        street_part = street_name
        km_value = None
        km_from = None
        km_to = None
        if km_range_m:
            km_from = int(km_range_m.group(1))
            km_to = int(km_range_m.group(2))
            street_part = street_name
        elif km_single_m:
            km_value = int(km_single_m.group(1))
            street_part = street_name
        houses_part = re.sub(r'\b\d+\s*(?:-\s*\d+)?\s*км\b', '', houses).strip()
        houses_part = re.sub(r'\b(ул|пер|пр|ш|наб|пл|пр-д|туп|б-р|тр|мкр|корп|снт|сот|днт|гсп|км)\b', '', houses_part).strip()
        groups.append({
            'street_norm': street_part,
            'km_value': km_value,
            'km_from': km_from,
            'km_to': km_to,
            'houses_part': houses_part,
            'full_norm': part_norm,
            'houses': houses,
        })
    if not groups:
        groups.append({
            'street_norm': norm,
            'km_value': None,
            'km_from': None,
            'km_to': None,
            'houses_part': '',
            'full_norm': norm,
            'houses': '',
        })
    return groups


def match_address_against_schedule(ua, norm_user_district, s, addr_groups):
    if norm_user_district not in normalize_district(s['district']):
        return False
    if ua['core_name'] is None:
        return False
    street_pattern = build_street_pattern(ua['core_name'])
    for grp in addr_groups:
        if not street_pattern.search(grp['street_norm']):
            continue
        if ua['is_km_address']:
            grp_has_km = grp['km_value'] is not None or grp['km_from'] is not None
            if grp_has_km:
                km_match = False
                if grp['km_value'] is not None and ua['km_value'] is not None:
                    if grp['km_value'] == ua['km_value']:
                        km_match = True
                if not km_match and grp['km_from'] is not None and ua['km_value'] is not None:
                    if grp['km_from'] <= ua['km_value'] <= grp['km_to']:
                        km_match = True
                if km_match:
                    if ua['main_house'] is not None:
                        if re.search(r'\b' + re.escape(ua['house_num']) + r'\b', grp['houses']):
                            return True
                        elif not re.search(r'\b\d+\b', grp['houses_part']):
                            return True
                    else:
                        return True
            else:
                if "частич" in grp['full_norm'] or "полн" in grp['full_norm']:
                    return True
        else:
            match_house = ua['main_house']
            match_num = ua['house_num']
            if ua['mkr'] and ua['korpus'] and match_house is None:
                match_house = int(ua['korpus'])
                match_num = ua['korpus']
            if match_house is not None:
                house_direct = re.search(r'\b' + re.escape(match_num) + r'\b(?!/)', grp['houses'])
                if house_direct:
                    if ua['korpus'] is not None and not ua['mkr']:
                        korpus_pattern = re.escape(match_num) + r'\s*корп\s*' + re.escape(ua['korpus'])
                        if re.search(korpus_pattern, grp['houses']):
                            return True
                        elif not re.search(re.escape(match_num) + r'\s*корп', grp['houses']):
                            return True
                    else:
                        return True
                else:
                    range_matches = re.finditer(r'(\d+)\s*[–-]\s*(\d+)', grp['houses'])
                    for rm in range_matches:
                        try:
                            lo, hi = int(rm.group(1)), int(rm.group(2))
                            if lo <= match_house <= hi:
                                return True
                        except: pass
                if ua['sub_house'] is None:
                    sub_match = re.search(r'\b' + str(match_house) + r'/\S+', grp['full_norm'])
                    if sub_match:
                        if ua['korpus'] is not None and not ua['mkr']:
                            korpus_after = re.search(r'\b' + str(match_house) + r'/\S+\s*корп\s*' + re.escape(ua['korpus']), grp['full_norm'])
                            if korpus_after:
                                return True
                            elif not re.search(r'\b' + str(match_house) + r'/\S+\s*корп', grp['full_norm']):
                                return True
                        else:
                            return True
                if "частич" in grp['full_norm'] or "полн" in grp['full_norm']:
                    return True
                elif not re.search(r'\d', grp['houses']):
                    return True
            else:
                return True
    return False


async def check_updates(application, target_chat_id=None, force_date=None, is_manual=False):
    logging.info(f"Checking updates (force_date={force_date}, is_manual={is_manual})...")
    schedules = await asyncio.to_thread(scraper.get_all_recent_schedules)

    ykt_tz = timezone(timedelta(hours=9))
    now_ykt = datetime.now(ykt_tz)
    today = now_ykt.date()

    if target_chat_id:
        all_addresses = [(target_chat_id, aid, dist, street) for aid, dist, street in database.get_user_addresses(target_chat_id)]
    else:
        all_addresses = database.get_all_addresses()

    from collections import defaultdict
    user_addresses = defaultdict(list)
    for chat_id, aid, dist, street in all_addresses:
        user_addresses[chat_id].append((aid, dist, street))

    for chat_id, addresses in user_addresses.items():
        if database.is_blocked(chat_id):
            continue
        all_matches = []

        for s in schedules:
            s_date = parse_russian_date(s['date'], ref_date=now_ykt)
            if not s_date:
                continue
            if force_date:
                if s_date != force_date:
                    continue
            else:
                if s_date < today:
                    continue

            addr_groups = split_schedule_addresses(s['addresses'])

            for aid, district, street in addresses:
                try:
                    ua = parse_user_address(street)
                except Exception as e:
                    logging.error(f"Failed to parse address '{street}' (chat_id={chat_id}): {e}")
                    continue
                norm_user_district = normalize_district(district)
                if match_address_against_schedule(ua, norm_user_district, s, addr_groups):
                    s_hash = hashlib.md5(f"{s['date']}{s['time']}{s['addresses']}{s['reason']}".encode()).hexdigest()
                    if is_manual:
                        all_matches.append((s, street, None))
                    elif not database.is_notified(chat_id, aid, s_hash):
                        all_matches.append((s, street, s_hash))

        if all_matches:
            # Группируем записи по адресу
            from collections import defaultdict
            by_address = defaultdict(list)
            for m, street, s_hash in all_matches:
                by_address[street].append((m, s_hash))

            msg = "⚠️ *Внимание! Обнаружены плановые работы:*\n\n"
            seen_hashes = set()
            for street, entries in by_address.items():
                for m, s_hash in entries:
                    if s_hash and s_hash in seen_hashes:
                        continue
                    if s_hash:
                        seen_hashes.add(s_hash)
                    msg += f"📍 *Адрес:* {street}\n📅 *Дата:* {m['date']}\n🕒 *Время:* {m['time']}\n🏠 *Где:* {m['addresses']}\n🛠 *Причина:* {m['reason']}\n\n"
                    if s_hash:
                        for aid, dist, st in addresses:
                            database.mark_as_notified(chat_id, aid, s_hash)

            try:
                await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
            except Exception as e:
                err = str(e).lower()
                if "blocked" in err or "deactivated" in err:
                    try:
                        database.set_blocked(chat_id)
                    except Exception:
                        pass
                logging.error(f"Error sending message to {chat_id}: {e}")
            for aid, district, street in addresses:
                database.log_request(chat_id, f"Улица: {street}", True)
        elif target_chat_id:
            try:
                await application.bot.send_message(chat_id=chat_id, text="✅ Работ не найдено.")
            except Exception as e:
                err = str(e).lower()
                if "blocked" in err or "deactivated" in err:
                    try:
                        database.set_blocked(chat_id)
                    except Exception:
                        pass
                logging.error(f"Error sending to {chat_id}: {e}")
            for aid, district, street in addresses:
                database.log_request(chat_id, f"Улица: {street}", False)

async def scheduler_task(application):
    ykt_tz = timezone(timedelta(hours=9))
    while True:
        now_ykt = datetime.now(ykt_tz)
        today = now_ykt.date()
        tomorrow = today + timedelta(days=1)

        # Define target times in YKT
        t9 = now_ykt.replace(hour=9, minute=0, second=0, microsecond=0)
        t21 = now_ykt.replace(hour=21, minute=0, second=0, microsecond=0)

        # Проверяем на сегодня, если 09:00 ещё не прошло
        if now_ykt < t9:
            wait_seconds = (t9 - now_ykt).total_seconds()
            logging.info(f"Next run at {t9} (YKT), checking today. Waiting {wait_seconds}s")
            await asyncio.sleep(wait_seconds)
            try:
                await check_updates(application, force_date=today)
            except Exception as e:
                logging.error(f"Scheduler check (today) failed: {e}", exc_info=True)

        # Проверяем на завтра в 09:00 (если 09:00 уже прошло сегодня)
        # или в 21:00 (если между 09:00 и 21:00)
        if now_ykt < t21:
            wait_seconds = (t21 - now_ykt).total_seconds()
            logging.info(f"Next run at {t21} (YKT), checking tomorrow. Waiting {wait_seconds}s")
            await asyncio.sleep(wait_seconds)
            try:
                await check_updates(application, force_date=tomorrow)
            except Exception as e:
                logging.error(f"Scheduler check (tomorrow evening) failed: {e}", exc_info=True)

        # Если оба времени прошли, ждём до завтра 09:00
        next_t9 = t9 + timedelta(days=1)
        wait_seconds = (next_t9 - datetime.now(ykt_tz)).total_seconds()
        logging.info(f"Next run at {next_t9} (YKT), checking tomorrow. Waiting {wait_seconds}s")
        await asyncio.sleep(wait_seconds)
        try:
            await check_updates(application, force_date=tomorrow)
        except Exception as e:
            logging.error(f"Scheduler check (next morning) failed: {e}", exc_info=True)

        # Small sleep to prevent immediate re-triggering
        await asyncio.sleep(60)

async def post_init(application):
    await application.bot.set_my_commands([
        ("start", "Меню"),
        ("add", "Добавить адрес"),
        ("remove", "Удалить адрес"),
        ("list", "Мои адреса"),
        ("check", "Проверить сейчас"),
    ])

if __name__ == '__main__':
    database.init_db()
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('➕ Добавить адрес'), start_setup)],
        states={
            SET_STREET: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_street_first)],
            CONFIRM_YAKUTSK: [CallbackQueryHandler(confirm_yakutsk)],
            SET_DISTRICT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_district_after)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', start))
    application.add_handler(CommandHandler('status', cmd_list_addresses))
    application.add_handler(CommandHandler('list', cmd_list_addresses))
    application.add_handler(CommandHandler('add', start_setup))
    application.add_handler(CommandHandler('remove', cmd_remove_address))
    application.add_handler(CommandHandler('check', check_now))
    application.add_handler(CallbackQueryHandler(confirm_remove, pattern=r'^remove_'))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(
        filters.Chat(int(ADMIN_TG_ID)) & filters.REPLY & filters.TEXT & ~filters.COMMAND,
        admin_reply
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    loop = asyncio.get_event_loop()
    loop.create_task(scheduler_task(application))
    application.run_polling()
