import os
import asyncio
from telethon import TelegramClient, events, types
from openai import OpenAI

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

SOURCE_CHANNELS = ['rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon']
DESTINATION = '@s_ostatok' # ТВОЙ ЮЗЕРНЕЙМ

MAX_VIDEO_SIZE = 50 * 1024 * 1024 

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
    if len(history_topics) < 1:
        history_str = "Нет истории."
    else:
        recent_history = history_topics[-5:] 
        history_str = "\n".join([f"- {t}" for t in recent_history])

    # --- УМНЫЙ ПРОМПТ С ЗАЩИТОЙ ОТ ФУТЕРОВ ---
    system_prompt = (
        f"Ты — редактор канала «Сухой остаток».\n"
        f"История тем:\n{history_str}\n\n"
        f"ТВОЯ ЗАДАЧА: Прочитать, очистить от мусора и сократить.\n\n"
        f"ПРАВИЛА ФИЛЬТРАЦИИ:\n"
        f"1. ИГНОРИРУЙ ПРИЗЫВЫ ИСТОЧНИКА: Фразы вроде 'Подписаться на РИА', 'Подписывайся на Mash', ссылки на их же канал — ЭТО НЕ РЕКЛАМА. Просто удали эти фразы из текста при переписывании.\n"
        f"2. РЕКЛАМА (SKIP): Возвращай 'SKIP', только если ВЕСЬ пост посвящен продаже курсов, финок, ставок или рекламе ЧУЖИХ каналов.\n"
        f"3. ДУБЛИ (DUPLICATE): Возвращай 'DUPLICATE', только если это 100% повтор события.\n\n"
        f"ФОРМАТ ОТВЕТА (HTML):\n"
        f"- Убери вводные слова, оставь суть.\n"
        f"- В конце: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"4. ОПРОС (||POLL||): Добавляй только к острым темам."
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
    
    if len(text) < 20: return

    short_hash = text[:100]
    if short_hash in raw_text_cache: return
    raw_text_cache.append(short_hash)
    if len(raw_text_cache) > 100: raw_text_cache.pop(0)

    print(f"🔎 Обработка: {event.chat.username}")
    
    full_response = await rewrite_news(text, published_topics)
    
    if not full_response: return

    if "DUPLICATE" in full_response:
        print(f"❌ Дубль. AI: {full_response}")
        return
    if "SKIP" in full_response:
        print(f"🗑 Реклама. AI пояснил: {full_response}")
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
            pass

    # --- СКАЧИВАНИЕ И ОТПРАВКА ---
    path = None
    try:
        has_video = event.message.video is not None
        has_photo = event.message.photo is not None

        if has_video:
            video_size = event.message.file.size
            if video_size > MAX_VIDEO_SIZE:
                print(f"⚠️ Видео > 50MB. Шлю только текст.")
                await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                print("🎥 Качаю видео...")
                path = await event.download_media()
                await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html', supports_streaming=True)
        
        elif has_photo:
            print("📸 Качаю фото...")
            path = await event.download_media()
            await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html')
        
        else:
            await client.send_message(DESTINATION, news_text, parse_mode='html')
        
        if poll_data:
            await asyncio.sleep(1)
            poll_media = types.InputMediaPoll(
                poll=types.Poll(
                    id=12345, 
                    question=poll_data["q"],
                    answers=[types.PollAnswer(text=opt, option=bytes([i])) for i, opt in enumerate(poll_data["o"])]
                )
            )
            await client.send_message(DESTINATION, file=poll_media)
            print("📊 Опрос опубликован")

        print("✅ Пост улетел в @s_ostatok")
        
        clean_summary = news_text.replace('<blockquote>', '').replace('</blockquote>', '').replace('<b>', '').replace('</b>', '')[:100]
        published_topics.append(clean_summary)
        if len(published_topics) > 10: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        if path and os.path.exists(path):
            os.remove(path)
            print("🗑 Файл удален")

print("Бот запущен! (v: Smart Footer Cleaner)")
client.start()
client.run_until_disconnected()
