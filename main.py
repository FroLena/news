import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI

# --- ПОЛУЧАЕМ КЛЮЧИ ИЗ НАСТРОЕК СЕРВЕРА ---
# На Amvera мы пропишем их в разделе "Переменные"
API_ID = int(os.getenv('TG_API_ID'))       
API_HASH = os.getenv('TG_API_HASH')
OPENAI_KEY = os.getenv('OPENAI_API_KEY')

# Настройки каналов
# Список каналов-доноров (без @)
SOURCE_CHANNELS = ['rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon']
# Куда кидать готовое (me - это Избранное, или ID твоего админского чата)
DESTINATION = 'me' 

# Инициализация клиентов
client = TelegramClient('amvera_session', API_ID, API_HASH)
gpt_client = OpenAI(api_key=OPENAI_KEY)

# Кэш, чтобы не дублировать новости
processed_news = []

async def rewrite_news(text):
    """Стучимся в GPT для рерайта"""
    system_prompt = (
        "Ты — редактор Telegram-канала «Сухой остаток». Твоя задача — сокращать новости. "
        "Стиль: предельно сухой, деловой, факты и цифры. Никакой воды. "
        "Структура поста:\n"
        "1. Заголовок с подходящим эмодзи\n"
        "2. Суть новости в 2-3 предложениях.\n"
        "3. Цитата-вывод в формате: '> 📌 Суть: ...'\n"
        "Если текст похож на рекламу, розыгрыш или спам — ответь одним словом SKIP."
    )
    
    try:
        response = gpt_client.chat.completions.create(
            model="gpt-4o-mini", # Используем модель подешевле и быстрее
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Перепиши эту новость:\n{text}"}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Ошибка AI: {e}")
        return None

@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handler(event):
    original_text = event.message.message
    
    if not original_text or len(original_text) < 50:
        return

    # Защита от дублей
    news_id = original_text[:50]
    if news_id in processed_news:
        return
    processed_news.append(news_id)
    if len(processed_news) > 100: processed_news.pop(0)

    print(f"Обрабатываю новость из {event.chat.username}...")
    new_post = await rewrite_news(original_text)

    if not new_post or "SKIP" in new_post:
        return

    final_message = f"{new_post}\n\n__Источник: {event.chat.title}__"
    
    # Отправляем в Избранное
    await client.send_message(DESTINATION, final_message)

print("Бот запущен на сервере Amvera!")
client.start()
client.run_until_disconnected()
