import os
import asyncio
import json
import requests # Прямые запросы для картинок
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

# 2. OpenAI
print("Использую OpenRouter...")
gpt_client = OpenAI(
    api_key=OPENAI_KEY, 
    base_url="https://openrouter.ai/api/v1"
)
AI_MODEL = "openai/gpt-4o-mini"
IMAGE_MODEL = "black-forest-labs/flux-1-schnell"

# 3. Клиент
client = TelegramClient('amvera_session', API_ID, API_HASH)
raw_text_cache = []
published_topics = []

# --- ГЕНЕРАЦИЯ КАРТИНКИ (ПРЯМОЙ ЗАПРОС) ---
async def generate_image(prompt_text):
    print(f"🎨 Рисую иллюстрацию: {prompt_text[:50]}...")
    url = "https://openrouter.ai/api/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://amvera.ru",
        "X-Title": "NewsBot"
    }
    data = {
        "model": IMAGE_MODEL,
        "prompt": prompt_text,
        "n": 1,
        "size": "1024x1024"
    }
    try:
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json()['data'][0]['url']
        else:
            print(f"⚠️ Ошибка API OpenRouter: {response.status_code}")
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
            "Текст для чтения вслух."
        )
        script = gpt_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": full_text}]
        ).choices[0].message.content.replace('*', '').replace('#', '')

        communicate = edge_tts.Communicate(script, "ru-RU-DmitryNeural")
        await communicate.save("podcast.mp3")
            
        await client.send_file(DESTINATION, "podcast.mp3", caption="🎙 <b>Итоги дня</b>", parse_mode='html', voice_note=True)
        if os.path.exists("podcast.mp3"): os.remove("podcast.mp3")
    except Exception as e:
        print(f"❌ Ошибка подкаста: {e}")

# --- AI РЕДАКТОР ---
async def rewrite_news(text, history_topics):
    recent_history = history_topics[-5:] if len(history_topics) > 0 else []
    history_str = "\n".join([f"- {t}" for t in recent_history]) if recent_history else "Нет истории."

    system_prompt = (
        f"Ты — редактор. История: {history_str}\n\n"
        f"ОТВЕТ В ФОРМАТЕ: ТЕКСТ ||| ПРОМПТ_КАРТИНКИ\n\n"
        f"ЧАСТЬ 1 (ТЕКСТ):\n"
        f"- Реклама -> SKIP. Дубли -> DUPLICATE.\n"
        f"- Сократи суть. В конце: <blockquote><b>📌 Суть:</b> [вывод]</blockquote>\n"
        f"- ОБЯЗАТЕЛЬНО добавь эмодзи-реакцию в самое начало текста в формате ||R:🔥||.\n"
        f"  (Варианты: ||R:🔥||, ||R:🤡||, ||R:⚡️||, ||R:😢||, ||R:👍||)\n"
        f"- Если тема острая — добавь ||POLL|| в конец.\n\n"
        f"ЧАСТЬ 2 (ПРОМПТ КАРТИНКИ):\n"
        f"- English only.\n"
        f"- Style: 'Hyperrealistic documentary photo, cinematic lighting, 8k'."
    )

    try:
        response = gpt_client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]
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

    print(f"🔎 Обработка новости...")
    
    full_response = await rewrite_news(text, published_topics)
    if not full_response: return

    if "DUPLICATE" in full_response: return
    if "SKIP" in full_response: return

    # --- ПАРСИНГ ---
    news_text = full_response
    image_prompt = None
    
    # 1. Отделяем картинку от текста
    if "|||" in full_response:
        parts = full_response.split("|||")
        news_text = parts[0].strip()
        image_prompt = parts[1].strip()
    else:
        # Fallback
        if event.message.photo:
            base_prompt = news_text.split('.')[0] if '.' in news_text else "News"
            image_prompt = f"Hyperrealistic documentary photo reflecting: {base_prompt}. Cinematic, 8k."
            news_text = full_response

    # 2. Ищем Реакцию (ВОТ ОНА!)
    reaction = None
    if "||R:" in news_text:
        try:
            parts = news_text.split("||R:")
            # Обычно это выглядит так: "||R:🔥|| Текст новости..."
            # parts[0] пустая, parts[1] "🔥|| Текст..."
            subparts = parts[1].split("||")
            reaction = subparts[0].strip() # 🔥
            news_text = subparts[1].strip() # Чистый текст
            print(f"😎 Найдена реакция: {reaction}")
        except:
            print("⚠️ Ошибка парсинга реакции")

    # 3. Ищем Опрос
    poll_data = None
    if "||POLL||" in news_text:
        try:
            p = news_text.split("||POLL||")
            news_text = p[0].strip()
            lines = p[1].strip().split('\n')
            if len(lines) >= 3: poll_data = {"q": lines[0], "o": [o for o in lines[1:] if o.strip()]}
        except: pass

    # --- ОТПРАВКА ---
    sent_msg = None
    try:
        has_video = event.message.video is not None
        
        if has_video:
            if event.message.file.size > MAX_VIDEO_SIZE:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
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

        # --- СТАВИМ РЕАКЦИЮ ---
        if sent_msg and reaction:
            await asyncio.sleep(2) # Даем телеграму время "осознать" сообщение
            try:
                await client(functions.messages.SendReactionRequest(
                    peer=DESTINATION,
                    msg_id=sent_msg.id,
                    reaction=[types.ReactionEmoji(emoticon=reaction)]
                ))
                print(f"✅ Реакция {reaction} поставлена!")
            except Exception as e:
                print(f"⚠️ Не удалось поставить реакцию: {e}")

        # --- СТАВИМ ОПРОС ---
        if poll_data:
            await asyncio.sleep(1)
            poll_media = types.InputMediaPoll(poll=types.Poll(id=1, question=poll_data["q"], answers=[types.PollAnswer(text=o, option=bytes([i])) for i, o in enumerate(poll_data["o"])]))
            await client.send_message(DESTINATION, file=poll_media)

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
    print("🤖 Бот запущен! (Reactions + Flux Fix)")
    client.run_until_disconnected()
