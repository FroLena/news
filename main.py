import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI

# Берем настройки из переменных Amvera
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

# Каналы, которые читаем (без @)
SOURCE_CHANNELS = ['rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon']
DESTINATION = 'me' # Кидать в Избранное

client = TelegramClient('amvera_session', API_ID, API_HASH)
gpt_client = OpenAI(api_key=OPENAI_KEY)

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
            model="gpt-4o-mini",
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
    if not text or len(text) < 50: return
    
    # Защита от дублей
    if text[:50] in processed_news: return
    processed_news.append(text[:50])
    if len(processed_news) > 100: processed_news.pop(0)

    print(f"Новость из {event.chat.username}")
    new_post = await rewrite_news(text)
    
    if new_post and "SKIP" not in new_post:
        await client.send_message(DESTINATION, f"{new_post}\n\nИсточник: {event.chat.title}")

print("Бот запущен!")
client.start()
client.run_until_disconnected()
