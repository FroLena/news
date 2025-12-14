import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

SOURCE_CHANNELS = ['rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon']
DESTINATION = '@s_ostatok' # <--- ТВОЙ ЮЗЕРНЕЙМ

# 2. OpenAI
if OPENAI_KEY.startswith("sk-or-"):
    print("Использую OpenRouter...")
    gpt_client = OpenAI(api_key=OPENAI_KEY, base_url="https://openrouter.ai/api/v1")
    AI_MODEL = "openai/gpt-4o-mini"
else:
    print("Использую OpenAI...")
    gpt_client = OpenAI(api_key=OPENAI_KEY)
    AI_MODEL = "gpt-4o-mini"

# 3. Клиент
client = TelegramClient('amvera_session', API_ID, API_HASH)

raw_text_cache = []
published_topics = []

async def rewrite_news(text, history_topics):
    recent_history = history_topics[-5:] 
    history_str = "\n".join([f"- {t}" for t in recent_history])
    
    # Промпт стал сложнее. Мы учим его отделять опрос спецсимволами ||POLL||
    system_prompt = (
        f"Ты — редактор канала. История тем:\n{history_str}\n\n"
        f"ИНСТРУКЦИЯ:\n"
        f"1. Если это дубль — верни DUPLICATE. Если реклама — SKIP.\n"
        f"2. Сократи новость (HTML). В конце: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"3. ИНТЕРАКТИВ: Если новость острая/спорная/социальная — придумай опрос.\n"
        f"   Формат добавления опроса (в самом конце текста):\n"
        f"   ||POLL||\n"
        f"   Вопрос опроса?\n"
        f"   Ответ 1\n"
        f"   Ответ 2\n"
        f"   Ответ 3\n"
        f"   (Максимум 3 варианта, коротко и с эмодзи).\n"
        f"4. Если новость скучная (погода, курсы валют) — НЕ добавляй ||POLL||."
    )

    try:
        response = gpt_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Ошибка AI: {e}")
        return None

@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handler(event):
    text = event.message.message
    if not text: text = "" 
    if len(text) < 15 and not event.message.photo: return

    if text:
        short_hash = text[:100]
        if short_hash in raw_text_cache: return
        raw_text_cache.append(short_hash)
        if len(raw_text_cache) > 100: raw_text_cache.pop(0)

    print(f"🔎 Обработка: {event.chat.username}")
    
    if len(text) < 10:
        full_response = "<blockquote><b>📌 Фотофакт</b></blockquote>"
    else:
        full_response = await rewrite_news(text, published_topics)
    
    if not full_response: return
    if "DUPLICATE" in full_response:
        print("❌ Дубль")
        return
    if "SKIP" in full_response:
        print("🗑 Реклама")
        return

    # --- ПАРСИНГ ОПРОСА ---
    # Разделяем текст новости и данные опроса по нашему секретному разделителю
    news_text = full_response
    poll_data = None
    
    if "||POLL||" in full_response:
        parts = full_response.split("||POLL||")
        news_text = parts[0].strip() # Чистый текст новости
        
        # Разбираем опрос (строки после разделителя)
        poll_lines = parts[1].strip().split('\n')
        if len(poll_lines) >= 3: # Должен быть вопрос и хотя бы 2 ответа
            poll_question = poll_lines[0]
            poll_options = [opt for opt in poll_lines[1:] if opt.strip()]
            if len(poll_options) > 1:
                poll_data = {"q": poll_question, "o": poll_options}
                print("📊 Найден опрос!")

    # --- ОТПРАВКА ---
    path = None
    try:
        # 1. Отправляем саму новость (с фото или без)
        if event.message.photo:
            path = await event.download_media()
            await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html')
        else:
            await client.send_message(DESTINATION, news_text, parse_mode='html')
        
        # 2. Если есть опрос — кидаем его следом
        if poll_data:
            await asyncio.sleep(1) # Пауза 1 сек для красоты
            await client.send_poll(
                DESTINATION,
                question=poll_data["q"],
                options=poll_data["o"]
            )
            print("📊 Опрос опубликован")

        print("✅ Пост готов!")
        
        summary = news_text[:80].replace('\n', ' ')
        published_topics.append(summary)
        if len(published_topics) > 10: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        if path and os.path.exists(path):
            os.remove(path)

print("Бот запущен! (Режим: HTML + Анти-дубль + УМНЫЕ ОПРОСЫ)")
client.start()
client.run_until_disconnected()
