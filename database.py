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
        # --- ВНУТРИ КЛАССА StatsManager в database.py ---

    def check_and_add_raw_text(self, new_text):
        """
        Проверяет, был ли такой текст раньше.
        Возвращает True (Дубль), если находит похожий.
        Иначе сохраняет и возвращает False.
        """
        now = time.time()
        
        # 1. Сначала чистим совсем старье (старше 48 часов), чтобы база не пухла
        # Но держим память дольше (было 24 часа)
        self.cursor.execute('DELETE FROM raw_history WHERE timestamp < ?', (now - 172800,))
        self.conn.commit()

        # 2. Достаем последние 500 текстов (было 200)
        # Это защитит от частого постинга одной и той же новости в разных каналах
        self.cursor.execute('SELECT text FROM raw_history ORDER BY id DESC LIMIT 500')
        rows = self.cursor.fetchall()

        # 3. Сравниваем
        for row in rows:
            old_text = row[0]
            
            # Быстрая проверка на полное совпадение (хэши)
            if new_text == old_text:
                return True

            # Fuzzy Match: 
            # Понижаем порог до 0.50 (50%). 
            # Если тексты похожи хотя бы наполовину — считаем дублем.
            matcher = difflib.SequenceMatcher(None, new_text, old_text)
            if matcher.ratio() > 0.50:
                return True 

        # 4. Если дубля нет — сохраняем "Сырой" текст
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
