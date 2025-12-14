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

# Куда отправляем (Твой канал)
DESTINATION = '@s_ostatok' # <--- ПРОВЕРЬ, ЧТО ТУТ ТВОЙ ЮЗЕРНЕЙМ

# 2. Настраиваем подключение к нейросети
if OPENAI_KEY.startswith("sk-or-"):
    print("Использую настройки OpenRouter...")
    gpt_client = OpenAI(
        api_key=OPENAI_KEY,
        base_url="https://openrouter.ai/api/v1"
    )
    AI_MODEL = "openai/gpt-4o-mini"
else:
    print("Использую официальный OpenAI...")
    gpt_client = OpenAI(api_key=OPENAI_KEY)
    AI_MODEL = "gpt-4o-mini"

# 3. Запускаем Телеграм
client = TelegramClient('amvera_session', API_ID, API_HASH)

processed_news = []

async def rewrite_news(text):
    # Обновленная инструкция: просим оформить Суть как цитату (>)
    system_prompt = (
        "Ты — редактор канала «Сухой остаток». Твоя задача — сократить новость, оставив только факты. "
        "Стиль: информационный, без воды. "
        "Структура ответа:\n"
        "1. Основной текст новости (коротко).\n"
        "2. С новой строки, обязательно со знаком цитирования '>':\n"
        "> 📌 Суть: [одно предложение с главным выводом].\n"
        "Если новость — реклама, верни только слово SKIP."
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
    if not text or len(text) < 50: return
    
    if text[:50] in processed_news: return
    processed_news.append(text[:50])
    if len(processed_news) > 100: processed_news.pop(0)

    print(f"Новость из {event.chat.username}")
    
    new_post = await rewrite_news(text)
    
    if new_post and "SKIP" not in new_post:
        # Отправляем ТОЛЬКО текст новости (без приписки Источник)
        # parse_mode='md' включен по умолчанию в Telethon для текста
        await client.send_message(DESTINATION, new_post)
        print("✅ Пост отправлен!")

print("Бот запущен! Жду новостей...")
client.start()
client.run_until_disconnected()
