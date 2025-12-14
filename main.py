import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')
# Clipdrop убираем, раз он не работает

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

# Кэши
raw_text_cache = []
published_topics = []

async def rewrite_news(text, history_topics):
    recent_history = history_topics[-5:] 
    history_str = "\n".join([f"- {t}" for t in recent_history])
    
    # ОТЛАДКА
    if recent_history:
        print(f"🧐 Сравниваю с темами:\n{history_str}")

    system_prompt = (
        f"Ты — профессиональный новостник. \n"
        f"Вот темы, которые мы УЖЕ публиковали:\n{history_str}\n\n"
        f"СТРОГАЯ ИНСТРУКЦИЯ:\n"
        f"1. Сравнивай ФАКТЫ (Локация, Имена). Если событие то же самое — верни DUPLICATE.\n"
        f"2. Если подробности новые — ЭТО НЕ ДУБЛЬ! Пиши новость.\n"
        f"3. Если реклама — верни SKIP.\n\n"
        f"Если пишешь новость, используй HTML:\n"
        f"Текст новости.\n"
        f"<blockquote><b>📌 Суть:</b> [вывод]</blockquote>"
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
    
    # Пропускаем совсем короткое, если нет фото
    if len(text) < 15 and not event.message.photo: return

    if text:
        short_hash = text[:100]
        if short_hash in raw_text_cache: return
        raw_text_cache.append(short_hash)
        if len(raw_text_cache) > 100: raw_text_cache.pop(0)

    print(f"🔎 Новое сообщение из {event.chat.username}")
    
    if len(text) < 10:
        result = "<blockquote><b>📌 Фотофакт</b></blockquote>"
    else:
        result = await rewrite_news(text, published_topics)
    
    if not result: return

    if "DUPLICATE" in result:
        print("❌ AI считает это дублем.")
        return
    if "SKIP" in result:
        print("🗑 AI считает это рекламой.")
        return

    # --- РАБОТА С ФОТО (Стабильная версия) ---
    path = None
    try:
        if event.message.photo:
            print("📸 Качаю фото (публикуем оригинал)...")
            path = await event.download_media()
    
        if path:
            # Отправляем оригинал фото
            await client.send_file(DESTINATION, path, caption=result, parse_mode='html')
        else:
            await client.send_message(DESTINATION, result, parse_mode='html')
        
        print("✅ Пост опубликован!")
        
        summary = result[:80].replace('\n', ' ')
        published_topics.append(summary)
        if len(published_topics) > 10: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        # Убираем за собой
        if path and os.path.exists(path):
            os.remove(path)

print("Бот запущен! (Режим: Стабильный, Оригиналы фото)")
client.start()
client.run_until_disconnected()
