import sqlite3

DB_NAME = 'bot_database.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            district TEXT,
            street TEXT,
            last_notified TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_user_preference(chat_id, district=None, street=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id FROM users WHERE chat_id = ?', (chat_id,))
    if cursor.fetchone():
        if district:
            cursor.execute('UPDATE users SET district = ? WHERE chat_id = ?', (district, chat_id))
        if street:
            cursor.execute('UPDATE users SET street = ? WHERE chat_id = ?', (street, chat_id))
    else:
        cursor.execute('INSERT INTO users (chat_id, district, street) VALUES (?, ?, ?)', (chat_id, district, street))
    conn.commit()
    conn.close()

def get_user_preference(chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT district, street FROM users WHERE chat_id = ?', (chat_id,))
    res = cursor.fetchone()
    conn.close()
    return res

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id, district, street FROM users')
    users = cursor.fetchall()
    conn.close()
    return users

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
