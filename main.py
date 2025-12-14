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

# Кэш технических дублей (точное совпадение текста)
raw_text_cache = []
# Кэш смысловых тем (о чем мы уже писали)
published_topics = []

async def rewrite_news(text, history_topics):
    # Превращаем список прошлых тем в строку
    history_str = "\n".join([f"- {t}" for t in history_topics])
    
    system_prompt = (
        f"Ты — строгий редактор новостного канала. \n"
        f"Вот список тем, которые мы УЖЕ опубликовали за последние часы:\n"
        f"{history_str}\n\n"
        f"ТВОЯ ЗАДАЧА:\n"
        f"1. Сравни новую новость с этим списком. Если новость об этом же событии (даже другими словами) — верни ТОЛЬКО слово DUPLICATE.\n"
        f"2. Если новость — реклама, верни ТОЛЬКО слово SKIP.\n"
        f"3. Если это новая уникальная новость — перепиши её.\n\n"
        f"ТРЕБОВАНИЯ К ФОРМАТУ (HTML):\n"
        f"- Сухой стиль, только факты.\n"
        f"- В конце вывод в цитате: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>"
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
    
    # 1. Быстрый фильтр: точное совпадение текста
    short_hash = text[:100]
    if short_hash in raw_text_cache: return
    raw_text_cache.append(short_hash)
    if len(raw_text_cache) > 100: raw_text_cache.pop(0)

    print(f"🔎 Проверяю новость из {event.chat.username}...")
    
    # 2. Умный фильтр через GPT
    result = await rewrite_news(text, published_topics)
    
    if not result: return # Ошибка сети
    
    if "DUPLICATE" in result:
        print(f"❌ Смысловой дубль. Пропускаем.")
        return
        
    if "SKIP" in result:
        print(f"🗑 Реклама. Пропускаем.")
        return

    # Если дошли сюда — новость уникальная. Отправляем.
    await client.send_message(DESTINATION, result, parse_mode='html')
    print("✅ Пост опубликован!")
    
    # Добавляем краткую суть этой новости в историю (чтобы не постить её снова)
    # Мы берем первые 50 символов ответа как "тему", этого обычно достаточно для ИИ
    topic_summary = result[:100].replace('\n', ' ')
    published_topics.append(topic_summary)
    # Храним только последние 15 тем, чтобы не перегружать промпт
    if len(published_topics) > 15: published_topics.pop(0)

print("Бот запущен! (Режим HTML + Анти-дубль)")
client.start()
client.run_until_disconnected()
