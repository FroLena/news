import os
import asyncio
from datetime import datetime
from telethon import TelegramClient, events, types, functions
from openai import OpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import edge_tts # <--- Бесплатная озвучка от Microsoft

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

SOURCE_CHANNELS = ['rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon']
DESTINATION = '@s_ostatok' # ТВОЙ ЮЗЕРНЕЙМ

MAX_VIDEO_SIZE = 50 * 1024 * 1024 

# 2. OpenAI (Работает и OpenRouter, и оригинал)
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
scheduler = AsyncIOScheduler()

raw_text_cache = []
published_topics = []

# --- ФУНКЦИЯ: ГЕНЕРАЦИЯ ПОДКАСТА (EDGE TTS) ---
async def send_evening_podcast():
    print("🎙 Начинаю готовить вечерний подкаст...")
    try:
        # 1. Читаем посты за сегодня (последние 30)
        history_posts = []
        async for message in client.iter_messages(DESTINATION, limit=30):
            if message.text:
                history_posts.append(message.text)
        
        if not history_posts:
            print("🎙 В канале пусто, подкаст отменен.")
            return

        full_text = "\n\n".join(history_posts[:20])

        # 2. Пишем сценарий
        system_prompt = (
            "Ты — ведущий радио «Сухой остаток». Твоя задача — сделать вечерний дайджест.\n"
            "Выбери 3-5 главных новостей из списка и свяжи их в рассказ.\n"
            "Стиль: Спокойный, уверенный, без лишних приветствий.\n"
            "Начни: 'Вечерний дайджест. Главное к этому часу...'\n"
            "Закончи: 'Это был Сухой остаток. До связи.'\n"
            "Текст должен быть готовым для чтения вслух (без спецсимволов и звездочек)."
        )
        
        script_response = gpt_client.chat.completions.create(
            model=AI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": full_text}
            ]
        )
        script = script_response.choices[0].message.content
        
        # Очистка скрипта от мусора (звездочек Markdown), чтобы диктор не читал "звездочка"
        script = script.replace('*', '').replace('#', '')
        print(f"🎙 Сценарий готов:\n{script}")

        # 3. Озвучка через Microsoft Edge (Бесплатно)
        speech_file_path = "podcast.mp3"
        voice = "ru-RU-DmitryNeural" # Мужской голос. Можно "ru-RU-SvetlanaNeural" (женский)
        
        communicate = edge_tts.Communicate(script, voice)
        await communicate.save(speech_file_path)
            
        # 4. Отправка
        await client.send_file(
            DESTINATION, 
            speech_file_path, 
            caption="🎙 <b>Итоги дня</b>\n<i>Главное за 2 минуты</i>", 
            parse_mode='html',
            voice_note=True # Голосовое сообщение
        )
        print("🎙 Подкаст отправлен!")
        
        if os.path.exists(speech_file_path):
            os.remove(speech_file_path)

    except Exception as e:
        print(f"❌ Ошибка подкаста: {e}")

# --- ОБРАБОТКА НОВОСТЕЙ ---
async def rewrite_news(text, history_topics):
    recent_history = history_topics[-5:] if len(history_topics) > 0 else []
    history_str = "\n".join([f"- {t}" for t in recent_history]) if recent_history else "Нет истории."

    system_prompt = (
        f"Ты — редактор канала. История тем:\n{history_str}\n\n"
        f"ЗАДАЧИ:\n"
        f"1. РЕАКЦИЯ (||R:emoji||): Оцени новость: 🔥, 🤡, ⚡️, 😢, 👍. "
        f"Добавь в начало: ||R:🔥||.\n"
        f"2. ФИЛЬТР: Рекламу -> SKIP. Дубли -> DUPLICATE.\n"
        f"3. ИГНОР ПОДПИСЕЙ: Удали 'Подпишись на...'.\n"
        f"4. ТЕКСТ (HTML): Сократи. В конце: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"5. ОПРОС (||POLL||): Добавляй к острым темам."
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

    # Парсинг
    news_text = full_response
    poll_data = None
    reaction = None

    if "||R:" in full_response:
        try:
            parts = full_response.split("||R:")
            subparts = parts[1].split("||")
            reaction = subparts[0].strip()
            full_response = subparts[1].strip()
        except: pass
            
    if "||POLL||" in full_response:
        try:
            parts = full_response.split("||POLL||")
            news_text = parts[0].strip()
            poll_lines = parts[1].strip().split('\n')
            if len(poll_lines) >= 3:
                poll_data = {"q": poll_lines[0], "o": [opt for opt in poll_lines[1:] if opt.strip()]}
        except: pass
    else:
        news_text = full_response

    # Отправка
    path = None
    sent_msg = None 
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
        
        # Реакция
        if sent_msg and reaction:
            await asyncio.sleep(2) 
            try:
                await client(functions.messages.SendReactionRequest(
                    peer=DESTINATION,
                    msg_id=sent_msg.id,
                    reaction=[types.ReactionEmoji(emoticon=reaction)]
                ))
                print(f"😎 Реакция: {reaction}")
            except: pass

        # Опрос
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

# Запуск таймера (21:00 по Москве = 18:00 UTC)
scheduler.add_job(send_evening_podcast, 'cron', hour=18, minute=0)
scheduler.start()

print("Бот запущен! (v: Reactions + FREE Podcast)")
client.start()
client.run_until_disconnected()
