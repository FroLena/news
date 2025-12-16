import sqlite3
import json
import time
import os
import difflib # Импортируем для сравнения
from datetime import datetime
from config import DB_PATH, HISTORY_FILE, MSK_TZ

class StatsManager:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._init_db()

    def _init_db(self):
        # Таблица статистики
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                scanned INTEGER DEFAULT 0,
                published INTEGER DEFAULT 0,
                rejected_ads INTEGER DEFAULT 0,
                rejected_dups INTEGER DEFAULT 0,
                rejected_other INTEGER DEFAULT 0
            )
        ''')
        # НОВАЯ ТАБЛИЦА: Хранит сырые тексты входящих новостей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS raw_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT,
                timestamp REAL
            )
        ''')
        self.conn.commit()

    # --- СТАТИСТИКА ---
    def increment(self, field):
        today = datetime.now(MSK_TZ).strftime('%Y-%m-%d')
        try:
            self.cursor.execute(f'UPDATE daily_stats SET {field} = {field} + 1 WHERE date = ?', (today,))
            if self.cursor.rowcount == 0:
                self.cursor.execute(f'INSERT INTO daily_stats (date, {field}) VALUES (?, 1)', (today,))
            self.conn.commit()
        except Exception as e:
            print(f"⚠️ Ошибка БД: {e}")

    def get_stats(self):
        today = datetime.now(MSK_TZ).strftime('%Y-%m-%d')
        self.cursor.execute('SELECT * FROM daily_stats WHERE date = ?', (today,))
        row = self.cursor.fetchone()
        if row:
            return {
                'date': row[0], 'scanned': row[1], 'published': row[2],
                'rejected_ads': row[3], 'rejected_dups': row[4], 'rejected_other': row[5]
            }
        return None

    # --- НОВАЯ ЛОГИКА ДУБЛЕЙ (ВЕЧНАЯ) ---
    def check_and_add_raw_text(self, new_text):
        """
        1. Сравнивает новый текст со всеми за последние 24 часа.
        2. Если находит совпадение > 60% — возвращает True (Дубль).
        3. Если дубля нет — сохраняет текст в базу и возвращает False.
        """
        now = time.time()
        # Чистим старье (старше 24 часов), чтобы база не тормозила
        self.cursor.execute('DELETE FROM raw_history WHERE timestamp < ?', (now - 86400,))
        self.conn.commit()

        # Достаем последние 200 текстов для проверки
        self.cursor.execute('SELECT text FROM raw_history ORDER BY id DESC LIMIT 200')
        rows = self.cursor.fetchall()

        # Сравниваем
        for row in rows:
            old_text = row[0]
            # Fuzzy Match: Порог 0.60 (60% сходства)
            matcher = difflib.SequenceMatcher(None, new_text, old_text)
            if matcher.ratio() > 0.60:
                return True # НАШЛИ ДУБЛЬ!

        # Если не нашли — сохраняем
        try:
            self.cursor.execute('INSERT INTO raw_history (text, timestamp) VALUES (?, ?)', (new_text, now))
            self.conn.commit()
        except: pass
        
        return False

# --- JSON ИСТОРИЯ (ДЛЯ GPT) ---
def load_history():
    if not os.path.exists(HISTORY_FILE): return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [item for item in data if time.time() - item['timestamp'] < 86400]
    except: return []

def save_to_history(text_essence):
    history = load_history()
    history.append({'text': text_essence, 'timestamp': time.time()})
    if len(history) > 50: history = history[-50:]
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
    except: pass

stats_db = StatsManager()
