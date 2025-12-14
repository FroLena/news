import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI

# 1. Получаем настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

# Настройки каналов (без @)
SOURCE_CHANNELS = ['rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon']
DESTINATION = 'me' 

# 2. Настраиваем подключение к нейросети (Умный выбор)
# Если ключ начинается на sk-or, значит это OpenRouter
if OPENAI_KEY.startswith("sk-or-"):
    print("Использую настройки OpenRouter...")
    gpt_client = OpenAI(
        api_key=OPENAI_KEY,
        base_url="https://openrouter.ai/api/v1"
    )
    # Для OpenRouter имя модели обычно с префиксом
    AI_MODEL = "openai/gpt-4o-mini"
else:
    print("Использую официальный OpenAI...")
    gpt_client = OpenAI(api_key=OPENAI_KEY)
    AI_MODEL = "gpt-4o-mini"

# 3. Запускаем Телеграм
client = TelegramClient('amvera_session', API_ID, API_HASH)

# Кэш для защиты от дублей
processed_news = []

async def rewrite_news(text):
    system_prompt = (
        "Ты — редактор канала «Сухой остаток». Сократи новость. "
        "Стиль: сухой, факты, без воды. "
        "В конце добавь вывод: '> 📌 Суть: ...'. "
        "Если реклама — верни SKIP."
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
    # Фильтр коротких сообщений и дублей
    if not text or len(text) < 50: return
    
    if text[:50] in processed_news: return
    processed_news.append(text[:50])
    if len(processed_news) > 100: processed_news.pop(0)

    print(f"Новость из {event.chat.username}")
    
    new_post = await rewrite_news(text)
    
    if new_post and "SKIP" not in new_post:
        # Отправляем готовую новость
        await client.send_message(DESTINATION, f"{new_post}\n\nИсточник: {event.chat.title}")
        print("✅ Пост отправлен!")

print("Бот запущен! Жду новостей...")
client.start()
client.run_until_disconnected()
