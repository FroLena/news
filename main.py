import os
import asyncio
from telethon import TelegramClient, events, types, functions
from openai import OpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import edge_tts

# 1. Настройки
API_ID = int(os.environ.get('TG_API_ID'))
API_HASH = os.environ.get('TG_API_HASH')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

SOURCE_CHANNELS = [
    'rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon', 
    'shot_shot', 'ostorozhno_novosti', 'rbc_news'
]
DESTINATION = '@s_ostatok' # ТВОЙ ЮЗЕРНЕЙМ

MAX_VIDEO_SIZE = 50 * 1024 * 1024 

# 2. OpenAI (OpenRouter для текста и картинок)
print("Использую OpenRouter...")
gpt_client = OpenAI(
    api_key=OPENAI_KEY, 
    base_url="https://openrouter.ai/api/v1"
)
# Модель для текста
AI_MODEL = "openai/gpt-4o-mini"
# Модель для картинок (Быстрая и качественная)
IMAGE_MODEL = "black-forest-labs/flux-1-schnell"

# 3. Клиент
client = TelegramClient('amvera_session', API_ID, API_HASH)
raw_text_cache = []
published_topics = []

# --- ГЕНЕРАЦИЯ КАРТИНКИ (FLUX HYPERREALISM) ---
async def generate_image(prompt_text):
    print(f"🎨 Рисую иллюстрацию: {prompt_text[:50]}...")
    try:
        # Flux любит квадратные или слегка горизонтальные кадры.
        # 1024x1024 - оптимально для скорости и качества.
        response = gpt_client.images.generate(
            model=IMAGE_MODEL,
            prompt=prompt_text,
            n=1,
            size="1024x1024"
        )
        image_url = response.data[0].url
        return image_url
    except Exception as e:
        print(f"⚠️ Ошибка генерации картинки: {e}")
        return None

# --- ПОДКАСТ (EDGE TTS) ---
async def send_evening_podcast():
    print("🎙 Готовлю подкаст...")
    try:
        history_posts = []
        async for message in client.iter_messages(DESTINATION, limit=30):
            if message.text: history_posts.append(message.text)
        
        if not history_posts: return

        full_text = "\n\n".join(history_posts[:20])

        system_prompt = (
            "Ты — ведущий 'Сухой остаток'. Сделай вечерний дайджест (3-5 новостей).\n"
            "Стиль: Спокойный, уверенный.\n"
            "Текст для чтения вслух."
        )
        
        script = gpt_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": full_text}]
        ).choices[0].message.content.replace('*', '').replace('#', '')

        communicate = edge_tts.Communicate(script, "ru-RU-DmitryNeural")
        await communicate.save("podcast.mp3")
            
        await client.send_file(
            DESTINATION, "podcast.mp3", 
            caption="🎙 <b>Итоги дня</b>", parse_mode='html', voice_note=True
        )
        if os.path.exists("podcast.mp3"): os.remove("podcast.mp3")
    except Exception as e:
        print(f"❌ Ошибка подкаста: {e}")

# --- AI РЕДАКТОР + ПРОМПТ-ИНЖЕНЕР ---
async def rewrite_news(text, history_topics):
    recent_history = history_topics[-5:] if len(history_topics) > 0 else []
    history_str = "\n".join([f"- {t}" for t in recent_history]) if recent_history else "Нет истории."

    # === НОВЫЙ, УСИЛЕННЫЙ ПРОМПТ ===
    system_prompt = (
        f"Ты — редактор и арт-директор. История: {history_str}\n\n"
        f"ЗАДАЧА: Верни ответ строго в формате: ТЕКСТ ||| ПРОМПТ_ДЛЯ_КАРТИНКИ\n\n"
        f"1. ТЕКСТ (HTML):\n"
        f"   - Реклама -> SKIP. Дубли -> DUPLICATE.\n"
        f"   - Сократи суть. В конце: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"   - Острые темы: ||R:🔥|| в начало, ||POLL|| в конец.\n\n"
        f"2. ПРОМПТ_ДЛЯ_КАРТИНКИ (English) --- СТРОГИЕ ПРАВИЛА:\n"
        f"   - Твоя цель: создать промпт для ГИПЕРРЕАЛИСТИЧНОЙ, кинематографичной фотографии, передающей суть новости.\n"
        f"   - Обязательно используй стиль: 'A documentary photograph, award-winning photojournalism, cinematic lighting, highly detailed, 8k resolution, realistic texture'.\n"
        f"   - Детализация: Опиши главное действие, время суток, погоду, атмосферу (напряженная, спокойная, мрачная). Опиши ключевые объекты сцены и фон.\n"
        f"   - ЗАПРЕТ: Никаких иллюстраций, мультиков, 3D-рендеров или абстракций. Только суровый реализм.\n"
        f"   - Пример: 'A documentary photograph of firefighters battling a massive warehouse fire at night in Moscow. Huge orange flames, smoke billowing, wet asphalt reflecting lights, exhausted firefighters with hoses. Cinematic, gritty, highly detailed.'\n"
        f"   - ПРОМПТ ДОЛЖЕН БЫТЬ НА АНГЛИЙСКОМ."
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
        print(f"❌ Дубль")
        return
    if "SKIP" in full_response:
        print(f"🗑 Реклама")
        return

    # Парсинг (ТЕКСТ ||| ПРОМПТ)
    news_text = full_response
    image_prompt = None
    if "|||" in full_response:
        parts = full_response.split("|||")
        news_text = parts[0].strip()
        image_prompt = parts[1].strip()
    
    # Парсинг допов
    poll_data = None
    reaction = None
    if "||R:" in news_text:
        try:
            p = news_text.split("||R:")
            sub = p[1].split("||")
            reaction = sub[0].strip()
            news_text = sub[1].strip()
        except: pass
    if "||POLL||" in news_text:
        try:
            p = news_text.split("||POLL||")
            news_text = p[0].strip()
            lines = p[1].strip().split('\n')
            if len(lines) >= 3: poll_data = {"q": lines[0], "o": [o for o in lines[1:] if o.strip()]}
        except: pass

    # Отправка
    sent_msg = None
    try:
        has_video = event.message.video is not None
        
        # 1. ВИДЕО -> Оригинал
        if has_video:
            if event.message.file.size > MAX_VIDEO_SIZE:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                print("🎥 Видео... (Оригинал)")
                path = await event.download_media()
                sent_msg = await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html', supports_streaming=True)
                os.remove(path)
        
        # 2. ФОТО/ТЕКСТ -> Генерация (Flux Hyperrealism)
        elif image_prompt:
            img_url = await generate_image(image_prompt)
            if img_url:
                # Отправляем как фото по ссылке
                sent_msg = await client.send_file(DESTINATION, img_url, caption=news_text, parse_mode='html')
            else:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
        
        else:
            sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')

        # Допы
        if sent_msg and reaction:
            await asyncio.sleep(2)
            try: await client(functions.messages.SendReactionRequest(peer=DESTINATION, msg_id=sent_msg.id, reaction=[types.ReactionEmoji(emoticon=reaction)]))
            except: pass
        if poll_data:
            await asyncio.sleep(1)
            poll_media = types.InputMediaPoll(poll=types.Poll(id=1, question=poll_data["q"], answers=[types.PollAnswer(text=o, option=bytes([i])) for i, o in enumerate(poll_data["o"])]))
            await client.send_message(DESTINATION, file=poll_media)

        print("✅ Пост готов!")
        published_topics.append(news_text[:100])
        if len(published_topics) > 10: published_topics.pop(0)

    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == '__main__':
    print("🚀 Старт...")
    client.start()
    scheduler = AsyncIOScheduler(event_loop=client.loop)
    scheduler.add_job(send_evening_podcast, 'cron', hour=18, minute=0)
    scheduler.start()
    print("🤖 Бот запущен! (Flux: Hyperrealistic News Photos)")
    client.run_until_disconnected()
