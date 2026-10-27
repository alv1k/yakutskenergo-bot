import os
import time
import requests
from dotenv import load_dotenv
import streamlit as st
import pandas as pd
import sqlite3
import hashlib
from datetime import datetime, timedelta

# Local application modules
import scraper
from bot import (
    parse_user_address,
    parse_user_address_v1,
    normalize_district,
    split_schedule_addresses,
    split_schedule_addresses_v1,
    match_address_against_schedule,
    match_address_against_schedule_v1,
    build_street_pattern,
    normalize_address,
)
import address_parser_v2 as parser_v2

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot_database.db')
PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "b236e2387136965e45af21ba55b037c0fd413acf727a98affd5d339ab201fbfe")

MONTHS_RU = {
    1: 'янв', 2: 'фев', 3: 'мар', 4: 'апр', 5: 'май', 6: 'июн',
    7: 'июл', 8: 'авг', 9: 'сен', 10: 'окт', 11: 'ноя', 12: 'дек'
}

def format_ru_date(val, include_time=False):
    if not val or pd.isna(val):
        return '-'
    dt = pd.to_datetime(val) + timedelta(hours=9)
    base = f"{dt.day} {MONTHS_RU.get(dt.month, '')} {dt.year}"
    if include_time:
        return f"{base}, {dt.strftime('%H:%M')}"
    return base

def check_password():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if not st.session_state.logged_in:
        pwd = st.text_input("Введите пароль:", type="password", key="login_pwd")
        submit = st.button("Войти", key="login_btn")
        if pwd and (submit or st.session_state.get("login_pwd_submitted", False)):
            if hashlib.sha256(pwd.encode()).hexdigest() == PASSWORD_HASH:
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("Неверный пароль")
        elif submit and not pwd:
            st.warning("Введите пароль")
        return False
    return True

def get_conn():
    return sqlite3.connect(DB_NAME)

@st.cache_data(ttl=60)
def load_users():
    conn = get_conn()
    df = pd.read_sql('''
        SELECT 
            u.chat_id, 
            u.bot_blocked, 
            u.created_at,
            COALESCE(a.address_count, 0) as address_count,
            COALESCE(sn.notif_count, 0) as notif_count,
            COALESCE(r.request_count, 0) as request_count
        FROM users u
        LEFT JOIN (
            SELECT chat_id, COUNT(*) as address_count 
            FROM addresses 
            GROUP BY chat_id
        ) a ON a.chat_id = u.chat_id
        LEFT JOIN (
            SELECT chat_id, COUNT(*) as notif_count 
            FROM sent_notifications 
            GROUP BY chat_id
        ) sn ON sn.chat_id = u.chat_id
        LEFT JOIN (
            SELECT chat_id, COUNT(*) as request_count 
            FROM request_logs 
            GROUP BY chat_id
        ) r ON r.chat_id = u.chat_id
        ORDER BY u.created_at DESC
    ''', conn)
    conn.close()
    return df

@st.cache_data(ttl=60)
def load_addresses():
    conn = get_conn()
    df = pd.read_sql('''
        SELECT a.id, a.chat_id, a.district, a.street, a.created_at
        FROM addresses a
        ORDER BY a.district, a.street
    ''', conn)
    conn.close()
    return df

@st.cache_data(ttl=60)
def load_notifications():
    conn = get_conn()
    df = pd.read_sql('''
        SELECT sn.chat_id, sn.address_id, sn.schedule_hash, sn.sent_at,
               COALESCE(NULLIF(sn.message_text, ''), '— (текст в БД не логировался)') as message_text,
               a.district, a.street
        FROM sent_notifications sn
        LEFT JOIN addresses a ON a.id = sn.address_id
        ORDER BY sn.sent_at DESC
    ''', conn)
    conn.close()
    return df

@st.cache_data(ttl=60)
def load_request_logs():
    conn = get_conn()
    df = pd.read_sql('''
        SELECT r.id, r.timestamp, r.chat_id, r.query_details, r.found_status
        FROM request_logs r
        ORDER BY r.timestamp DESC
    ''', conn)
    conn.close()
    return df

@st.cache_data(ttl=60)
def load_tickets():
    conn = get_conn()
    df = pd.read_sql('''
        SELECT id, chat_id, user_message, user_username, admin_reply,
               replied_at, created_at
        FROM support_tickets
        ORDER BY created_at DESC
    ''', conn)
    conn.close()
    return df

def reply_to_ticket(ticket_id, chat_id, reply_text):
    if BOT_TOKEN and chat_id:
        tg_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        text_to_send = f"📩 *Ответ поддержки:*\n\n{reply_text}"
        try:
            resp = requests.post(tg_url, json={
                "chat_id": chat_id,
                "text": text_to_send,
                "parse_mode": "Markdown"
            }, timeout=10)
            if not resp.ok:
                st.error(f"Ошибка отправки сообщения в Telegram: {resp.text}")
                return False
        except Exception as e:
            st.error(f"Не удалось связаться с Telegram API: {e}")
            return False

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE support_tickets
        SET admin_reply = ?, replied_at = datetime('now')
        WHERE id = ?
    ''', (reply_text, ticket_id))
    conn.commit()
    conn.close()
    st.cache_data.clear()
    return True

@st.cache_data(ttl=60)
def check_proxy_and_site_status():
    url = scraper.NEWS_LIST_URL
    proxies = scraper.PROXIES
    headers = scraper.HEADERS
    t0 = time.time()
    try:
        resp = requests.get(url, proxies=proxies, headers=headers, verify=False, timeout=8)
        latency = int((time.time() - t0) * 1000)
        return {
            "proxy_ok": True,
            "site_ok": resp.status_code == 200,
            "status_code": resp.status_code,
            "latency_ms": latency,
            "error": None
        }
    except Exception as e:
        latency = int((time.time() - t0) * 1000)
        return {
            "proxy_ok": False,
            "site_ok": False,
            "status_code": 0,
            "latency_ms": latency,
            "error": str(e)
        }

@st.cache_data(ttl=300)
def load_schedules():
    try:
        return scraper.get_all_recent_schedules()
    except Exception as e:
        st.error(f"Ошибка загрузки графика: {e}")
        return []

st.set_page_config(page_title="Якутскэнерго — Дашборд", layout="wide", page_icon="favicon.png")

if not check_password():
    st.stop()

# --- Боковая панель: Мониторинг прокси, бэкап и управление ---
with st.sidebar:
    st.header("⚡ Состояние системы")
    status_info = check_proxy_and_site_status()
    
    if status_info["site_ok"]:
        st.success(f"🟢 **Сайт Якутскэнерго:** {status_info['status_code']} OK ({status_info['latency_ms']} мс)")
    elif status_info["proxy_ok"]:
        st.warning(f"🟡 **Сайт вернул код:** {status_info['status_code']} ({status_info['latency_ms']} мс)")
    else:
        st.error(f"🔴 **Ошибка подключения к сайту/прокси**\n`{status_info['error']}`")

    # Состояние кеша скрейпера
    cache_entries = len(scraper._cache['data']) if scraper._cache['data'] else 0
    cache_age_sec = int(time.time() - scraper._cache['timestamp']) if scraper._cache['timestamp'] else None
    
    st.caption(f"**В кеше:** {cache_entries} записей")
    if cache_age_sec is not None and cache_age_sec < 86400:
        st.caption(f"**Возраст кеша:** {cache_age_sec // 60} мин {cache_age_sec % 60} сек")

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("🔄 Связь", help="Перепроверить доступность сайта и прокси"):
            check_proxy_and_site_status.clear()
            st.rerun()
    with col_btn2:
        if st.button("⚡ Сброс кеша", help="Сбросить кеш и спарсить свежий график с сайта"):
            scraper._cache['data'] = None
            scraper._cache['timestamp'] = 0
            load_schedules.clear()
            st.rerun()

    st.divider()
    st.subheader("📦 База данных")
    if os.path.exists(DB_NAME):
        with open(DB_NAME, "rb") as f:
            db_bytes = f.read()
        st.download_button(
            label="💾 Скачать бэкап БД",
            data=db_bytes,
            file_name=f"bot_database_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
            mime="application/x-sqlite3",
            width='stretch'
        )

    st.divider()
    if st.button("🚪 Выйти из системы", width='stretch'):
        st.session_state.logged_in = False
        st.rerun()

st.title("Якутскэнерго — панель управления ботом")

df_users = load_users()
df_addresses = load_addresses()
df_notifications = load_notifications()
df_logs = load_request_logs()
df_tickets = load_tickets()

tab_overview, tab_schedule, tab_matcher, tab_users, tab_addresses, tab_notif, tab_tickets, tab_logs = \
    st.tabs([
        "Обзор", 
        "Текущий график", 
        "Песочница адресов", 
        "Пользователи", 
        "Адреса", 
        "Уведомления", 
        "Тикеты", 
        "Лог запросов"
    ])

with tab_overview:
    col1, col2, col3, col4 = st.columns(4)
    total_users = len(df_users)
    active_users = len(df_users[df_users['bot_blocked'] == 0])
    total_addresses = len(df_addresses)
    total_notif = len(df_notifications)
    open_tickets = len(df_tickets[df_tickets['admin_reply'].isna()])

    col1.metric("Всего пользователей", total_users, f"{active_users} активных")
    col2.metric("Адресов", total_addresses)
    col3.metric("Уведомлений отправлено", total_notif)
    col4.metric("Открытых тикетов", open_tickets)

    st.subheader("Уведомления по дням")
    if not df_notifications.empty:
        df_notif_daily = df_notifications.copy()
        df_notif_daily['date'] = pd.to_datetime(pd.to_datetime(df_notif_daily['sent_at']).dt.date)
        notif_by_day = df_notif_daily.groupby('date').size().reset_index(name='Уведомления')
        st.bar_chart(notif_by_day, x='date', y='Уведомления', height=250)
    else:
        st.info("Нет данных")

    st.subheader("Запросы по дням")
    if not df_logs.empty:
        df_logs_daily = df_logs.copy()
        df_logs_daily['date'] = pd.to_datetime(pd.to_datetime(df_logs_daily['timestamp']).dt.date)
        logs_by_day = df_logs_daily.groupby(['date', 'found_status']).size().reset_index(name='count')
        pivot = logs_by_day.pivot(index='date', columns='found_status', values='count').fillna(0).reset_index()
        cols_map = {0: 'Не найдено', 1: 'Найдено'}
        pivot = pivot.rename(columns=cols_map)
        y_cols = [c for c in ['Не найдено', 'Найдено'] if c in pivot.columns]
        st.bar_chart(pivot, x='date', y=y_cols, height=250)
    else:
        st.info("Нет данных")

    st.subheader("Топ районов")
    district_counts = df_addresses['district'].value_counts().head(10).reset_index()
    district_counts.columns = ['Район', 'Количество']
    st.bar_chart(district_counts, x='Район', y='Количество', height=250)

# --- ВКЛАДКА: ТЕКУЩИЙ ГРАФИК ---
with tab_schedule:
    schedules = load_schedules()
    st.subheader(f"Текущий график ремонтных работ (в памяти: {len(schedules)})")
    
    if not schedules:
        st.warning("В текущий момент список ремонтных работ пуст или не удалось загрузить данные с сайта.")
        if st.button("Попробовать загрузить снова"):
            load_schedules.clear()
            st.rerun()
    else:
        df_sched = pd.DataFrame(schedules)
        
        col_sc1, col_sc2, col_sc3 = st.columns([1, 1, 2])
        with col_sc1:
            all_districts = sorted(df_sched['district'].unique().tolist())
            selected_dist = st.selectbox("Фильтр по району:", ["Все"] + all_districts, key="sched_dist_filter")
        with col_sc2:
            all_dates = sorted(df_sched['date'].dropna().unique().tolist())
            selected_date = st.selectbox("Фильтр по дате:", ["Все"] + all_dates, key="sched_date_filter")
        with col_sc3:
            sched_search = st.text_input("Поиск по улице, номеру или причине:", key="sched_search_box")
        
        filtered_sched = df_sched.copy()
        if selected_dist != "Все":
            filtered_sched = filtered_sched[filtered_sched['district'] == selected_dist]
        if selected_date != "Все":
            filtered_sched = filtered_sched[filtered_sched['date'] == selected_date]
        if sched_search:
            s_mask = (
                filtered_sched['addresses'].str.contains(sched_search, case=False, na=False) |
                filtered_sched['reason'].str.contains(sched_search, case=False, na=False) |
                filtered_sched['time'].str.contains(sched_search, case=False, na=False)
            )
            filtered_sched = filtered_sched[s_mask]
            
        st.caption(f"Отображается: **{len(filtered_sched)}** из {len(df_sched)} записей")
        
        display_sched = filtered_sched.rename(columns={
            'district': 'Район',
            'date': 'Дата',
            'time': 'Время',
            'addresses': 'Адреса',
            'reason': 'Причина работ'
        })
        
        st.dataframe(
            display_sched[['Район', 'Дата', 'Время', 'Адреса', 'Причина работ']],
            column_config={
                "Адреса": st.column_config.TextColumn("Адреса", width="large"),
                "Причина работ": st.column_config.TextColumn("Причина работ", width="medium"),
            },
            width='stretch',
            hide_index=True
        )
        
        # Экспорт отфильтрованного графика в CSV
        csv_data = filtered_sched.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 Скачать отображаемый график (CSV)",
            data=csv_data,
            file_name=f"yakutskenergo_schedule_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )

# --- ВКЛАДКА: ПЕСОЧНИЦА АДРЕСОВ (MATCHER SANDBOX) ---
with tab_matcher:
    st.subheader("🧪 Песочница проверки адресов (Address Matcher)")
    st.markdown("Здесь можно проверить, как бот нормализует введенный пользователем адрес и находит ли совпадение в текущем графике.")
    
    # Быстрый выбор из существующих адресов в базе
    addr_options = [("— Ручной ввод —", None)]
    for _, row in df_addresses.iterrows():
        label = f"ID #{row['id']}: {row['district']}, {row['street']} (Chat ID: {row['chat_id']})"
        addr_options.append((label, (row['district'], row['street'])))
    
    selected_quick = st.selectbox(
        "Быстрый выбор из базы пользователей (для разбора жалоб):",
        options=range(len(addr_options)),
        format_func=lambda idx: addr_options[idx][0],
        key="quick_addr_select"
    )
    
    default_dist = "ЯКУТСК"
    default_str = "Лермонтова 58"
    
    if selected_quick > 0:
        _, val = addr_options[selected_quick]
        default_dist = val[0]
        default_str = val[1]
        
    col_m1, col_m2 = st.columns([1, 2])
    with col_m1:
        test_district = st.text_input("Район:", value=default_dist, key="test_district_val")
    with col_m2:
        test_street = st.text_input("Адрес (улица, дом, км):", value=default_str, key="test_street_val")

    matcher_mode = st.radio(
        "Алгоритм сопоставления:",
        ["V2 (Интеллектуальный с базой топонимов)", "V1 (Legacy)", "Сравнение V1 vs V2 (Side-by-Side)"],
        horizontal=True,
        key="matcher_engine_selector"
    )
        
    if st.button("🔍 Проверить совпадение по графику", type="primary", key="btn_run_matcher"):
        if not test_street.strip():
            st.warning("Введите улицу для проверки.")
        else:
            with st.spinner("Проверяем алгоритм сопоставления..."):
                norm_dist = normalize_district(test_district.strip())
                schedules_data = load_schedules()

                # Разбор обоими парсерами
                ua_v1 = parse_user_address_v1(test_street.strip())
                ua_v2 = parser_v2.parse_user_address(test_street.strip(), test_district.strip())

                if matcher_mode == "Сравнение V1 vs V2 (Side-by-Side)":
                    col_res1, col_res2 = st.columns(2)
                    
                    # Левая колонка: V1 Legacy
                    with col_res1:
                        st.subheader("🏛️ V1 (Legacy)")
                        with st.expander("Параметры V1", expanded=False):
                            st.json(ua_v1)
                        matches_v1 = []
                        for s in schedules_data:
                            if norm_dist not in normalize_district(s['district']):
                                continue
                            grps_v1 = split_schedule_addresses_v1(s['addresses'])
                            if match_address_against_schedule_v1(ua_v1, norm_dist, s, grps_v1):
                                matches_v1.append(s)
                        if matches_v1:
                            st.success(f"🎯 Найдено совпадений: {len(matches_v1)}")
                            for m in matches_v1:
                                st.caption(f"⚡ **{m['date']} ({m['time']})**\n{m['addresses']}")
                        else:
                            st.info("Совпадений нет")

                    # Правая колонка: V2 Enhanced
                    with col_res2:
                        st.subheader("🚀 V2 (Enhanced)")
                        with st.expander("Параметры V2", expanded=False):
                            st.json({
                                "street_name": ua_v2.street_name,
                                "canonical_name": ua_v2.canonical_name,
                                "core_name": ua_v2.core_name,
                                "house_num": ua_v2.house_num,
                                "main_house": ua_v2.main_house,
                                "sub_house": ua_v2.sub_house,
                                "korpus": ua_v2.korpus,
                                "structure": ua_v2.structure,
                                "is_km": ua_v2.is_km_address,
                                "km_value": ua_v2.km_value,
                                "is_sot": ua_v2.is_sot
                            })
                        matches_v2 = []
                        for s in schedules_data:
                            if norm_dist not in normalize_district(s['district']):
                                continue
                            grps_v2 = parser_v2.split_schedule_addresses_v2(s['addresses'])
                            res = parser_v2.match_address_detailed(ua_v2, norm_dist, s, grps_v2)
                            if res.matched:
                                matches_v2.append((s, res))
                        if matches_v2:
                            st.success(f"🎯 Найдено совпадений: {len(matches_v2)}")
                            for m, res in matches_v2:
                                st.caption(f"⚡ **{m['date']} ({m['time']})** — *{res.reason}*\n{m['addresses']}")
                        else:
                            st.info("Совпадений нет")

                elif matcher_mode == "V1 (Legacy)":
                    with st.expander("🛠️ Результат нормализации V1 (Legacy)", expanded=True):
                        st.json(ua_v1)
                    exact_matches = []
                    for s in schedules_data:
                        if norm_dist not in normalize_district(s['district']):
                            continue
                        grps_v1 = split_schedule_addresses_v1(s['addresses'])
                        if match_address_against_schedule_v1(ua_v1, norm_dist, s, grps_v1):
                            exact_matches.append(s)
                    if exact_matches:
                        st.success(f"🎯 **Найдено точных совпадений V1: {len(exact_matches)}**")
                        for s in exact_matches:
                            with st.container(border=True):
                                st.markdown(f"### ⚡ [{s['district']}] {s['date']} с {s['time']}")
                                st.markdown(f"> {s['addresses']}")
                    else:
                        st.info("ℹ️ В текущем графике совпадений не найдено.")

                else:
                    # Режим V2 (Enhanced)
                    with st.expander("🛠️ Результат нормализации V2 (с базой топонимов)", expanded=True):
                        col_u1, col_u2, col_u3, col_u4 = st.columns(4)
                        col_u1.metric("Норм. район", norm_dist or "-")
                        col_u2.metric("Улица / Канон", f"{ua_v2.street_name} ➔ {ua_v2.canonical_name}")
                        col_u3.metric("Дом / Дробь", f"{ua_v2.house_num or '-'}")
                        col_u4.metric("Корпус / Стр", f"к.{ua_v2.korpus or '-'} / с.{ua_v2.structure or '-'}")
                        st.json({
                            "raw_input": ua_v2.raw_input,
                            "street_name": ua_v2.street_name,
                            "canonical_name": ua_v2.canonical_name,
                            "core_name": ua_v2.core_name,
                            "house_num": ua_v2.house_num,
                            "main_house": ua_v2.main_house,
                            "sub_house": ua_v2.sub_house,
                            "korpus": ua_v2.korpus,
                            "structure": ua_v2.structure,
                            "mkr": ua_v2.mkr,
                            "is_km_address": ua_v2.is_km_address,
                            "km_value": ua_v2.km_value,
                            "is_sot": ua_v2.is_sot,
                            "sot_name": ua_v2.sot_name
                        })

                    exact_matches = []
                    for s in schedules_data:
                        if norm_dist not in normalize_district(s['district']):
                            continue
                        grps_v2 = parser_v2.split_schedule_addresses_v2(s['addresses'])
                        res = parser_v2.match_address_detailed(ua_v2, norm_dist, s, grps_v2)
                        if res.matched:
                            exact_matches.append((s, res))

                    st.divider()
                    if exact_matches:
                        st.success(f"🎯 **Найдено точных совпадений V2: {len(exact_matches)}** (Свет будет отключен)")
                        for s, res in exact_matches:
                            with st.container(border=True):
                                st.markdown(f"### ⚡ [{s['district']}] {s['date']} с {s['time']}")
                                st.markdown(f"**Причина матча:** `{res.reason}` *(уверенность: {res.confidence})*")
                                if s['reason']:
                                    st.markdown(f"**Причина работ:** {s['reason']}")
                                st.markdown(f"**Все адреса в пункте:**\n> {s['addresses']}")
                    else:
                        st.info("ℹ️ **В текущем графике Якутскэнерго совпадений по этому адресу не найдено.**")

with tab_users:
    status_filter = st.radio("Фильтр", ["Все", "Активные", "Заблокированные"], horizontal=True)
    filtered = df_users.copy()
    if status_filter == "Активные":
        filtered = filtered[filtered['bot_blocked'] == 0]
    elif status_filter == "Заблокированные":
        filtered = filtered[filtered['bot_blocked'] == 1]

    col_s, col_m = st.columns([3, 1])
    with col_s:
        search_id = st.text_input("Поиск по chat_id")
    if search_id:
        filtered = filtered[filtered['chat_id'].astype(str).str.contains(search_id)]

    filtered_display = filtered.copy()
    filtered_display['created_at'] = filtered_display['created_at'].apply(format_ru_date)

    display = filtered_display.rename(columns={
        'chat_id': 'Chat ID', 
        'bot_blocked': 'Заблокирован',
        'created_at': 'Зарегистрирован', 
        'address_count': 'Адресов',
        'notif_count': 'Уведомлений',
        'request_count': 'Запросов'
    })
    display['Заблокирован'] = display['Заблокирован'].map({0: 'Нет', 1: 'Да'})
    st.dataframe(
        display[['Chat ID', 'Заблокирован', 'Зарегистрирован', 'Адресов', 'Уведомлений', 'Запросов']], 
        width='stretch', 
        hide_index=True
    )

    st.divider()
    st.subheader("Адреса выбранного пользователя")
    user_ids = st.multiselect("Выберите chat_id", options=sorted(df_users['chat_id'].tolist()))
    if user_ids:
        user_addrs = df_addresses[df_addresses['chat_id'].isin(user_ids)].copy()
        user_addrs['created_at'] = user_addrs['created_at'].apply(format_ru_date)
        st.dataframe(
            user_addrs[['id', 'district', 'street', 'created_at']].rename(columns={
                'id': 'ID', 'district': 'Район', 'street': 'Улица', 'created_at': 'Добавлен'
            }),
            width='stretch', hide_index=True
        )

with tab_addresses:
    st.subheader(f"Всего адресов: {len(df_addresses)}")

    districts = sorted(df_addresses['district'].unique())
    sel_district = st.selectbox("Фильтр по району", ["Все"] + districts)
    filtered_addr = df_addresses.copy()
    if sel_district != "Все":
        filtered_addr = filtered_addr[filtered_addr['district'] == sel_district]

    filtered_addr_display = filtered_addr.copy()
    filtered_addr_display['created_at'] = filtered_addr_display['created_at'].apply(format_ru_date)

    st.dataframe(
        filtered_addr_display[['id', 'chat_id', 'district', 'street', 'created_at']].rename(columns={
            'id': 'ID', 'chat_id': 'Chat ID', 'district': 'Район',
            'street': 'Улица', 'created_at': 'Добавлен'
        }),
        width='stretch', hide_index=True
    )

    st.subheader("Адресов по районам")
    dist_counts = df_addresses['district'].value_counts().reset_index()
    dist_counts.columns = ['Район', 'Количество']
    st.dataframe(dist_counts, width='stretch', hide_index=True)

with tab_notif:
    st.subheader(f"Всего отправлено: {len(df_notifications)}")

    notif_filter = st.text_input("Поиск по chat_id, улице или тексту работ", key="notif_search")
    filtered_notif = df_notifications.copy()
    if notif_filter:
        mask = (
            filtered_notif['chat_id'].astype(str).str.contains(notif_filter, case=False, na=False) |
            filtered_notif['street'].astype(str).str.contains(notif_filter, case=False, na=False) |
            filtered_notif['message_text'].astype(str).str.contains(notif_filter, case=False, na=False)
        )
        filtered_notif = filtered_notif[mask]

    filtered_notif_display = filtered_notif.copy()
    filtered_notif_display['sent_at'] = filtered_notif_display['sent_at'].apply(lambda x: format_ru_date(x, include_time=True))

    display_notif = filtered_notif_display.rename(columns={
        'chat_id': 'Chat ID', 'address_id': 'ID адреса',
        'schedule_hash': 'Хэш', 'sent_at': 'Отправлено (ЯКТ)',
        'district': 'Район', 'street': 'Улица',
        'message_text': 'Текст работ'
    })
    st.dataframe(
        display_notif,
        column_config={
            "Текст работ": st.column_config.TextColumn("Текст работ", width="large")
        },
        width='stretch',
        hide_index=True
    )

    has_text_df = filtered_notif[filtered_notif['message_text'] != '']
    if not has_text_df.empty:
        with st.expander("🔍 Детальный просмотр текста уведомлений", expanded=False):
            for _, r in has_text_df.head(20).iterrows():
                dt_str = format_ru_date(r['sent_at'], include_time=True)
                st.markdown(f"**Chat ID:** `{r['chat_id']}` | **Адрес:** {r['district']}, {r['street']} | **Отправлено:** {dt_str}")
                st.text(r['message_text'])
                st.divider()

with tab_tickets:
    ticket_view = st.radio("Показать", ["Открытые", "Закрытые", "Все"], horizontal=True)

    filtered_tickets = df_tickets.copy()
    if ticket_view == "Открытые":
        filtered_tickets = filtered_tickets[filtered_tickets['admin_reply'].isna()]
    elif ticket_view == "Закрытые":
        filtered_tickets = filtered_tickets[filtered_tickets['admin_reply'].notna()]

    for _, row in filtered_tickets.iterrows():
        with st.container(border=True):
            st.markdown(f"**Тикет #{row['id']}** — Chat ID: `{row['chat_id']}` — {format_ru_date(row['created_at'], include_time=True)}")
            if row['user_username']:
                st.markdown(f"Username: @{row['user_username']}")
            st.markdown(f"**Сообщение:** {row['user_message']}")
            if pd.notna(row['admin_reply']):
                st.success(f"**Ответ:** {row['admin_reply']} ({format_ru_date(row['replied_at'], include_time=True)})")
            else:
                reply_key = f"reply_{row['id']}"
                reply_text = st.text_area("Ваш ответ:", key=f"input_{row['id']}", label_visibility="collapsed")
                if st.button("Отправить ответ", key=f"btn_{row['id']}") and reply_text.strip():
                    if reply_to_ticket(row['id'], row['chat_id'], reply_text.strip()):
                        st.success("Ответ отправлен!")
                        st.rerun()

    if filtered_tickets.empty:
        st.info("Нет тикетов")

with tab_logs:
    st.subheader(f"Всего записей: {len(df_logs)}")

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        log_search = st.text_input("Поиск по запросу")
    with col_f2:
        status_filter_log = st.selectbox("Статус", ["Все", "Найдено", "Не найдено"])
    with col_f3:
        days_back = st.number_input("Последние N дней", min_value=1, value=30)

    filtered_logs = df_logs.copy()
    cutoff = datetime.now() - timedelta(days=days_back)
    filtered_logs['timestamp_dt'] = pd.to_datetime(filtered_logs['timestamp'])
    filtered_logs = filtered_logs[filtered_logs['timestamp_dt'] >= cutoff]

    if log_search:
        filtered_logs = filtered_logs[filtered_logs['query_details'].str.contains(log_search, case=False, na=False)]
    if status_filter_log == "Найдено":
        filtered_logs = filtered_logs[filtered_logs['found_status'] == 1]
    elif status_filter_log == "Не найдено":
        filtered_logs = filtered_logs[filtered_logs['found_status'] == 0]

    filtered_logs_display = filtered_logs.copy()
    filtered_logs_display['timestamp'] = filtered_logs_display['timestamp'].apply(lambda x: format_ru_date(x, include_time=True))

    display_logs = filtered_logs_display.drop(columns=['timestamp_dt']).rename(columns={
        'id': 'ID', 'timestamp': 'Время (ЯКТ)', 'chat_id': 'Chat ID',
        'query_details': 'Запрос', 'found_status': 'Найдено'
    })
    display_logs['Найдено'] = display_logs['Найдено'].map({0: 'Нет', 1: 'Да'})
    st.dataframe(display_logs, width='stretch', hide_index=True)

st.caption(f"Данные актуальны на: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
           f"(кеш обновляется каждые 60 сек)")
st.caption(f"https://www.yakutskenergo.ru/press/news/news-remont")
