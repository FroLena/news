import os
import asyncio
import json
import requests # <--- Используем для прямой отправки запроса
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
DESTINATION = '@s_ostatok'

MAX_VIDEO_SIZE = 50 * 1024 * 1024 

# 2. OpenAI (Для текста)
print("Использую OpenRouter...")
gpt_client = OpenAI(
    api_key=OPENAI_KEY, 
    base_url="https://openrouter.ai/api/v1"
)
AI_MODEL = "openai/gpt-4o-mini"
# Модель для картинок
IMAGE_MODEL = "black-forest-labs/flux-1-schnell"

# 3. Клиент
client = TelegramClient('amvera_session', API_ID, API_HASH)
raw_text_cache = []
published_topics = []

# --- ГЕНЕРАЦИЯ КАРТИНКИ (ПРЯМОЙ ЗАПРОС) ---
async def generate_image(prompt_text):
    print(f"🎨 Рисую иллюстрацию (Direct Request): {prompt_text[:50]}...")
    
    url = "https://openrouter.ai/api/v1/images/generations"
    
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://amvera.ru", # OpenRouter просит эти заголовки
        "X-Title": "NewsBot"
    }
    
    data = {
        "model": IMAGE_MODEL,
        "prompt": prompt_text,
        "n": 1,
        "size": "1024x1024" # Flux работает лучше всего с квадратом
    }

    try:
        # Делаем запрос в отдельном потоке, чтобы не блокировать бота
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=data)
        
        if response.status_code == 200:
            result = response.json()
            image_url = result['data'][0]['url']
            print("🎨 Картинка успешно создана!")
            return image_url
        else:
            print(f"⚠️ Ошибка API OpenRouter: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"⚠️ Ошибка запроса картинки: {e}")
        return None

# --- ПОДКАСТ ---
async def send_evening_podcast():
    print("🎙 Готовлю подкаст...")
    try:
        history_posts = []
        async for message in client.iter_messages(DESTINATION, limit=30):
            if message.text: history_posts.append(message.text)
        
        if not history_posts: return

        full_text = "\n\n".join(history_posts[:20])

        system_prompt = (
            "Ты — ведущий 'Сухой остаток'. Сделай вечерний дайджест.\n"
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

# --- AI РЕДАКТОР ---
async def rewrite_news(text, history_topics):
    recent_history = history_topics[-5:] if len(history_topics) > 0 else []
    history_str = "\n".join([f"- {t}" for t in recent_history]) if recent_history else "Нет истории."

    system_prompt = (
        f"Ты — редактор. История: {history_str}\n\n"
        f"ОЧЕНЬ ВАЖНО: Твой ответ должен состоять из ДВУХ частей, разделенных символами |||\n"
        f"Часть 1: Текст новости (HTML)\n"
        f"Часть 2: Промпт для картинки (English)\n\n"
        f"ПРАВИЛА ТЕКСТА:\n"
        f"- Реклама -> SKIP. Дубли -> DUPLICATE.\n"
        f"- Сократи суть. В конце: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"- Острые темы: ||R:🔥|| в начало, ||POLL|| в конец.\n\n"
        f"ПРАВИЛА КАРТИНКИ (ОБЯЗАТЕЛЬНО):\n"
        f"- Промпт строго на английском.\n"
        f"- Стиль: 'Hyperrealistic documentary photo, cinematic lighting, 8k'.\n"
        f"- Пример ответа:\n"
        f"Пожар на складе... ||| A photo of firefighters at night in Moscow, smoke, orange fire lights, wet asphalt."
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

    chat_title = event.chat.title if hasattr(event.chat, 'title') else str(event.chat_id)
    print(f"🔎 Обработка: {chat_title}")
    
    full_response = await rewrite_news(text, published_topics)
    if not full_response: return

    print(f"🤖 Ответ AI (начало): {full_response[:100]}...")

    if "DUPLICATE" in full_response:
        print(f"❌ Дубль")
        return
    if "SKIP" in full_response:
        print(f"🗑 Реклама")
        return

    # Парсинг
    news_text = full_response
    image_prompt = None
    
    if "|||" in full_response:
        parts = full_response.split("|||")
        news_text = parts[0].strip()
        image_prompt = parts[1].strip()
        print("✅ Промпт найден!")
    else:
        # Fallback генерация
        if event.message.photo:
            print("⚠️ ИИ забыл промпт! Генерирую авто-промпт...")
            base_prompt = news_text.split('.')[0] if '.' in news_text else news_text[:50]
            # Транслитерацию делать сложно без библиотек, надеемся что Flux поймет или возьмем просто "Breaking news" стиль
            # Лучше попросить GPT перевести, но для скорости просто сделаем общий промпт
            image_prompt = f"Hyperrealistic documentary photo reflecting the news topic. Cinematic lighting, 8k. Context: {base_prompt}"
            news_text = full_response
        else:
            news_text = full_response

    # Допы
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
        
        if has_video:
            if event.message.file.size > MAX_VIDEO_SIZE:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                print("🎥 Видео... (Оригинал)")
                path = await event.download_media()
                sent_msg = await client.send_file(DESTINATION, path, caption=news_text, parse_mode='html', supports_streaming=True)
                os.remove(path)
        
        elif image_prompt:
            img_url = await generate_image(image_prompt)
            if img_url:
                sent_msg = await client.send_file(DESTINATION, img_url, caption=news_text, parse_mode='html')
            else:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
        
        else:
            sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')

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
        print(f"Ошибка отправки: {e}")

if __name__ == '__main__':
    print("🚀 Старт...")
    client.start()
    scheduler = AsyncIOScheduler(event_loop=client.loop)
    scheduler.add_job(send_evening_podcast, 'cron', hour=18, minute=0)
    scheduler.start()
    print("🤖 Бот запущен! (Fixed: 405 Error)")
    client.run_until_disconnected()
