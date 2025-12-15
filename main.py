import os
import asyncio
import json
import httpx
import urllib.parse
from telethon import TelegramClient, events, types, functions
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import edge_tts

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

SOURCE_CHANNELS = [
    'rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon', 
    'shot_shot', 'ostorozhno_novosti', 'rbc_news'
]
DESTINATION = '@s_ostatok'

MAX_VIDEO_SIZE = 50 * 1024 * 1024 

# МОДЕЛЬ
AI_MODEL = "openai/gpt-4o-mini"

# 2. Клиент Телеграм
client = TelegramClient('amvera_session', API_ID, API_HASH)
raw_text_cache = []
published_topics = [] 

# --- ПРЯМОЙ ЗАПРОС К GPT ---
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
                else:
                    print(f"⚠️ OpenAI Error ({response.status_code})")
            except: pass
            await asyncio.sleep(5)
            
    print("❌ Не удалось получить ответ от GPT.")
    return None

# --- ГЕНЕРАЦИЯ КАРТИНКИ ---
async def generate_image(prompt_text):
    # Чистим от возможных остатков разметки
    clean_prompt = prompt_text.replace('|||', '').strip()
    # Убираем технические заголовки, если ИИ вдруг их оставил
    clean_prompt = clean_prompt.replace('=== ПРОМПТ ДЛЯ КАРТИНКИ (English) ===', '').strip()
    
    print(f"🎨 Рисую (Flux): {clean_prompt[:60]}...")
    
    encoded_prompt = urllib.parse.quote(clean_prompt)
    import random
    seed = random.randint(1, 1000000)
    # Используем Flux Realism для лучшего качества
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&model=flux-realism&seed={seed}&nologo=true"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"}

    for attempt in range(3):
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http_client:
            try:
                response = await http_client.get(url, headers=headers)
                if response.status_code == 200:
                    filename = f"image_{seed}.jpg"
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
        await communicate.save("podcast.mp3")
            
        await client.send_file(DESTINATION, "podcast.mp3", caption="🎙 <b>Итоги дня</b>", parse_mode='html', voice_note=True)
        if os.path.exists("podcast.mp3"): os.remove("podcast.mp3")
    except Exception as e:
        print(f"❌ Ошибка подкаста: {e}")

# --- AI РЕДАКТОР (НОВЫЙ ЖЕСТКИЙ ВИЗУАЛЬНЫЙ ПРОМПТ) ---
async def rewrite_news(text, history_topics):
    history_str = "\n".join([f"- {t}" for t in history_topics[-15:]]) if history_topics else "Нет истории."

    system_prompt = (
        f"Ты — главный редактор канала 'Сухой остаток'.\n"
        f"ИСТОРИЯ: {history_str}\n\n"
        f"=== ЧАСТЬ 1: РАБОТА С ТЕКСТОМ ===\n"
        f"1. 🚨 РЕКЛАМА -> ВЕРНИ: SKIP (Если есть 'Реклама', 'erid', продажа).\n"
        f"2. 🔄 ДУБЛИ -> ВЕРНИ: DUPLICATE (Если событие уже было).\n"
        f"3. ✍️ РЕРАЙТ (Русский язык):\n"
        f"   - Инфостиль, без воды, без 'мы'/'нам'.\n"
        f"   - Заголовок: Жирный, яркий. Цитаты: в косвенную речь.\n"
        f"   - Структура: Реакция -> Заголовок -> Текст -> Суть -> Опрос (если нужен).\n"
        f"   - Реакции: ||R:🔥|| (срочно), ||R:🤡|| (смешно), ||R:😢|| (грустно), ||R:⚡️|| (шок), ||R:👍|| (позитив).\n\n"
        f"=== ЧАСТЬ 2: ГЕНЕРАЦИЯ ПРОМПТА ДЛЯ КАРТИНКИ (English) ===\n"
        f"Твоя задача — превратить текст новости в визуальную сцену.\n"
        f"1. АНАЛИЗ: Выдели главные визуальные объекты (кто? где? что делают?).\n"
        f"2. ПЕРЕВОД: Опиши эту сцену на английском языке для генератора картинок.\n"
        f"3. ПРАВИЛА ОПИСАНИЯ:\n"
        f"   - ТОЛЬКО физические объекты. Никаких абстракций ('политические разногласия', 'кризис'). Если новость про политику — рисуй здание парламента или политика за трибуной. Если про панд — рисуй панд в зоопарке.\n"
        f"   - Детализируй: время суток, погода, освещение, эмоции людей на фото.\n"
        f"4. СТИЛЬ (Добавляй в конец каждого промпта):\n"
        f"   '... Documentary photograph, highly detailed, realistic film grain, cinematic lighting, shot on Canon 5D, 4k journalism.'\n\n"
        f"ФОРМАТ ВЫВОДА (СТРОГО):\n"
        f"[Текст новости с разметкой]\n"
        f"|||\n"
        f"[Твой подробный промпт на английском]"
    )

    return await ask_gpt_direct(system_prompt, text)

@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handler(event):
    text = event.message.message
    if not text: text = "" 
    if len(text) < 20: return

    short_hash = text[:100]
    if short_hash in raw_text_cache: return
    raw_text_cache.append(short_hash)
    if len(raw_text_cache) > 100: raw_text_cache.pop(0)

    try:
        chat = await event.get_chat()
        source_name = chat.title
    except:
        source_name = "Неизвестный канал"
    
    print(f"🔎 Обработка новости из: {source_name}")
    
    full_response = await rewrite_news(text, published_topics)
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
        if len(parts) > 1 and parts[1].strip():
            image_prompt = parts[1].strip()
    else:
        # Если разделителя нет, считаем всё текстом и не генерируем картинку
        news_text = full_response.strip()

    # Чистка от возможных технических заголовков, если они все же просочились
    news_text = news_text.replace("=== ЧАСТЬ 1: РАБОТА С ТЕКСТОМ ===", "").strip()

    reaction = None
    if "||R:" in news_text:
        try:
            parts = news_text.split("||R:")
            subparts = parts[1].split("||")
            reaction = subparts[0].strip()
            news_text = subparts[1].strip()
            print(f"😎 Реакция: {reaction}")
        except: pass

    poll_data = None
    if "||POLL||" in news_text:
        try:
            p = news_text.split("||POLL||")
            news_text = p[0].strip()
            raw_poll = p[1].strip().split('\n')
            poll_lines = [line.strip() for line in raw_poll if line.strip()]
            if len(poll_lines) >= 3:
                poll_data = {"q": poll_lines[0], "o": poll_lines[1:]}
        except: pass

    # Fallback: если ИИ не дал промпт, но в оригинале было фото - пробуем авто-промпт
    if not image_prompt and event.message.photo:
        print("⚠️ ИИ не дал промпт, генерирую авто-промпт по контексту...")
        base_prompt = news_text.replace('\n', ' ')[:200]
        image_prompt = f"Documentary photograph capturing the scene described: {base_prompt}. Realistic film grain, cinematic lighting, 4k journalism."

    # --- ОТПРАВКА ---
    path_to_image = None
    sent_msg = None
    try:
        has_video = event.message.video is not None
        
        if has_video:
            if event.message.file.size > MAX_VIDEO_SIZE:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                path = await event.download_media()
                sent_msg = await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html', supports_streaming=True)
                os.remove(path)
        
        elif image_prompt:
            path_to_image = await generate_image(image_prompt)
            if path_to_image and os.path.exists(path_to_image):
                sent_msg = await client.send_file(DESTINATION, path_to_image, caption=news_text, parse_mode='html')
            else:
                # Если картинка не сгенерировалась, шлем просто текст
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
                poll_media = types.InputMediaPoll(
                    poll=types.Poll(
                        id=1, 
                        question=poll_data["q"], 
                        answers=[types.PollAnswer(text=o, option=bytes([i])) for i, o in enumerate(poll_data["o"])]
                    )
                )
                await client.send_message(DESTINATION, file=poll_media)
            except: pass

        print("✅ Пост готов!")
        
        essence = news_text
        if "📌 Суть:" in news_text:
            try: essence = news_text.split("📌 Суть:")[1].replace("</blockquote>", "").strip()
            except: pass
        
        published_topics.append(essence[:200])
        if len(published_topics) > 15: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        if path_to_image and os.path.exists(path_to_image):
            os.remove(path_to_image)

if __name__ == '__main__':
    print("🚀 Старт...")
    client.start()
    scheduler = AsyncIOScheduler(event_loop=client.loop)
    scheduler.add_job(send_evening_podcast, 'cron', hour=18, minute=0)
    scheduler.start()
    print("🤖 Бот запущен! (Visual Director Mode)")
    client.run_until_disconnected()
