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

# Максимальный размер видео для скачивания (в байтах). 50 МБ = 50 * 1024 * 1024
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
        print("🆕 История пуста, пропускаем проверку на дубли.")
        history_str = "Нет истории."
    else:
        recent_history = history_topics[-5:] 
        history_str = "\n".join([f"- {t}" for t in recent_history])
        print(f"🧐 Сравниваю с:\n{history_str}")

    system_prompt = (
        f"Ты — редактор канала «Сухой остаток». \n"
        f"История опубликованного:\n{history_str}\n\n"
        f"ИНСТРУКЦИЯ:\n"
        f"1. ДУБЛИ: Блокируй (верни DUPLICATE) ТОЛЬКО если это 100% повтор. Развитие темы — публикуй.\n"
        f"2. РЕКЛАМА: Если продажа/подписка — верни SKIP.\n"
        f"3. ТЕКСТ (HTML): Сократи новость, оставь суть.\n"
        f"   В конце обязательно: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"4. ОПРОС: Если тема острая, добавь в конце:\n"
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
    
    # Определяем тип медиа
    has_photo = event.message.photo is not None
    has_video = event.message.video is not None
    
    # Если текста мало (< 20 символов) и нет медиа, пропускаем.
    # Если есть видео/фото, но текста нет вообще — тоже пропускаем (нужен контекст).
    if len(text) < 20: return

    # Быстрый фильтр (кэш)
    short_hash = text[:100]
    if short_hash in raw_text_cache: return
    raw_text_cache.append(short_hash)
    if len(raw_text_cache) > 100: raw_text_cache.pop(0)

    print(f"🔎 Обработка: {event.chat.username}")
    
    # Генерируем текст
    full_response = await rewrite_news(text, published_topics)
    
    if not full_response: return
    if "DUPLICATE" in full_response:
        print(f"❌ Дубль. Причина AI: {full_response[:50]}...")
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
            pass

    # --- СКАЧИВАНИЕ И ОТПРАВКА ---
    path = None
    try:
        # Сценарий 1: ВИДЕО
        if has_video:
            # Проверяем размер
            video_size = event.message.file.size
            if video_size > MAX_VIDEO_SIZE:
                print(f"⚠️ Видео слишком большое ({video_size/1024/1024:.1f} MB). Публикую только текст.")
                await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                print("🎥 Качаю видео...")
                path = await event.download_media()
                await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html', supports_streaming=True)
        
        # Сценарий 2: ФОТО
        elif has_photo:
            print("📸 Качаю фото...")
            path = await event.download_media()
            await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html')
        
        # Сценарий 3: ТОЛЬКО ТЕКСТ
        else:
            await client.send_message(DESTINATION, news_text, parse_mode='html')
        
        # Отправка опроса (если есть)
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

        print("✅ Пост опубликован в @s_ostatok!")
        
        # История
        clean_summary = news_text.replace('<blockquote>', '').replace('</blockquote>', '').replace('<b>', '').replace('</b>', '')[:100]
        published_topics.append(clean_summary)
        if len(published_topics) > 10: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        # Важно: удаляем видео/фото, чтобы не забить диск
        if path and os.path.exists(path):
            os.remove(path)
            print("🗑 Временный файл удален")

print("Бот запущен! (v: Video + Photo + Text + Polls)")
client.start()
client.run_until_disconnected()
