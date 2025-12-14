import os
import asyncio
from datetime import datetime
from telethon import TelegramClient, events, types, functions
from openai import OpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler # <--- Для расписания

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

SOURCE_CHANNELS = ['rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon', 'rhymestg']
DESTINATION = '@s_ostatok' # ТВОЙ ЮЗЕРНЕЙМ

MAX_VIDEO_SIZE = 50 * 1024 * 1024 

# 2. OpenAI
if OPENAI_KEY.startswith("sk-or-"):
    print("Использую OpenRouter...")
    gpt_client = OpenAI(api_key=OPENAI_KEY, base_url="https://openrouter.ai/api/v1")
    AI_MODEL = "openai/gpt-4o-mini"
    TTS_MODEL = None # OpenRouter часто не поддерживает Audio, проверим ниже
else:
    print("Использую OpenAI...")
    gpt_client = OpenAI(api_key=OPENAI_KEY)
    AI_MODEL = "gpt-4o-mini"
    TTS_MODEL = "tts-1"

# 3. Клиент
client = TelegramClient('amvera_session', API_ID, API_HASH)
scheduler = AsyncIOScheduler() # Таймер

raw_text_cache = []
published_topics = []

# --- ФУНКЦИЯ: ГЕНЕРАЦИЯ ПОДКАСТА ---
async def send_evening_podcast():
    print("🎙 Начинаю готовить вечерний подкаст...")
    try:
        # 1. Читаем последние 30 постов из СВОЕГО канала
        history_posts = []
        async for message in client.iter_messages(DESTINATION, limit=30):
            if message.text:
                history_posts.append(message.text)
        
        if not history_posts:
            print("🎙 В канале пусто, подкаст отменен.")
            return

        full_text = "\n\n".join(history_posts[:20]) # Берем 20 последних текстов

        # 2. Пишем сценарий
        system_prompt = (
            "Ты — ведущий радио «Сухой остаток». Твоя задача — сделать короткий вечерний дайджест.\n"
            "Тебе дан список постов за день. Выбери 5 самых важных и свяжи их в один рассказ.\n"
            "Стиль: Спокойный, ироничный, мужской. Без приветствий типа 'Доброго времени суток'.\n"
            "Начни сразу: 'Вечерний дайджест. Главное за сегодня...'\n"
            "Закончи фразой: 'Это был Сухой остаток. Услышимся завтра.'\n"
            "Максимум 150 слов."
        )
        
        script_response = gpt_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": full_text}
            ]
        )
        script = script_response.choices[0].message.content
        print(f"🎙 Сценарий готов:\n{script}")

        # 3. Озвучка (Только если это офф. OpenAI, OpenRouter может не уметь)
        if TTS_MODEL:
            speech_file_path = "podcast.mp3"
            response = gpt_client.audio.speech.create(
                model=TTS_MODEL,
                voice="onyx", # Мужской голос (варианты: alloy, echo, fable, onyx, nova, shimmer)
                input=script
            )
            response.stream_to_file(speech_file_path)
            
            # 4. Отправка
            await client.send_file(
                DESTINATION, 
                speech_file_path, 
                caption="🎙 <b>Итоги дня</b>\n<i>Слушать в наушниках</i>", 
                parse_mode='html',
                voice_note=True # Отправится как красивое голосовое с волной
            )
            print("🎙 Подкаст отправлен!")
            os.remove(speech_file_path)
        else:
            print("⚠️ TTS не поддерживается текущим ключом.")

    except Exception as e:
        print(f"❌ Ошибка подкаста: {e}")

# --- ФУНКЦИЯ: ОБРАБОТКА НОВОСТЕЙ ---
async def rewrite_news(text, history_topics):
    recent_history = history_topics[-5:] if len(history_topics) > 0 else []
    history_str = "\n".join([f"- {t}" for t in recent_history]) if recent_history else "Нет истории."

    system_prompt = (
        f"Ты — редактор канала. История тем:\n{history_str}\n\n"
        f"ЗАДАЧИ:\n"
        f"1. РЕАКЦИЯ (||R:emoji||): Оцени новость и выбери эмодзи: 🔥 (важно/круто), 🤡 (кринж/глупость), ⚡️ (срочно), 😢 (трагедия), 👍 (норм). "
        f"Добавь в начало ответа строку: ||R:🔥|| (или другой эмодзи).\n"
        f"2. ФИЛЬТР: Рекламу -> SKIP. Дубли -> DUPLICATE.\n"
        f"3. ИГНОР ПОДПИСЕЙ: Удали 'Подпишись на...'.\n"
        f"4. ТЕКСТ (HTML): Сократи. В конце: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"5. ОПРОС (||POLL||): Если нужно, добавь в конец."
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
        print(f"❌ Дубль: {full_response[:50]}")
        return
    if "SKIP" in full_response:
        print(f"🗑 Реклама: {full_response[:50]}")
        return

    # --- ПАРСИНГ ---
    news_text = full_response
    poll_data = None
    reaction = None

    # 1. Ищем реакцию ||R:😊||
    if "||R:" in full_response:
        try:
            parts = full_response.split("||R:")
            # parts[0] - пусто или текст до тега, parts[1] - эмодзи||текст
            subparts = parts[1].split("||")
            reaction = subparts[0].strip() # Эмодзи
            full_response = subparts[1].strip() # Остальной текст
        except:
            pass
            
    # 2. Ищем опрос ||POLL||
    if "||POLL||" in full_response:
        try:
            parts = full_response.split("||POLL||")
            news_text = parts[0].strip()
            poll_lines = parts[1].strip().split('\n')
            if len(poll_lines) >= 3:
                poll_data = {"q": poll_lines[0], "o": [opt for opt in poll_lines[1:] if opt.strip()]}
        except:
            pass
    else:
        news_text = full_response

    # --- ОТПРАВКА ---
    path = None
    sent_msg = None # Сюда сохраним отправленное сообщение
    try:
        has_video = event.message.video is not None
        has_photo = event.message.photo is not None

        if has_video:
            if event.message.file.size > MAX_VIDEO_SIZE:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                print("🎥 Качаю видео...")
                path = await event.download_media()
                sent_msg = await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html', supports_streaming=True)
        elif has_photo:
            print("📸 Качаю фото...")
            path = await event.download_media()
            sent_msg = await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html')
        else:
            sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
        
        # СТАВИМ РЕАКЦИЮ НА СВОЙ ЖЕ ПОСТ
        if sent_msg and reaction:
            try:
                # Пауза, чтобы телеграм успел сохранить msg
                await asyncio.sleep(2) 
                await client(functions.messages.SendReactionRequest(
                    peer=DESTINATION,
                    msg_id=sent_msg.id,
                    reaction=[types.ReactionEmoji(emoticon=reaction)]
                ))
                print(f"😎 Реакция поставлена: {reaction}")
            except Exception as r_e:
                print(f"⚠️ Не удалось поставить реакцию: {r_e}")

        # ОПРОС
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

        print("✅ Пост готов!")
        
        clean_summary = news_text.replace('<blockquote>', '').replace('</blockquote>', '').replace('<b>', '').replace('</b>', '')[:100]
        published_topics.append(clean_summary)
        if len(published_topics) > 10: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка отправки: {e}")
    finally:
        if path and os.path.exists(path):
            os.remove(path)

# Запуск планировщика (Подкаст в 18:00 UTC = 21:00 MSK)
# Если у Amvera время MSK, ставь hour=21. Обычно там UTC, поэтому 18.
scheduler.add_job(send_evening_podcast, 'cron', hour=18, minute=0)
scheduler.start()

print("Бот запущен! (v: Reactions + Podcast 21:00)")
client.start()
client.run_until_disconnected()
