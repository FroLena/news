import os
import asyncio
import json
import httpx
import urllib.parse
import time
from telethon import TelegramClient, events, types, functions
from telethon.sessions import StringSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import edge_tts

# 1. Настройки из переменных окружения
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')
SESSION_STRING = os.environ.get('TG_SESSION_STR')

SOURCE_CHANNELS = [
    'rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon', 
    'shot_shot', 'ostorozhno_novosti', 'rbc_news'
]
DESTINATION = '@s_ostatok'

# --- НАСТРОЙКА ПУТЕЙ (Fix Persistence) ---
# Если мы на сервере, пишем всё в /data. Если локально — в текущую папку.
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

# 2. Инициализация клиента (StringSession)
if not SESSION_STRING:
    print("❌ ОШИБКА: Не найдена переменная TG_SESSION_STR!")
    exit(1)

try:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
except Exception as e:
    print(f"❌ Ошибка инициализации клиента: {e}")
    client = None

raw_text_cache = []

# --- ФУНКЦИИ ИСТОРИИ ---
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            current_time = time.time()
            fresh_data = [item for item in data if current_time - item['timestamp'] < 86400]
            return fresh_data
    except Exception as e:
        print(f"⚠️ Ошибка чтения истории: {e}")
        return []

def save_to_history(text_essence):
    history = load_history()
    history.append({
        'text': text_essence,
        'timestamp': time.time()
    })
    if len(history) > 50:
        history = history[-50:]
    
    try:
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"❌ Ошибка записи истории в {HISTORY_FILE}: {e}")

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
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text}
        ]
    }

    for attempt in range(3):
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            try:
                response = await http_client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    data = response.json()
                    return data['choices'][0]['message']['content']
            except: pass
            await asyncio.sleep(5)
    return None

# --- ГЕНЕРАЦИЯ КАРТИНКИ ---
async def generate_image(prompt_text):
    clean_prompt = prompt_text.replace('|||', '').strip()
    clean_prompt = clean_prompt.replace('=== ПРОМПТ ===', '').strip()
    
    encoded_prompt = urllib.parse.quote(clean_prompt)
    import random
    seed = random.randint(1, 1000000)
    
    # Файл сохраняем в правильную папку BASE_DIR
    filename = os.path.join(BASE_DIR, f"image_{seed}.jpg")
    
    # Используем модель flux-realism без логотипа
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&model=flux-realism&seed={seed}&nologo=true"
    headers = {"User-Agent": "Mozilla/5.0"}

    for attempt in range(3):
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http_client:
            try:
                response = await http_client.get(url, headers=headers)
                if response.status_code == 200:
                    with open(filename, "wb") as f:
                        f.write(response.content)
                    return filename
            except: pass
            await asyncio.sleep(2)
    return None

# --- ПОДКАСТ ---
async def send_evening_podcast():
    print("🎙 Готовлю подкаст...")
    try:
        history_posts = []
        async for message in client.iter_messages(DESTINATION, limit=30):
            if message.text: history_posts.append(message.text)
        
        if not history_posts: return
        full_text = "\n\n".join(history_posts[:20])

        system_prompt = (
            "Ты — ведущий шоу «Сухой остаток». Создай сценарий подкаста на 60 секунд.\n"
            "Стиль: Живой, ироничный. Без сложных цифр."
        )
        script = await ask_gpt_direct(system_prompt, full_text)
        if not script: return

        script = script.replace('*', '').replace('#', '')
        communicate = edge_tts.Communicate(script, "ru-RU-DmitryNeural")
        
        await communicate.save(PODCAST_FILE)
        await client.send_file(DESTINATION, PODCAST_FILE, caption="🎙 <b>Итоги дня</b>", parse_mode='html', voice_note=True)
        
        if os.path.exists(PODCAST_FILE): os.remove(PODCAST_FILE)
    except Exception as e:
        print(f"❌ Ошибка подкаста: {e}")

# --- AI РЕДАКТОР (С ОБНОВЛЕННЫМ СТИЛЕМ IMAX) ---
async def rewrite_news(text):
    history_items = load_history()
    recent_history = history_items[-15:]
    history_str = "\n".join([f"- {item['text']}" for item in recent_history]) if recent_history else "История пуста."

    system_prompt = (
        f"Ты — главный редактор канала 'Сухой остаток'.\n"
        f"СПИСОК ОПУБЛИКОВАННЫХ СОБЫТИЙ (ЗА 24 ЧАСА):\n{history_str}\n\n"
        f"ЧАСТЬ 1. ПРАВИЛА ФИЛЬТРАЦИИ:\n"
        f"1. РЕКЛАМА -> ВЕРНИ: SKIP (Любые продажи, 'erid', 'партнерский материал').\n"
        f"2. ДУБЛИ -> ВЕРНИ: DUPLICATE (Если событие уже было в списке выше).\n\n"
        f"ЧАСТЬ 2. ПРАВИЛА ТЕКСТА (Русский, HTML):\n"
        f"- Используй <b>жирный</b>. Markdown (**) НЕЛЬЗЯ.\n"
        f"- Инфостиль. Структура: Реакция -> Заголовок -> Текст -> Суть -> Опрос.\n\n"
        f"ЧАСТЬ 3. ПРАВИЛА КАРТИНКИ (English, IMAX Quality):\n"
        f"- Твоя задача: Описать сцену как дорогую документальную фотографию высокого разрешения.\n"
        f"- Описывай ТОЛЬКО физические объекты, время суток, погоду.\n"
        f"- ЗАПРЕТ НА СЛОВА: 'grain', 'film grain', 'cinematic lighting', 'dramatic', 'blur'.\n"
        f"- ВМЕСТО ЭТОГО ПИШИ: 'Sharp focus', 'Natural daylight', 'Highly detailed', 'Realistic textures'.\n"
        f"- КРИМИНАЛ: Рисуй 'Police tape, emergency vehicle lights, building exterior'. Без насилия.\n\n"
        f"=== ШАБЛОН ОТВЕТА ===\n"
        f"||R:🔥|| <b>Заголовок</b>\n"
        f"\n"
        f"Текст новости.\n"
        f"<blockquote><b>📌 Суть:</b> Вывод.</blockquote>\n"
        f"||POLL||\n"
        f"Вопрос?\n"
        f"Вариант 1\n"
        f"Вариант 2\n"
        f"|||\n"
        f"A detailed documentary photograph shot on IMAX camera: [Описание сцены]. Natural daylight, sharp focus, highly detailed textures, realistic colors, 8k resolution."
    )
    return await ask_gpt_direct(system_prompt, text)

@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handler(event):
    text = event.message.message
    if not text or len(text) < 20: return

    short_hash = text[:100]
    if short_hash in raw_text_cache: return
    raw_text_cache.append(short_hash)
    if len(raw_text_cache) > 100: raw_text_cache.pop(0)

    try:
        chat = await event.get_chat()
        print(f"🔎 Обработка новости из: {chat.title}")
    except: pass
    
    full_response = await rewrite_news(text)
    if not full_response: return

    if "DUPLICATE" in full_response: 
        print(f"❌ Отсечен дубль")
        return
    if "SKIP" in full_response: 
        print(f"🗑 Отсечена реклама/мусор")
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
        # Обновил авто-промпт под новый стиль
        base_prompt = news_text.replace('\n', ' ')[:200]
        image_prompt = f"A detailed documentary photograph shot on IMAX camera: {base_prompt}. Natural daylight, sharp focus, highly detailed textures, realistic colors, 8k resolution."

    path_to_image = None
    sent_msg = None
    try:
        has_video = event.message.video is not None
        if has_video:
            if event.message.file.size > MAX_VIDEO_SIZE:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                path = await event.download_media()
                sent_msg = await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html')
                os.remove(path)
        elif image_prompt:
            path_to_image = await generate_image(image_prompt)
            if path_to_image and os.path.exists(path_to_image):
                sent_msg = await client.send_file(DESTINATION, path_to_image, caption=news_text, parse_mode='html')
            else:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
        else:
            sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')

        if sent_msg and reaction:
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

        print("✅ Пост готов!")
        
        essence = news_text
        if "📌 Суть:" in news_text:
            try: essence = news_text.split("📌 Суть:")[1].replace("</blockquote>", "").strip()
            except: pass
        
        save_to_history(essence)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        if path_to_image and os.path.exists(path_to_image):
            os.remove(path_to_image)

if __name__ == '__main__':
    print("🚀 Старт...")
    if not os.path.exists('/data'):
        try: os.makedirs('/data', exist_ok=True)
        except: pass

    if client:
        client.start()
        scheduler = AsyncIOScheduler(event_loop=client.loop)
        scheduler.add_job(send_evening_podcast, 'cron', hour=18, minute=0)
        scheduler.start()
        print("🤖 Бот запущен! (IMAX Visual Style)")
        client.run_until_disconnected()
