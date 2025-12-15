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

# --- ИМПОРТЫ СТАТИСТИКИ ---
from stats import stats_db
from scheduler import start_scheduler

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
    print("Проверьте 'Переменные' (Environment Variables) в панели управления хостинга!")
    time.sleep(30) # Даем время прочитать лог перед падением
    exit(1)

SOURCE_CHANNELS = [
    'rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon', 
    'shot_shot', 'ostorozhno_novosti', 'rbc_news'
]
DESTINATION = '@s_ostatok'

# --- ПУТИ (Fix Persistence) ---
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

# 2. Клиент
if not SESSION_STRING:
    print("❌ ОШИБКА: Не найдена переменная TG_SESSION_STR!")
    exit(1)

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

# --- ГЕНЕРАЦИЯ КАРТИНКИ ---
async def generate_image(prompt_text):
    clean_prompt = prompt_text.replace('|||', '').replace('=== ПРОМПТ ===', '').strip()
    
    # Жесткий суффикс для резкости
    tech_suffix = " . Shot on Phase One XF IQ4, 150MP, ISO 100, f/8, crystal clear, sharp focus, professional stock photography, no grain, no blur, bright lighting."
    final_prompt = clean_prompt + tech_suffix
    
    encoded_prompt = urllib.parse.quote(final_prompt)
    import random
    seed = random.randint(1, 1000000)
    filename = os.path.join(BASE_DIR, f"image_{seed}.jpg")
    
    # Модель flux
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&model=flux&seed={seed}&nologo=true"
    
    for _ in range(3):
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http_client:
            try:
                response = await http_client.get(url)
                if response.status_code == 200:
                    with open(filename, "wb") as f: f.write(response.content)
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
            "Ты — профессиональный радиоведущий итогового шоу «Сухой остаток».\n"
            "Твоя задача: Создать увлекательный сценарий на основе предоставленных новостей за день.\n\n"
            "ТРЕБОВАНИЯ К ТЕКСТУ:\n"
            "1. СТРУКТУРА: Вступление -> Плавный рассказ (3-5 главных тем) -> Заключение.\n"
            "2. СТИЛЬ: Живой, разговорный, немного ироничный, но уверенный.\n"
            "3. АДАПТАЦИЯ ПОД ОЗВУЧКУ: Не используй сложные цифры, убери ссылки и спецсимволы.\n"
            "4. ХРОНОМЕТРАЖ: 60-90 секунд.\n\n"
            "НАЧАЛО: 'Добрый вечер. В эфире Сухой остаток. Подведем итоги этого дня.'\n"
            "КОНЕЦ: 'Таким был этот день. Оставайтесь с нами. До связи.'"
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

# --- AI РЕДАКТОР ---
async def rewrite_news(text):
    history_items = load_history()
    recent_history = history_items[-25:]
    history_str = "\n".join([f"- {item['text']}" for item in recent_history]) if recent_history else "История пуста."

    system_prompt = (
        f"Ты — циничный и строгий главный редактор канала 'Сухой остаток'.\n"
        f"Твоя задача: Выжимать факты из новостей, безжалостно убирая воду и канцелярщину.\n"
        f"СПИСОК ОПУБЛИКОВАННЫХ СОБЫТИЙ (ЧТОБЫ НЕ ПОВТОРЯТЬСЯ):\n{history_str}\n\n"
        
        f"=== ЧАСТЬ 1. ЖЕСТКИЙ ФИЛЬТР ===\n"
        f"1. РЕКЛАМА -> ВЕРНИ: SKIP\n"
        f"   (Любые продажи, 'erid', промокоды, ссылки на каналы, 'партнерский материал', курсы).\n"
        f"2. ДУБЛИ -> ВЕРНИ: DUPLICATE\n"
        f"   (Если новость об этом событии уже есть в списке выше).\n"
        f"3. МУСОР -> ВЕРНИ: SKIP\n"
        f"   (Пожелания доброго утра, размытые фото без контекста, поздравления с праздниками).\n\n"
        
        f"=== ЧАСТЬ 2. ПРАВИЛА ТЕКСТА (INFOSTYLE) ===\n"
        f"Язык: Русский. Формат: HTML.\n"
        f"1. ТЕГИ: Используй только <b>жирный</b>. Markdown (**) ЗАПРЕЩЕН.\n"
        f"2. СТИЛЬ: Инфостиль Максима Ильяхова. \n"
        f"   - ЗАПРЕЩЕНО: 'Сообщается', 'Стало известно', 'В сети появилось', 'Отметим, что'. Сразу к делу.\n"
        f"   - ЗАПРЕЩЕНО: Оценочные суждения ('Ужасная трагедия', 'Потрясающий успех'). Только факты.\n"
        f"3. ОБЪЕМ: Не более 600 знаков. Один плотный абзац + вывод.\n"
        f"4. СТРУКТУРА:\n"
        f"   - Реакция (Скрытый тег).\n"
        f"   - <b>Заголовок</b> (Хлесткий, 3-6 слов, без точки на конце).\n"
        f"   - Текст новости (Кто, что сделал, последствия).\n"
        f"   - <blockquote><b>📌 Суть:</b> (Короткий вывод или ирония редактора).</blockquote>\n"
        
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
        f"||R:🔥|| <b>Заголовок</b>\n"
        f"Текст...\n"
        f"<blockquote><b>📌 Суть:</b> Вывод.</blockquote>\n"
        f"|||\n"
        f"Prompt...\n\n"

        f"=== ШАБЛОН ОТВЕТА (С ОПРОСОМ) ===\n"
        f"||R:😱|| <b>Заголовок</b>\n"
        f"Текст...\n"
        f"<blockquote><b>📌 Суть:</b> Вывод.</blockquote>\n"
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
    # Инициализируем переменные, чтобы finally не упал
    path_to_image = None
    path_to_video = None
    
    text = event.message.message
    if not text or len(text) < 20: return

    short_hash = text[:100]
    if short_hash in raw_text_cache: return
    raw_text_cache.append(short_hash)
    if len(raw_text_cache) > 100: raw_text_cache.pop(0)

    stats_db.increment('scanned')

    try:
        chat = await event.get_chat()
        print(f"🔎 Обработка новости из: {chat.title}")
    except: pass
    
    full_response = await rewrite_news(text)
    
    if not full_response:
        stats_db.increment('rejected_other')
        print("❌ GPT вернул пустоту (см. ошибки выше)")
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
                path_to_video = await event.download_media() # Сохраняем путь к видео
                if path_to_video: # Проверка что видео скачалось
                     sent_msg = await client.send_file(DESTINATION, path_to_video, caption=news_text, parse_mode='html')
                
        elif image_prompt:
            path_to_image = await generate_image(image_prompt)
            if path_to_image and os.path.exists(path_to_image):
                sent_msg = await client.send_file(DESTINATION, path_to_image, caption=news_text, parse_mode='html')
            else:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
        else:
            sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')

        if sent_msg:
            stats_db.increment('published')
            print(f"✅ Пост опубликован! ID: {sent_msg.id} | Канал: {DESTINATION}")
            
            essence = news_text
            if "📌 Суть:" in news_text:
                try: essence = news_text.split("📌 Суть:")[1].replace("</blockquote>", "").strip()
                except: pass
            save_to_history(essence)
            
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
            print("⚠️ Ошибка: Пост не был отправлен (sent_msg is None)")

    except Exception as e:
        print(f"❌ Критическая ошибка отправки: {e}")
        stats_db.increment('rejected_other')
    finally:
        # Безопасное удаление всех временных файлов
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
        scheduler = AsyncIOScheduler(event_loop=client.loop)
        scheduler.add_job(send_evening_podcast, 'cron', hour=18, minute=0)
        scheduler.start()
        start_scheduler(client)
        print("🤖 Бот запущен! (CLEAN CODE + DEBUG MODE)")
        client.run_until_disconnected()
