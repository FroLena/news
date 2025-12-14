import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

# Каналы
SOURCE_CHANNELS = ['rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon']
DESTINATION = '@s_ostatok' # <--- ПРОВЕРЬ ЮЗЕРНЕЙМ

# 2. OpenAI / OpenRouter
if OPENAI_KEY.startswith("sk-or-"):
    print("Использую OpenRouter...")
    gpt_client = OpenAI(api_key=OPENAI_KEY, base_url="https://openrouter.ai/api/v1")
    AI_MODEL = "openai/gpt-4o-mini"
else:
    print("Использую OpenAI...")
    gpt_client = OpenAI(api_key=OPENAI_KEY)
    AI_MODEL = "gpt-4o-mini"

# 3. Клиент Телеграм
client = TelegramClient('amvera_session', API_ID, API_HASH)

processed_news = []

async def rewrite_news(text):
    # ПРОМПТ: Просим вернуть HTML. Тег <blockquote> создаст цитату.
    system_prompt = (
        "Ты — редактор канала. Сократи новость, оставь факты. "
        "Формат ответа строго HTML:\n"
        "1. Основной текст новости.\n"
        "2. В конце вставь вывод в теге цитаты:\n"
        "<blockquote><b>📌 Суть:</b> [твой вывод]</blockquote>\n"
        "Не используй Markdown (звездочки и решетки), только теги HTML."
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
    if not text or len(text) < 50: return
    
    if text[:50] in processed_news: return
    processed_news.append(text[:50])
    if len(processed_news) > 100: processed_news.pop(0)

    print(f"Новость из {event.chat.username}")
    
    new_post = await rewrite_news(text)
    
    if new_post and "SKIP" not in new_post:
        # ВАЖНО: parse_mode='html' включает поддержку тегов
        await client.send_message(DESTINATION, new_post, parse_mode='html')
        print("✅ Пост отправлен!")

print("Бот запущен! (Режим HTML)")
client.start()
client.run_until_disconnected()
