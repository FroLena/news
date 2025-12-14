import os
import asyncio
from telethon import TelegramClient, events
from openai import OpenAI
import requests

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')
CLIPDROP_KEY = os.environ.get('CLIPDROP_API_KEY')

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

# --- ФУНКЦИЯ СТИРАТЕЛЯ ---
def clean_image(input_path):
    if not CLIPDROP_KEY:
        return input_path
    
    print(f"🧼 Отправляю в стирку: {input_path}")
    output_path = input_path + "_clean.jpg"
    try:
        response = requests.post(
            'https://clipdrop-api.co/remove-text/v1',
            headers={'x-api-key': CLIPDROP_KEY},
            files={'image_file': open(input_path, 'rb')}
        )
        if response.ok:
            with open(output_path, 'wb') as f:
                f.write(response.content)
            return output_path
        else:
            print(f"❌ Ошибка стирки: {response.status_code}")
            return input_path
    except Exception as e:
        print(f"❌ Ошибка стирки: {e}")
        return input_path
# -------------------------

async def rewrite_news(text, history_topics):
    # Берем только 5 последних тем для проверки (чтобы не путался в старом)
    recent_history = history_topics[-5:] 
    history_str = "\n".join([f"- {t}" for t in recent_history])
    
    # ОТЛАДКА: Показываем в логах, с чем сравниваем
    if recent_history:
        print(f"🧐 Сравниваю с темами:\n{history_str}")
    else:
        print("🧐 История пуста, это первая новость.")

    system_prompt = (
        f"Ты — профессиональный новостник. \n"
        f"Вот темы, которые мы УЖЕ публиковали (Recent History):\n{history_str}\n\n"
        f"СТРОГАЯ ИНСТРУКЦИЯ ПРОВЕРКИ НА ДУБЛИ:\n"
        f"1. Сравнивай ФАКТЫ: Локация, Имена, Числа.\n"
        f"2. Если событие то же самое (например, 'пожар на складе Озон') — верни DUPLICATE.\n"
        f"3. ВАЖНО: Если тема похожа, но детали другие (другой пожар, другое ДТП) — ЭТО НЕ ДУБЛЬ! Пиши новость.\n"
        f"4. ВАЖНО: Если это ПРОДОЛЖЕНИЕ истории (новые подробности, число жертв) — ЭТО НЕ ДУБЛЬ! Пиши новость.\n"
        f"5. Если реклама — верни SKIP.\n\n"
        f"Если не дубль и не реклама — сократи новость (HTML формат):\n"
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
    
    # Фильтр мусора, но пропускаем фото с подписями
    if len(text) < 15 and not event.message.photo: return

    # Быстрый кэш (точное совпадение текста)
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
        print("❌ AI считает это дублем. Пропуск.")
        return
    if "SKIP" in result:
        print("🗑 AI считает это рекламой. Пропуск.")
        return

    # --- РАБОТА С ФАЙЛАМИ ---
    original_path = None
    final_path = None
    
    try:
        if event.message.photo:
            print("📸 Качаю фото...")
            original_path = await event.download_media()
            final_path = await asyncio.to_thread(clean_image, original_path)
    
        if final_path:
            await client.send_file(DESTINATION, final_path, caption=result, parse_mode='html')
        else:
            await client.send_message(DESTINATION, result, parse_mode='html')
        
        print("✅ Пост опубликован!")
        
        # Добавляем в историю (коротко, чтобы не забивать память)
        summary = result[:80].replace('\n', ' ')
        published_topics.append(summary)
        # Храним только 10 последних
        if len(published_topics) > 10: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        if original_path and os.path.exists(original_path):
            os.remove(original_path)
        if final_path and final_path != original_path and os.path.exists(final_path):
            os.remove(final_path)

print("Бот запущен! (Исправлена логика дублей)")
client.start()
client.run_until_disconnected()
