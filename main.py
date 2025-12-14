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
    # Если истории почти нет, не напрягаем ИИ проверкой, просто пишем
    if len(history_topics) < 1:
        print("🆕 История пуста, пропускаем проверку на дубли.")
        check_duplicates = False
        history_str = "Нет истории."
    else:
        check_duplicates = True
        recent_history = history_topics[-5:] 
        history_str = "\n".join([f"- {t}" for t in recent_history])
        print(f"🧐 Сравниваю с:\n{history_str}")

    # ИНСТРУКЦИЯ СТАЛА МЯГЧЕ
    system_prompt = (
        f"Ты — редактор новостей. \n"
        f"История опубликованного:\n{history_str}\n\n"
        f"ИНСТРУКЦИЯ:\n"
        f"1. СРАВНЕНИЕ (Только если история не пуста): \n"
        f"   - Блокируй (верни DUPLICATE) ТОЛЬКО если это АБСОЛЮТНО то же самое событие (те же цифры, те же имена).\n"
        f"   - Если это развитие темы, новые подробности или просто похожая тема — ЭТО НЕ ДУБЛЬ! Пиши новость.\n"
        f"   - Если сомневаешься — ПИШИ НОВОСТЬ.\n"
        f"2. РЕКЛАМА: Если пост продает курсы, товары или просит подписаться — верни SKIP.\n"
        f"3. ОФОРМЛЕНИЕ (HTML):\n"
        f"   Текст новости.\n"
        f"   <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"4. ОПРОС: Если тема вызывает эмоции, добавь в конце:\n"
        f"   ||POLL||\n"
        f"   Вопрос?\n"
        f"   Вариант 1\n"
        f"   Вариант 2"
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

    # Быстрый фильтр (точное совпадение текста)
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

    # Логика блокировки
    if "DUPLICATE" in full_response:
        # Мы теперь видим в логах, что именно он ответил (иногда он пишет DUPLICATE: причина)
        print(f"❌ ИИ решил, что это дубль. Ответ ИИ: {full_response[:50]}...")
        return
    if "SKIP" in full_response:
        print("🗑 Реклама.")
        return

    # --- ПАРСИНГ ---
    news_text = full_response
    poll_data = None
    
    if "||POLL||" in full_response:
        try:
            parts = full_response.split("||POLL||")
            news_text = parts[0].strip()
            poll_lines = parts[1].strip().split('\n')
            if len(poll_lines) >= 3:
                poll_data = {"q": poll_lines[0], "o": [opt for opt in poll_lines[1:] if opt.strip()]}
        except:
            pass # Если опрос кривой, постим только текст

    # --- ОТПРАВКА ---
    path = None
    try:
        if event.message.photo:
            path = await event.download_media()
            await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html')
        else:
            await client.send_message(DESTINATION, news_text, parse_mode='html')
        
        if poll_data:
            await asyncio.sleep(1)
            await client.send_poll(DESTINATION, question=poll_data["q"], options=poll_data["o"])

        print("✅ Пост опубликован!")
        
        # Сохраняем в историю очищенный от HTML текст (чтобы ИИ было проще сравнивать)
        clean_summary = news_text.replace('<blockquote>', '').replace('</blockquote>', '').replace('<b>', '').replace('</b>', '')[:100]
        published_topics.append(clean_summary)
        if len(published_topics) > 10: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        if path and os.path.exists(path):
            os.remove(path)

print("Бот запущен! (Режим: МЯГКИЙ ФИЛЬТР)")
client.start()
client.run_until_disconnected()
