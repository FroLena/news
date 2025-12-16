import os
import asyncio
import json
import httpx
import urllib.parse
import time
import sqlite3
import pytz
from datetime import datetime
from telethon import TelegramClient, events, types, functions
from telethon.sessions import StringSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import edge_tts

# ==========================================
# ЧАСТЬ 1. СТАТИСТИКА (Встроено)
# ==========================================

# Проверяем путь для базы данных
if os.path.exists('/data'):
    DB_PATH = os.path.join('/data', 'stats.db')
else:
    DB_PATH = os.path.join('.', 'stats.db')

MSK_TZ = pytz.timezone('Europe/Moscow')

class StatsManager:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
        return datetime.now(MSK_TZ).strftime('%Y-%m-%d')

    def increment(self, field):
        today = self._get_today_str()
        try:
            self.cursor.execute(f'UPDATE daily_stats SET {field} = {field} + 1 WHERE date = ?', (today,))
            if self.cursor.rowcount == 0:
                self.cursor.execute(f'INSERT INTO daily_stats (date, {field}) VALUES (?, 1)', (today,))
            self.conn.commit()
        except Exception as e:
            print(f"⚠️ Ошибка БД: {e}")

    def get_stats(self, date_str=None):
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
        return None

# Инициализируем БД
stats_db = StatsManager()

# ==========================================
# ЧАСТЬ 2. ПЛАНИРОВЩИК (Встроено)
# ==========================================

REPORT_DESTINATION = '@s_ostatok'

async def send_daily_report(client: TelegramClient):
    """Формирует и отправляет отчет"""
    print("📊 Формирую ежедневный отчет...")
    data = stats_db.get_stats()
    
    if not data:
        print("📊 Данных за сегодня нет.")
        return

    saved_minutes = (data['scanned'] - data['published']) * 2
    saved_hours = round(saved_minutes / 60, 1)

    text = (
        f"🌙 **Итоги дня: {data['date']}**\n\n"
        f"Сегодня я просеял для вас весь информационный шум.\n\n"
        f"📊 **Сухие цифры:**\n"
        f"• Просканировано постов: {data['scanned']}\n"
        f"• Опубликовано в канале: {data['published']}\n"
        f"• Отсеяно мусора: {data['scanned'] - data['published']}\n"
        f"  ├ 🛑 Реклама: {data['rejected_ads']}\n"
        f"  ├ 👯 Дубли: {data['rejected_dups']}\n"
        f"  └ 📉 Несущественное: {data['rejected_other']}\n\n"
        f"⏳ **Ваша выгода:**\n"
        f"Вы сэкономили ~{saved_hours} часа времени, не читая лишнее.\n"
        f"Спокойной ночи! 🤖"
    )

    try:
        await client.send_message(REPORT_DESTINATION, text)
        print("✅ Ежедневный отчет отправлен.")
    except Exception as e:
        print(f"❌ Ошибка отправки отчета: {e}")

# ==========================================
# ЧАСТЬ 3. ОСНОВНОЙ БОТ
# ==========================================

# 1. Настройки
try:
    API_ID = int(os.environ.get('TG_API_ID', 0))
    API_HASH = os.environ.get('TG_API_HASH')
    OPENAI_KEY = os.environ.get('OPENAI_API_KEY')
    SESSION_STRING = os.environ.get('TG_SESSION_STR')
    
    if API_ID == 0 or not API_HASH:
        raise ValueError("Не заданы API_ID или API_HASH")
except Exception as e:
    print(f"❌ КРИТИЧЕСКАЯ ОШИБКА НАСТРОЕК: {e}")
    time.sleep(30)
    exit(1)

SOURCE_CHANNELS = [
    'rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon', 
    'shot_shot', 'ostorozhno_novosti', 'rbc_news'
]
DESTINATION = '@s_ostatok'

# Пути
if os.path.exists('/data'):
    print("🖥 СРЕДА: СЕРВЕР (Amvera). Все файлы пишу в /data")
    BASE_DIR = '/data'
else:
    print("💻 СРЕДА: ЛОКАЛЬНАЯ. Пишу файлы рядом со скриптом")
    BASE_DIR = '.'

HISTORY_FILE = os.path.join(BASE_DIR, 'history.json')
PODCAST_FILE = os.path.join(BASE_DIR, 'podcast.mp3')

MAX_VIDEO_SIZE = 50 * 1024 * 1024 
AI_MODEL = "openai/gpt-4o-mini"

# Клиент
try:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
except Exception as e:
    print(f"❌ Ошибка клиента: {e}")
    client = None

raw_text_cache = []

# --- ИСТОРИЯ ---
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

# --- GPT ЗАПРОС ---
async def ask_gpt_direct(system_prompt, user_text):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://amvera.ru",
        "X-Title": "NewsBot"
    }
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]
    }
    
    last_error = None
    for i in range(3):
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            try:
                response = await http_client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content']
                else:
                    print(f"⚠️ GPT Ошибка {response.status_code}: {response.text}")
            except Exception as e:
                last_error = e
                print(f"⚠️ GPT Connection Error (попытка {i+1}): {e}")
            await asyncio.sleep(5)
            
    print(f"❌ GPT не ответил после 3 попыток. Последняя ошибка: {last_error}")
    return None

# --- ГЕНЕРАЦИЯ КАРТИНКИ (С USER-AGENT) ---
async def generate_image(prompt_text):
    clean_prompt = prompt_text.replace('|||', '').replace('=== ПРОМПТ ===', '').strip()
    tech_suffix = " . Shot on Phase One XF IQ4, 150MP, ISO 100, f/8, crystal clear, sharp focus, professional stock photography, no grain, no blur, bright lighting."
    final_prompt = clean_prompt + tech_suffix
    encoded_prompt = urllib.parse.quote(final_prompt)
    
    import random
    seed = random.randint(1, 1000000)
    filename = os.path.join(BASE_DIR, f"image_{seed}.jpg")
    
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&model=flux&seed={seed}&nologo=true"
    
    # Добавляем User-Agent
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    for i in range(3):
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http_client:
            try:
                print(f"🎨 Попытка генерации фото ({i+1}/3)...")
                response = await http_client.get(url, headers=headers)
                
                if response.status_code == 200:
                    with open(filename, "wb") as f: f.write(response.content)
                    if os.path.getsize(filename) > 0:
                        return filename
                    else:
                        print("⚠️ Скачан пустой файл изображения")
                else:
                    print(f"⚠️ Ошибка Pollinations API: {response.status_code}")
                    
            except Exception as e:
                print(f"⚠️ Ошибка сети при генерации фото: {e}")
            
            await asyncio.sleep(2)
            
    print("❌ Не удалось сгенерировать картинку после 3 попыток")
    return None

# --- AI РЕДАКТОР (АГРЕССИВНЫЙ ФИЛЬТР) ---
async def rewrite_news(text):
    history_items = load_history()
    # Увеличили историю для проверки дублей
    recent_history = history_items[-30:]
    history_str = "\n".join([f"- {item['text']}" for item in recent_history]) if recent_history else "История пуста."

    system_prompt = (
        f"Ты — циничный и строгий главный редактор канала 'Сухой остаток'.\n"
        f"Твоя задача: Выжимать факты из новостей, безжалостно убирая воду и канцелярщину.\n"
        f"СПИСОК ОПУБЛИКОВАННЫХ СОБЫТИЙ (ЧТОБЫ НЕ ПОВТОРЯТЬСЯ):\n{history_str}\n\n"
        
        f"=== ЧАСТЬ 1. ЖЕСТКИЙ ФИЛЬТР (ПРИОРИТЕТ №1) ===\n"
        f"Твоя первая задача — отсеять лишнее. Не жалей контент.\n"
        f"1. РЕКЛАМА -> ВЕРНИ: SKIP\n"
        f"   (Любые продажи, 'erid', 'партнерский пост', ссылки на другие каналы/курсы/товары).\n"
        f"2. ДУБЛИ -> ВЕРНИ: DUPLICATE\n"
        f"   (Сравни с ИСТОРИЕЙ ВЫШЕ. Если событие уже описано — даже другими словами — это ДУБЛЬ).\n"
        f"   (Если это просто видео/фото к уже известной новости без новых фактов — это ДУБЛЬ).\n"
        f"3. МУСОР -> ВЕРНИ: SKIP\n"
        f"   (Приветствия, 'доброе утро', анонсы без фактов, 'подробнее в комментариях', просьбы подписаться).\n\n"
        
        f"=== ЧАСТЬ 2. ПРАВИЛА ТЕКСТА (INFOSTYLE) ===\n"
        f"Язык: Русский. Формат: HTML.\n"
        f"1. ТЕГИ: Используй только <b>жирный</b> и <blockquote>цитата</blockquote>. Markdown (**) ЗАПРЕЩЕН.\n"
        f"2. СТИЛЬ: Инфостиль Максима Ильяхова. \n"
        f"   - ЗАПРЕЩЕНО: 'Сообщается', 'Стало известно', 'В сети появилось', 'Отметим, что'. Сразу к делу.\n"
        f"   - ЗАПРЕЩЕНО: Оценочные суждения ('Ужасная трагедия', 'Потрясающий успех'). Только факты.\n"
        f"3. ОБЪЕМ: Не более 600 знаков. Один плотный абзац + вывод.\n"
        f"4. СТРУКТУРА:\n"
        f"   - Реакция (Скрытый тег).\n"
        f"   - <b>Заголовок</b> (Хлесткий, 3-6 слов, без точки на конце).\n"
        f"   - <ПУСТАЯ СТРОКА>\n"
        f"   - Текст новости (Кто, что сделал, последствия).\n"
        f"   - <blockquote>(Короткий вывод или ирония редактора).</blockquote>\n"
        f"=== ЧАСТЬ 3. ПРАВИЛА ОПРОСОВ (ВАЖНО!) ===\n"
        f"Ты ОБЯЗАН создать опрос, если в новости есть:\n"
        f" - ДЕНЬГИ (Цены, зарплаты, штрафы, крипта).\n"
        f" - ЗАПРЕТЫ (Новые законы, блокировки, ограничения).\n"
        f" - КОНФЛИКТ (Кто-то с кем-то спорит, судится, воюет).\n"
        f" - ТЕХНОЛОГИИ (ИИ, роботы, гаджеты - заменит ли это людей?).\n"
        f"Вопрос должен быть ПРОВОКАЦИОННЫМ. Не спрашивай 'Как вы к этому относитесь?'.\n"
        f"Спрашивай конкретно: 'Пора валить?', 'Оштрафуют нас?', 'Это прорыв или скам?'.\n"
        f"Цель: Заставить читателя тыкнуть кнопку.\n\n"
        f"=== ЧАСТЬ 4. ПРАВИЛА КАРТИНКИ (DIGITAL STOCK PHOTO) ===\n"
        f"Prompt strictly in English.\n"
        f"Target: High-end commercial photography, 8k resolution.\n"
        f"Style: Shot on Phase One XF IQ4, 150MP, sharp focus, bright natural lighting.\n"
        f"Content: Describe the scene objectively. NO TEXT in image. NO BLUR.\n"
        f"Restriction: If crime/war -> use symbolic objects (police tape, gavel, silhouette), no gore/blood.\n\n"
        
        f"=== ШАБЛОН ОТВЕТА (ЕСЛИ НЕТ ОПРОСА) ===\n"
        f"||R:🔥|| <b>Заголовок</b>\n\n"
        f"Текст новости...\n"
        f"<blockquote>Вывод редактора.</blockquote>\n"
        f"|||\n"
        f"Prompt...\n\n"
        
        f"=== ШАБЛОН ОТВЕТА (С ОПРОСОМ) ===\n"
        f"||R:😱|| <b>Заголовок</b>\n\n"
        f"Текст новости...\n"
        f"<blockquote>Вывод редактора.</blockquote>\n"
        f"||POLL||\n"
        f"Острый вопрос?\n"
        f"Да, это круто\n"
        f"Нет, бред полный\n"
        f"|||\n"
        f"Prompt..."
    )
    return await ask_gpt_direct(system_prompt, text)

@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handler(event):
    path_to_image = None
    path_to_video = None
    
    text = event.message.message
    if not text or len(text) < 20: return

    # УЛУЧШЕННЫЙ КЭШ: Берем хеш от всего текста и убираем пробелы, чтобы ловить 100% дубли
    clean_text = text.strip()
    # Используем хеш-функцию для всего текста, а не срез 100 символов
    text_hash = hash(clean_text)
    
    if text_hash in raw_text_cache: return
    raw_text_cache.append(text_hash)
    # Увеличили размер кэша до 1000, чтобы помнить новости дольше
    if len(raw_text_cache) > 1000: raw_text_cache.pop(0)

    stats_db.increment('scanned')

    try:
        chat = await event.get_chat()
        print(f"🔎 Обработка новости из: {chat.title}")
    except: pass
    
    full_response = await rewrite_news(text)
    
    if not full_response:
        stats_db.increment('rejected_other')
        print("❌ GPT вернул пустоту")
        return

    if "DUPLICATE" in full_response: 
        print(f"❌ Отсечен дубль")
        stats_db.increment('rejected_dups')
        return
    if "SKIP" in full_response: 
        print(f"🗑 Отсечена реклама/мусор")
        stats_db.increment('rejected_ads')
        return

    # --- ПАРСИНГ ---
    raw_text = full_response
    image_prompt = None
    if "|||" in full_response:
        parts = full_response.split("|||")
        news_text = parts[0].strip()
        if len(parts) > 1: image_prompt = parts[1].strip()
    else:
        news_text = full_response.strip()

    reaction = None
    if "||R:" in news_text:
        try:
            parts = news_text.split("||R:")
            subparts = parts[1].split("||")
            reaction = subparts[0].strip()
            news_text = subparts[1].strip()
        except: pass

    poll_data = None
    if "||POLL||" in news_text:
        try:
            parts = news_text.split("||POLL||")
            news_text = parts[0].strip()
            poll_raw = parts[1].strip().split('\n')
            poll_lines = [line.strip() for line in poll_raw if line.strip()]
            if len(poll_lines) >= 3:
                poll_data = {"q": poll_lines[0], "o": poll_lines[1:]}
        except: pass

    if not image_prompt and event.message.photo:
        base_prompt = news_text.replace('\n', ' ')[:200]
        image_prompt = f"Commercial photo of {base_prompt}. Bright light, 8k sharp."

    sent_msg = None
    try:
        has_video = event.message.video is not None
        if has_video:
            if event.message.file.size > MAX_VIDEO_SIZE:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                path_to_video = await event.download_media()
                if path_to_video:
                     sent_msg = await client.send_file(DESTINATION, path_to_video, caption=news_text, parse_mode='html')
        elif image_prompt:
            path_to_image = await generate_image(image_prompt)
            if path_to_image and os.path.exists(path_to_image):
                sent_msg = await client.send_file(DESTINATION, path_to_image, caption=news_text, parse_mode='html')
            else:
                print("⚠️ Картинка не скачалась, отправляю текст")
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
        else:
            sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')

        if sent_msg:
            stats_db.increment('published')
            print(f"✅ Пост опубликован! ID: {sent_msg.id}")
            
            # --- СОХРАНЕНИЕ ИСТОРИИ (БЕЗ "СУТЬ:") ---
            essence = news_text
            # Ищем текст внутри <blockquote> </blockquote>
            if "<blockquote>" in news_text:
                try: 
                    # Берем то, что между тегами
                    essence = news_text.split("<blockquote>")[1].split("</blockquote>")[0].strip()
                except: pass
            save_to_history(essence)
            # ----------------------------------------
            
            if reaction:
                await asyncio.sleep(2)
                try:
                    await client(functions.messages.SendReactionRequest(
                        peer=DESTINATION,
                        msg_id=sent_msg.id,
                        reaction=[types.ReactionEmoji(emoticon=reaction)]
                    ))
                except: pass

            if poll_data:
                await asyncio.sleep(1)
                try:
                    await client.send_message(DESTINATION, file=types.InputMediaPoll(
                        poll=types.Poll(
                            id=1,
                            question=poll_data["q"],
                            answers=[types.PollAnswer(text=o, option=bytes([i])) for i, o in enumerate(poll_data["o"])]
                        )
                    ))
                except: pass
        else:
            print("⚠️ Ошибка: Пост не был отправлен")

    except Exception as e:
        print(f"❌ Критическая ошибка отправки: {e}")
        stats_db.increment('rejected_other')
    finally:
        if path_to_image and os.path.exists(path_to_image):
            try: os.remove(path_to_image)
            except: pass
        if path_to_video and os.path.exists(path_to_video):
            try: os.remove(path_to_video)
            except: pass

if __name__ == '__main__':
    print("🚀 Старт...")
    if not os.path.exists('/data'):
        try: os.makedirs('/data', exist_ok=True)
        except: pass

    if client:
        client.start()
        
        # Запускаем планировщики
        scheduler = AsyncIOScheduler(event_loop=client.loop)
        
        # 1. Подкаст (18:00)
        scheduler.add_job(send_evening_podcast, 'cron', hour=18, minute=0)
        
        # 2. Статистика (00:00)
        scheduler.add_job(send_daily_report, CronTrigger(hour=0, minute=0, timezone=pytz.timezone('Europe/Moscow')), args=[client])
        
        scheduler.start()
        
        print("🤖 Бот запущен! (ALL IN ONE VERSION)")
        client.run_until_disconnected()
