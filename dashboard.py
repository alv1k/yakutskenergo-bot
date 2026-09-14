import os
import requests
from dotenv import load_dotenv
import streamlit as st
import pandas as pd
import sqlite3
import hashlib
from datetime import datetime, timedelta

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

DB_NAME = 'bot_database.db'
PASSWORD_HASH = "5ef8b46fd33d194c94af08922dd369f90ed597504dc0b22697f97d95f60bba3a"

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

st.set_page_config(page_title="Якутскэнерго — Дашборд", layout="wide", page_icon="favicon.png")

if not check_password():
    st.stop()

st.title("Якутскэнерго — панель управления ботом")

df_users = load_users()
df_addresses = load_addresses()
df_notifications = load_notifications()
df_logs = load_request_logs()
df_tickets = load_tickets()

tab_overview, tab_users, tab_addresses, tab_notif, tab_tickets, tab_logs = \
    st.tabs(["Обзор", "Пользователи", "Адреса", "Уведомления", "Тикеты", "Лог запросов"])

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
        # Ensure standard column naming
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

    notif_filter = st.text_input("Поиск по chat_id", key="notif_search")
    filtered_notif = df_notifications.copy()
    if notif_filter:
        filtered_notif = filtered_notif[filtered_notif['chat_id'].astype(str).str.contains(notif_filter)]

    filtered_notif_display = filtered_notif.copy()
    filtered_notif_display['sent_at'] = filtered_notif_display['sent_at'].apply(lambda x: format_ru_date(x, include_time=True))

    display_notif = filtered_notif_display.rename(columns={
        'chat_id': 'Chat ID', 'address_id': 'ID адреса',
        'schedule_hash': 'Хэш', 'sent_at': 'Отправлено (ЯКТ)',
        'district': 'Район', 'street': 'Улица'
    })
    st.dataframe(display_notif, width='stretch', hide_index=True)

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
         
