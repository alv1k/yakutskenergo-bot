import streamlit as st
import pandas as pd
import sqlite3
import hashlib

# --- Configuration ---
DB_NAME = 'bot_database.db'
PASSWORD_HASH = "8757bebe2081f3ce966925fa0b548badb9ca3910ab6ab39ea2523fd8c62d783d"

def check_password():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    
    if not st.session_state.logged_in:
        pwd = st.text_input("Введите пароль:", type="password")
        if pwd:
            if hashlib.sha256(pwd.encode()).hexdigest() == PASSWORD_HASH:
                st.session_state.logged_in = True
                st.rerun()
            else:
                st.error("Неверный пароль")
        return False
    return True

st.set_page_config(page_title="Bot Dashboard", layout="wide")

if check_password():
    st.title("📊 Дашборд пользователей бота")
    
    conn = sqlite3.connect(DB_NAME)
    
    df_users = pd.read_sql('SELECT * FROM users', conn)
    df_logs = pd.read_sql('SELECT * FROM request_logs ORDER BY timestamp DESC LIMIT 50', conn)
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Всего пользователей", len(df_users))
    with col2:
        st.metric("Уникальных районов", df_users['district'].nunique())
    
    st.subheader("Статистика по районам")
    st.bar_chart(df_users['district'].value_counts())
    
    st.subheader("Список пользователей")
    st.dataframe(df_users, use_container_width=True)

    st.subheader("История запросов")
    st.dataframe(df_logs, use_container_width=True)
    
    if st.button("Обновить данные"):
        st.rerun()
        
    conn.close()
