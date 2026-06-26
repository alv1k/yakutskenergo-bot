import sqlite3

DB_NAME = 'bot_database.db'
MAX_ADDRESSES = 10

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            bot_blocked INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            district TEXT NOT NULL,
            street TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (chat_id) REFERENCES users(chat_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_notifications (
            chat_id INTEGER,
            address_id INTEGER,
            schedule_hash TEXT,
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, address_id, schedule_hash)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            chat_id INTEGER,
            query_details TEXT,
            found_status BOOLEAN
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_message TEXT NOT NULL,
            user_username TEXT,
            admin_reply TEXT,
            replied_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def migrate_from_old_schema():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if cursor.fetchone():
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'street' in columns:
            cursor.execute('ALTER TABLE users RENAME TO users_old')
            cursor.execute('''
                CREATE TABLE users (
                    chat_id INTEGER PRIMARY KEY,
                    bot_blocked INTEGER DEFAULT 0
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS addresses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    district TEXT NOT NULL,
                    street TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES users(chat_id)
                )
            ''')
            cursor.execute('''
                INSERT OR IGNORE INTO users (chat_id, bot_blocked)
                SELECT chat_id, COALESCE(bot_blocked, 0) FROM users_old
            ''')
            cursor.execute('''
                INSERT INTO addresses (chat_id, district, street)
                SELECT chat_id, COALESCE(district, 'ЯКУТСК'), COALESCE(street, '')
                FROM users_old
                WHERE street IS NOT NULL AND street != ''
            ''')
            cursor.execute('DROP TABLE IF EXISTS users_old')
            conn.commit()
    conn.close()

def create_support_ticket(chat_id, user_message, user_username=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO support_tickets (chat_id, user_message, user_username)
        VALUES (?, ?, ?)
    ''', (chat_id, user_message, user_username))
    ticket_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return ticket_id

def reply_support_ticket(ticket_id, admin_reply):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE support_tickets
        SET admin_reply = ?, replied_at = CURRENT_TIMESTAMP
        WHERE id = ?
    ''', (admin_reply, ticket_id))
    conn.commit()
    conn.close()

def get_last_open_ticket(chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, chat_id, user_message, user_username, admin_reply, replied_at, created_at
        FROM support_tickets
        WHERE chat_id = ? AND admin_reply IS NULL
        ORDER BY id DESC LIMIT 1
    ''', (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def log_request(chat_id, query_details, found_status):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO request_logs (chat_id, query_details, found_status) VALUES (?, ?, ?)',
                   (chat_id, query_details, found_status))
    conn.commit()
    conn.close()

def get_address_count(chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM addresses WHERE chat_id = ?', (chat_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count

def add_address(chat_id, district, street):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM addresses WHERE chat_id = ?', (chat_id,))
    if cursor.fetchone()[0] >= MAX_ADDRESSES:
        conn.close()
        return False
    cursor.execute('INSERT OR IGNORE INTO users (chat_id, bot_blocked) VALUES (?, 0)', (chat_id,))
    cursor.execute('INSERT INTO addresses (chat_id, district, street) VALUES (?, ?, ?)',
                   (chat_id, district, street))
    address_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return address_id

def remove_address(chat_id, address_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM addresses WHERE chat_id = ? AND id = ?', (chat_id, address_id))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def get_user_addresses(chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, district, street FROM addresses WHERE chat_id = ? ORDER BY id', (chat_id,))
    addresses = cursor.fetchall()
    conn.close()
    return addresses

def get_all_addresses():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT a.chat_id, a.id, a.district, a.street
        FROM addresses a
        JOIN users u ON a.chat_id = u.chat_id
        WHERE u.bot_blocked = 0
    ''')
    addresses = cursor.fetchall()
    conn.close()
    return addresses

def is_notified(chat_id, address_id, schedule_hash):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM sent_notifications WHERE chat_id = ? AND address_id = ? AND schedule_hash = ?',
                   (chat_id, address_id, schedule_hash))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def mark_as_notified(chat_id, address_id, schedule_hash, sent_at=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        if sent_at:
            cursor.execute('INSERT INTO sent_notifications (chat_id, address_id, schedule_hash, sent_at) VALUES (?, ?, ?, ?)',
                           (chat_id, address_id, schedule_hash, sent_at))
        else:
            cursor.execute('INSERT INTO sent_notifications (chat_id, address_id, schedule_hash) VALUES (?, ?, ?)',
                           (chat_id, address_id, schedule_hash))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def set_blocked(chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET bot_blocked = 1 WHERE chat_id = ?', (chat_id,))
    conn.commit()
    conn.close()

def is_blocked(chat_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT bot_blocked FROM users WHERE chat_id = ?', (chat_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None and res[0] == 1

if __name__ == "__main__":
    init_db()
    migrate_from_old_schema()
    print("Database initialized and migrated.")
