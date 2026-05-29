import logging
import asyncio
import re
import os
from dotenv import load_dotenv
from datetime import datetime, time, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    ContextTypes, 
    MessageHandler, 
    filters, 
    ConversationHandler
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

# Conversation states
SET_DISTRICT, SET_STREET = range(2)

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🔍 Проверить сейчас")],
        [KeyboardButton("📍 Настроить адрес")],
        [KeyboardButton("⚙️ Мои настройки"), KeyboardButton("❓ Справка")]
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
    pref = database.get_user_preference(update.effective_chat.id)
    if pref and pref[0] and pref[1]:
        await update.message.reply_text(
            f"Ваш текущий адрес: {pref[0]}, {pref[1]}.\n"
            "Давайте обновим его.\n\n"
            "Шаг 1: Введите название вашего района (например: Якутск, Намский, пгт Жатай)."
        )
    else:
        await update.message.reply_text(
            "Давайте настроим ваш адрес. \n\n"
            "Шаг 1: Введите название вашего района.\n"
            "Примеры: Якутск, Хангаласский, Намский, Мирнинский, пгт Жатай"
        )
    return SET_DISTRICT

async def process_district(update: Update, context: ContextTypes.DEFAULT_TYPE):
    district = normalize_district(update.message.text)
    context.user_data['temp_district'] = district
    await update.message.reply_text(
        f"Район «{district}» принят.\n\n"
        "Шаг 2: Теперь введите вашу улицу и номер дома.\n"
        "Примеры: Лермонтова 45, Лесная, переулок Сединский, Вилюйский тракт 4 км"
    )
    return SET_STREET

async def process_street(update: Update, context: ContextTypes.DEFAULT_TYPE):
    street = update.message.text
    district = context.user_data.get('temp_district')
    database.save_user_preference(update.effective_chat.id, district=district, street=street)
    await update.message.reply_text(
        f"✅ Готово! Адрес сохранен.\n"
        f"Район: {district}\n"
        f"Улица: {street}\n\n"
        "Теперь я буду присылать вам уведомления о плановых работах в 21:00.",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Настройка отменена.", reply_markup=get_main_keyboard())
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
    await check_updates(context.application, target_chat_id=update.effective_chat.id)
    await update.message.reply_text("Проверка завершена.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🔍 Проверить сейчас": await check_now(update, context)
    elif text == "⚙️ Мои настройки": await status(update, context)
    elif text == "❓ Справка": await start(update, context)

def normalize_district(text):
    if not text: return ""
    text = text.upper()
    prefixes = ["Г.", "ПГТ", "ПОС.", "ПОСЕЛОК", "С.", "СЕЛО", "УЛУС", "РАЙОН", "Р-Н"]
    for p in prefixes: text = re.sub(rf'\b{re.escape(p)}\b', ' ', text)
    text = re.sub(r'[^А-Я0-9\s-]', '', text)
    text = text.replace("-", " ")
    return re.sub(r'\s+', ' ', text).strip()

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
    text = re.sub(r'[^а-я0-9\s\/-]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def parse_russian_date(date_str):
    if not date_str: return None
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
        now = datetime.now()
        year = now.year + 1 if now.month == 12 and month == 1 else now.year
        return datetime(year, month, day).date()
    except: return None

async def check_updates(application, target_chat_id=None):
    logging.info("Checking updates...")
    schedules = await asyncio.to_thread(scraper.get_all_recent_schedules)
    today = datetime.now().date()
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

        core_name = re.sub(r'\b(ул|пер|пр|ш|наб|пл|пр-д|туп|б-р|тр|мкр|корп|снт|сот|днт|гсп|км)\b', '', street_name).strip()
        if not core_name: core_name = street_name

        for s in schedules:
            if parse_russian_date(s['date']) and parse_russian_date(s['date']) < today: continue
            if norm_user_district in normalize_district(s['district']):
                norm_schedule_addr = normalize_address(s['addresses'])
                if core_name in norm_schedule_addr:
                    if house_num:
                        if re.search(r'\b' + re.escape(house_num) + r'\b', norm_schedule_addr): matches.append(s)
                        range_match = re.search(r'(\d+)\s*[–-]\s*(\d+)', norm_schedule_addr)
                        if range_match:
                            try:
                                clean_h = int(re.sub(r'\D', '', house_num))
                                if int(range_match.group(1)) <= clean_h <= int(range_match.group(2)): matches.append(s)
                            except: pass
                    else: matches.append(s)
        
        unique_matches = []
        seen = set()
        for m in matches:
            key = (m['date'], m['time'], m['addresses'])
            if key not in seen: unique_matches.append(m); seen.add(key)
        
        if unique_matches:
            msg = "⚠️ *Внимание! Обнаружены плановые работы:*\n\n"
            for m in unique_matches: msg += f"📅 *Дата:* {m['date']}\n🕒 *Время:* {m['time']}\n📍 *Адреса:* {m['addresses']}\n🛠 *Причина:* {m['reason']}\n\n"
            try: await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
            except: pass
        elif target_chat_id: await application.bot.send_message(chat_id=chat_id, text="✅ Работ не найдено.")

async def scheduler_task(application):
    while True:
        now = datetime.now()
        target = datetime.combine(now.date(), time(21, 0))
        if now >= target: target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        await check_updates(application)

async def post_init(application):
    await application.bot.set_my_commands([("start", "Меню"), ("status", "Настройки"), ("check", "Проверить")])

if __name__ == '__main__':
    database.init_db()
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('📍 Настроить адрес'), start_setup)],
        states={
            SET_DISTRICT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_district)],
            SET_STREET: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_street)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('menu', start))
    application.add_handler(CommandHandler('status', status))
    application.add_handler(CommandHandler('check', check_now))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    loop = asyncio.get_event_loop()
    loop.create_task(scheduler_task(application))
    application.run_polling()
