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

# --- ГЕНЕРАЦИЯ КАРТИНКИ (Realism Update) ---
async def generate_image(prompt_text):
    clean_prompt = prompt_text.replace('||', '').replace('R:', '').strip()
    print(f"🎨 Рисую (Flux): {clean_prompt[:50]}...")
    
    encoded_prompt = urllib.parse.quote(clean_prompt)
    import random
    seed = random.randint(1, 1000000)
    # nologo=true, model=flux-realism (если доступна) или flux
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&model=flux&seed={seed}&nologo=true"
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

# --- AI РЕДАКТОР (IRON CONSTITUTION) ---
async def rewrite_news(text, history_topics):
    history_str = "\n".join([f"- {t}" for t in history_topics[-15:]]) if history_topics else "Нет истории."

    # === ЖЕЛЕЗНЫЙ ПРОМПТ ===
    system_prompt = (
        f"Ты — главный редактор новостного канала 'Сухой остаток'.\n\n"
        f"ИСТОРИЯ (Уже было): {history_str}\n\n"
        f"ТВОЙ ЗАКОН (Инструкция):\n"
        f"1. ПРОВЕРКА НА ДУБЛИ:\n"
        f"   - Если событие уже есть в Истории (даже с новыми мелкими деталями) -> ВЕРНИ: DUPLICATE\n"
        f"   - Если появились ВАЖНЫЕ подробности (число жертв, причина, заявление властей) -> ЭТО НОВАЯ НОВОСТЬ.\n"
        f"2. ПРОВЕРКА НА СПАМ:\n"
        f"   - Игнорируй приписки 'Подпишись на канал', 'Ставь лайк'. Это НЕ спам.\n"
        f"   - Если ВЕСЬ текст — реклама, продажа, другой канал -> ВЕРНИ: SKIP\n"
        f"3. ОБРАБОТКА ТЕКСТА (СТРОГО РУССКИЙ ЯЗЫК):\n"
        f"   - Переведи, если источник иностранный.\n"
        f"   - СТИЛЬ: Инфостиль. Убирай воду. Сокращай текст на 30-50% без потери смысла.\n"
        f"   - ЗАГОЛОВОК: Телеграм-стиль (Яркий, но честный). Жирный шрифт. Без слова 'Заголовок'.\n"
        f"   - ЦИТАТЫ: Переводи прямую речь в косвенную ('Он заявил, что...').\n"
        f"   - ТОН: Обезличенный (Без 'мы', 'нам').\n"
        f"4. СТРУКТУРА ОТВЕТА:\n"
        f"   ||R:🔥|| <b>Заголовок</b>\n"
        f"   (Пустая строка)\n"
        f"   Текст новости (2-3 предложения).\n"
        f"   <blockquote><b>📌 Суть:</b> [Вывод одним предложением]</blockquote>\n"
        f"   (Если новость острая) ||POLL||\n"
        f"   Вопрос?\n"
        f"   Вариант 1\n"
        f"   Вариант 2\n\n"
        f"=== ЧАСТЬ 2: ПРОМПТ КАРТИНКИ (English) ===\n"
        f"- Description of the scene.\n"
        f"- Keywords for realism: 'Raw candid photo, film grain, shot on Canon 5D, 4k news footage, journalism, realistic lighting, dirty realism, no CGI, no 3D render'.\n"
        f"- NO TEXT on image.\n"
        f"ФОРМАТ ВЫВОДА СТРОГО: ТЕКСТ ||| ПРОМПТ_КАРТИНКИ"
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
        print(f"🗑 Отсечен мусор")
        return

    # --- ПАРСИНГ ---
    raw_text = full_response
    image_prompt = None
    
    if "|||" in raw_text:
        parts = raw_text.split("|||")
        news_text = parts[0].strip()
        image_prompt = parts[1].strip()
    else:
        news_text = raw_text.strip()

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

    if not image_prompt and event.message.photo:
        print("⚠️ Генерирую авто-промпт...")
        base_prompt = news_text.replace('\n', ' ')[:150]
        image_prompt = f"Raw photo, journalism style, realistic lighting, 4k. Context: {base_prompt}"

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
    print("🤖 Бот запущен! (IRON PROMPT)")
    client.run_until_disconnected()
