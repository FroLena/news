import sqlite3
from datetime import datetime
import pytz

# Настройки
DB_NAME = "stats.db"
MSK_TZ = pytz.timezone('Europe/Moscow')

class StatsManager:
    def __init__(self):
        self.conn = sqlite3.connect(DB_NAME, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._init_db()

    def _init_db(self):
        """Создает таблицу, если её нет"""
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
        self.conn.commit()

    def _get_today_str(self):
        """Получаем текущую дату по МСК"""
        return datetime.now(MSK_TZ).strftime('%Y-%m-%d')

    def increment(self, field):
        """
        Увеличивает счетчик.
        field: 'scanned', 'published', 'rejected_ads', 'rejected_dups', 'rejected_other'
        """
        today = self._get_today_str()
        try:
            # Пытаемся обновить существующую запись
            self.cursor.execute(f'''
                UPDATE daily_stats 
                SET {field} = {field} + 1 
                WHERE date = ?
            ''', (today,))
            
            # Если ничего не обновилось (новая дата), создаем запись
            if self.cursor.rowcount == 0:
                self.cursor.execute(f'''
                    INSERT INTO daily_stats (date, {field}) 
                    VALUES (?, 1)
                ''', (today,))
            
            self.conn.commit()
        except Exception as e:
            print(f"Ошибка БД (статистика): {e}")

    def get_stats(self, date_str=None):
        """Возвращает статистику за дату (по умолчанию за сегодня)"""
        if not date_str:
            date_str = self._get_today_str()
            
        self.cursor.execute('SELECT * FROM daily_stats WHERE date = ?', (date_str,))
        row = self.cursor.fetchone()
        
        if row:
            return {
                'date': row[0],
                'scanned': row[1],
                'published': row[2],
                'rejected_ads': row[3],
                'rejected_dups': row[4],
                'rejected_other': row[5]
            }
        else:
            return None

# Создаем глобальный экземпляр, чтобы импортировать его в main
stats_db = StatsManager()
