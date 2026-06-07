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
        [KeyboardButton("📍 Настроить адрес")],
        [KeyboardButton("⚙️ Мои настройки"), KeyboardButton("❓ Справка")],
        [KeyboardButton("📩 Поддержка")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для уведомления о плановых отключениях электроэнергии Якутскэнерго.\n\n"
        "Чтобы начать получать уведомления, нажмите кнопку «📍 Настроить адрес».\n\n"
        "Вы также можете проверить текущие настройки через «⚙️ Мои настройки» или запустить мгновенный поиск кнопкой «🔍 Проверить сейчас».",
        reply_markup=get_main_keyboard()
    )

async def start_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Давайте настроим ваш адрес.\n\n"
        "Шаг 1: Введите вашу улицу и номер дома.\n"
        "Примеры: Лермонтова 45, переулок Сединский, Вилюйский тракт 4 км"
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
        database.save_user_preference(query.message.chat_id, district=district, street=street)
        await query.edit_message_text(
            f"✅ Готово! Адрес сохранен.\n"
            f"Район: {district}\n"
            f"Улица: {street}\n\n"
            "Теперь я буду присылать вам уведомления в 9:00 или в 21:00 по Якутскому времени."
        )
        return ConversationHandler.END
    else:
        await query.edit_message_text(
            f"Улица: {street}\n\n"
            "Принято. Теперь введите название вашего района (улуса).\n"
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

    database.save_user_preference(update.effective_chat.id, district=district, street=street)
    await update.message.reply_text(
        f"✅ Готово! Адрес сохранен.\n"
        f"Район: {district}\n"
        f"Улица: {street}\n\n"
         "Теперь я буду присылать вам уведомления о плановых работах в 9:00 или в 21:00 по Якутску.",
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
    admin_text = f"💬 Поддержка от {user_info} (ID: {chat_id}):\n\n{text}"

    try:
        await context.bot.send_message(chat_id=int(ADMIN_TG_ID), text=admin_text)
    except Exception as e:
        logging.error(f"Support forward error: {e}")
        await update.message.reply_text("⚠️ Ошибка отправки. Попробуйте позже.", reply_markup=get_main_keyboard())
        return

    await update.message.reply_text(
        "✅ Сообщение отправлено. Ответ придёт сюда.",
        reply_markup=get_main_keyboard()
    )

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Ответьте на сообщение пользователя (Reply).")
        return
    orig = update.message.reply_to_message.text or ""
    m = re.search(r"ID:\s*(\d+)", orig)
    if not m:
        await update.message.reply_text("⚠️ Не найден ID пользователя в сообщении.")
        return
    target_chat_id = int(m.group(1))
    reply_text = " ".join(context.args)
    if not reply_text:
        await update.message.reply_text("⚠️ Укажите текст: /reply <сообщение>")
        return
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
    pref = database.get_user_preference(update.effective_chat.id)
    if pref:
        district, street = pref
        await update.message.reply_text(f"Ваши настройки:\nРайон: {district or 'Не установлен'}\nУлица: {street or 'Не установлена'}")
    else:
        await update.message.reply_text("Вы еще не установили настройки. Нажмите кнопку «📍 Настроить адрес».")

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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in ("🔍 Проверить сейчас", "⚙️ Мои настройки", "❓ Справка", "📍 Настроить адрес"):
        context.user_data.pop('support_mode', None)
    if text == "🔍 Проверить сейчас": await check_now(update, context)
    elif text == "⚙️ Мои настройки": await status(update, context)
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
    text = text.upper()
    prefixes = ["Г.", "ПГТ", "ПОС.", "ПОСЕЛОК", "С.", "СЕЛО", "УЛУС", "РАЙОН", "Р-Н"]
    for p in prefixes: text = re.sub(rf'\b{re.escape(p)}\b', ' ', text)
    text = re.sub(r'[^А-Я0-9\s-]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def _expand_single_letter(token):
    if re.fullmatch(r'[а-яёв]', token):
        return r"[а-яёв]{2,}"
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

def normalize_address(text):
    if not text: return ""
    text = text.lower()
    text = re.sub(r'\s*(кв|квартира|подъезд|этаж)\s*\d+.*$', '', text)
    replacements = {
        "улица": "ул", "переулок": "пер", "проспект": "пр", "бульвар": "б-р",
        "бул": "б-р", "шоссе": "ш", "проезд": "пр-д", "пр": "пр-д",
        "тупик": "туп", "набережная": "наб", "площадь": "пл", "тракт": "тр",
        "микрорайон": "мкр", "корпус": "корп", "километр": "км", "участок": "уч", "товарищество": "ст",
    }
    for old, new in replacements.items(): text = text.replace(old, new)
    text = text.replace(".", " ").replace(",", " ")
    text = re.sub(r'\b(дом|д|уч|участка)\b', ' ', text)
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

async def check_updates(application, target_chat_id=None, force_date=None, is_manual=False):
    logging.info(f"Checking updates (force_date={force_date}, is_manual={is_manual})...")
    schedules = await asyncio.to_thread(scraper.get_all_recent_schedules)
    
    # Use Yakutsk time (UTC+9)
    ykt_tz = timezone(timedelta(hours=9))
    now_ykt = datetime.now(ykt_tz)
    today = now_ykt.date()
    
    users = [(target_chat_id, *database.get_user_preference(target_chat_id))] if target_chat_id else database.get_all_users()
    
    for chat_id, user_district, street in users:
        if not user_district or not street: continue
        matches = []
        norm_user_input = normalize_address(street)
        norm_user_district = normalize_district(user_district)

        house_match = re.search(r'(\d+[\w\/-]*(\s*км)?)$', norm_user_input)
        if house_match:
            house_num = house_match.group(1).replace(" ", "")
            street_name = norm_user_input[:house_match.start()].strip()
        else:
            house_num, street_name = None, norm_user_input

        def parse_house(num):
            if not num: return None, None
            m = re.match(r'^(\d+)(?:/(.+))?$', num)
            if m:
                return int(m.group(1)), m.group(2)
            digits = re.sub(r'\D', '', num)
            return (int(digits) if digits else None), None

        main_house, sub_house = parse_house(house_num)

        core_name = re.sub(r'\b(ул|пер|пр|ш|наб|пл|пр-д|туп|б-р|тр|мкр|корп|снт|сот|днт|гсп|км)\b', '', street_name).strip()
        if not core_name: core_name = street_name
        street_pattern = build_street_pattern(core_name)

        for s in schedules:
            s_date = parse_russian_date(s['date'], ref_date=now_ykt)
            if not s_date: continue

            # Filtering by date
            if force_date:
                if s_date != force_date: continue
            else:
                if s_date < today: continue

            if norm_user_district in normalize_district(s['district']):
                norm_schedule_addr = normalize_address(s['addresses'])
                if street_pattern.search(norm_schedule_addr):
                    match_found = False
                    if house_num and main_house is not None:
                        # 1. Direct match — full house number including sub-building
                        if re.search(r'\b' + re.escape(house_num) + r'\b', norm_schedule_addr):
                            match_found = True
                        else:
                            # 2. Range match — compare only main house number
                            range_match = re.search(r'(\d+)\s*[–-]\s*(\d+)', norm_schedule_addr)
                            if range_match:
                                try:
                                    if int(range_match.group(1)) <= main_house <= int(range_match.group(2)):
                                        match_found = True
                                except: pass

                            # 3. Sub-building match — e.g. user has "9" and schedule has "9/3а"
                            if not match_found and sub_house is None:
                                sub_match = re.search(r'\b' + str(main_house) + r'/\S+', norm_schedule_addr)
                                if sub_match:
                                    match_found = True

                            # 4. General street match (no house numbers or partial/full)
                            if not match_found:
                                if "частич" in norm_schedule_addr or "полн" in norm_schedule_addr:
                                    match_found = True
                                elif not re.search(r'\d', norm_schedule_addr):
                                    match_found = True
                    else:
                        match_found = True

                    if match_found:
                        # Duplicate prevention
                        s_hash = hashlib.md5(f"{s['date']}{s['time']}{s['addresses']}{s['reason']}".encode()).hexdigest()
                        if is_manual:
                            matches.append((s, None)) # No marking for manual
                        elif not database.is_notified(chat_id, s_hash):
                            matches.append((s, s_hash))
        
        if matches:
            msg = "⚠️ *Внимание! Обнаружены плановые работы:*\n\n"
            for m, s_hash in matches:
                msg += f"📅 *Дата:* {m['date']}\n🕒 *Время:* {m['time']}\n📍 *Адреса:* {m['addresses']}\n🛠 *Причина:* {m['reason']}\n\n"
                if s_hash: database.mark_as_notified(chat_id, s_hash)
            
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
            database.log_request(chat_id, f"Улица: {street}", False)

async def scheduler_task(application):
    ykt_tz = timezone(timedelta(hours=9))
    while True:
        now_ykt = datetime.now(ykt_tz)
        
        # Define target times in YKT
        t9 = now_ykt.replace(hour=9, minute=0, second=0, microsecond=0)
        t21 = now_ykt.replace(hour=21, minute=0, second=0, microsecond=0)
        
        targets = []
        if now_ykt < t9:
            targets.append((t9, "today"))
        if now_ykt < t21:
            targets.append((t21, "tomorrow"))
        
        # If both passed today, next is 9:00 tomorrow
        if not targets:
            next_t9 = t9 + timedelta(days=1)
            targets.append((next_t9, "today"))
            
        # Sort targets to find the closest one
        targets.sort()
        target_time, mode = targets[0]
        
        wait_seconds = (target_time - now_ykt).total_seconds()
        logging.info(f"Next run at {target_time} (YKT), mode={mode}. Waiting {wait_seconds}s")
        
        await asyncio.sleep(wait_seconds)
        
        # Refresh current time after sleep
        now_ykt = datetime.now(ykt_tz)
        today = now_ykt.date()
        tomorrow = today + timedelta(days=1)
        
        target_date = today if mode == "today" else tomorrow
        await check_updates(application, force_date=target_date)
        
        # Small sleep to prevent immediate re-triggering if sleep was slightly short
        await asyncio.sleep(60)

async def post_init(application):
    await application.bot.set_my_commands([("start", "Меню"), ("status", "Настройки"), ("check", "Проверить")])

if __name__ == '__main__':
    database.init_db()
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('📍 Настроить адрес'), start_setup)],
        states={
            SET_STREET: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_street_first)],
            CONFIRM_YAKUTSK: [CallbackQueryHandler(confirm_yakutsk)],
            SET_DISTRICT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_district_after)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', start))
    application.add_handler(CommandHandler('status', status))
    application.add_handler(CommandHandler('check', check_now))
    application.add_handler(CommandHandler('reply', admin_reply, filters=filters.Chat(int(ADMIN_TG_ID))))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    loop = asyncio.get_event_loop()
    loop.create_task(scheduler_task(application))
    application.run_polling()
