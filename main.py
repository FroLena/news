import os
import asyncio
import json
import requests
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
    clean_prompt = prompt_text.replace('||', '').replace('R:', '').strip()
    print(f"🎨 Рисую (Flux): {clean_prompt[:50]}...")
    
    url = "https://openrouter.ai/api/v1/images/generations"
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://amvera.ru",
        "X-Title": "NewsBot"
    }
    data = {
        "model": IMAGE_MODEL,
        "prompt": clean_prompt,
        "n": 1,
        "size": "1024x1024"
    }
    try:
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=data)
        if response.status_code == 200:
            return response.json()['data'][0]['url']
        else:
            print(f"⚠️ Ошибка API OpenRouter ({response.status_code}): {response.text}")
            return None
    except Exception as e:
        print(f"⚠️ Критическая ошибка запроса: {e}")
        return None

# --- ПОДКАСТ (ТВОЙ ПРОМПТ) ---
async def send_evening_podcast():
    print("🎙 Готовлю подкаст...")
    try:
        history_posts = []
        async for message in client.iter_messages(DESTINATION, limit=30):
            if message.text: history_posts.append(message.text)
        
        if not history_posts: return
        full_text = "\n\n".join(history_posts[:20])

        system_prompt = (
            "Ты — профессиональный радиоведущий итогового шоу «Сухой остаток».\n"
            "Твоя задача: Создать увлекательный сценарий на основе предоставленных новостей за день.\n\n"
            "ТРЕБОВАНИЯ К ТЕКСТУ:\n"
            "1. СТРУКТУРА: Вступление -> Плавный рассказ (3-5 главных тем) -> Заключение.\n"
            "2. СТИЛЬ: Живой, разговорный, немного ироничный, но уверенный. Избегай сухих перечислений. Используй переходы.\n"
            "3. АДАПТАЦИЯ ПОД ОЗВУЧКУ: Не используй сложные цифры, убери ссылки и спецсимволы.\n"
            "4. ХРОНОМЕТРАЖ: 60-90 секунд.\n\n"
            "НАЧАЛО: 'Добрый вечер. В эфире Сухой остаток. Подведем итоги этого дня.'\n"
            "КОНЕЦ: 'Таким был этот день. Оставайтесь с нами. До связи.'"
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

# --- AI РЕДАКТОР (ИСПРАВЛЕННЫЙ ПРОМПТ) ---
async def rewrite_news(text, history_topics):
    recent_history = history_topics[-5:] if len(history_topics) > 0 else []
    history_str = "\n".join([f"- {t}" for t in recent_history]) if recent_history else "Нет истории."

    # === ВЕРНУЛ ПОТЕРЯННУЮ ЧАСТЬ ПРОМПТА ===
    system_prompt = (
        f"Ты — главный редактор новостного канала «Сухой остаток». История тем: {history_str}\n\n"
        f"ФОРМАТ ОТВЕТА СТРОГО: ТЕКСТ ||| ПРОМПТ_КАРТИНКИ\n\n"
        f"=== ЧАСТЬ 1: ТЕКСТ (Russian HTML) ===\n"
        f"1. ФИЛЬТРЫ: Реклама/Продажи/Казино -> верни слово SKIP. Дубликаты событий -> верни DUPLICATE.\n"
        f"2. СТИЛЬ (Инфостиль): Пиши жестко, коротко, фактурно. Убирай вводные слова. Сразу к делу.\n"
        f"3. ЗАГОЛОВОК: Придумай цепляющий заголовок, выдели его жирным (<b>Заголовок</b>).\n"
        f"4. СТРУКТУРА: Заголовок -> 2-3 абзаца самой сути -> Цитата (если есть, через <i></i>) -> Вывод.\n"
        f"5. В КОНЦЕ ОБЯЗАТЕЛЬНО: <blockquote><b>📌 Суть:</b> [одно предложение, самый главный вывод]</blockquote>\n"
        f"6. РЕАКЦИИ: В самое начало текста добавь код реакции, например ||R:🔥|| (Выбери: 🔥, 🤡, ⚡️, 😢, 👍).\n"
        f"7. ОПРОСЫ: Если новость вызывает споры, добавь в конец ||POLL||.\n\n"
        f"=== ЧАСТЬ 2: ПРОМПТ КАРТИНКИ (English) ===\n"
        f"- Опиши сцену как режиссер кино. Style: 'Hyperrealistic documentary photo, award-winning journalism, cinematic lighting, 8k, highly detailed'.\n"
        f"- НЕ пиши текст на картинках.\n"
        f"- ПРИМЕР ОТВЕТА: ||R:🔥|| <b>Пожар в Москве</b>... ||| A hyperrealistic photo of firefighters battling a massive fire at night in Moscow."
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

    try:
        chat = await event.get_chat()
        source_name = chat.title
    except:
        source_name = "Неизвестный канал"
    
    print(f"🔎 Обработка новости из: {source_name}")
    
    full_response = await rewrite_news(text, published_topics)
    if not full_response: return

    if "DUPLICATE" in full_response: 
        print("❌ Дубль")
        return
    if "SKIP" in full_response: 
        print("🗑 Реклама")
        return

    # --- ПАРСИНГ ---
    raw_text = full_response
    image_prompt = None
    
    if "|||" in raw_text:
        parts = raw_text.split("|||")
        news_text = parts[0].strip()
        image_prompt = parts[1].strip()
        print("✅ Промпт для картинки найден!")
    else:
        news_text = raw_text.strip()
        print("⚠️ Промпт не найден (будет только текст или авто-промпт).")

    reaction = None
    if "||R:" in news_text:
        try:
            parts = news_text.split("||R:")
            subparts = parts[1].split("||")
            reaction = subparts[0].strip()
            news_text = subparts[1].strip()
            print(f"😎 Реакция: {reaction}")
        except: pass

    poll_data = None
    if "||POLL||" in news_text:
        try:
            p = news_text.split("||POLL||")
            news_text = p[0].strip()
            lines = p[1].strip().split('\n')
            if len(lines) >= 3: poll_data = {"q": lines[0], "o": [o for o in lines[1:] if o.strip()]}
        except: pass

    # Fallback (авто-промпт)
    if not image_prompt and event.message.photo:
        print("⚠️ Генерирую авто-промпт из текста...")
        base_prompt = news_text.replace('\n', ' ')[:150]
        image_prompt = f"Hyperrealistic documentary photo, award-winning journalism, cinematic lighting, 8k. Context: {base_prompt}"

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

        if sent_msg and reaction:
            await asyncio.sleep(2)
            try:
                await client(functions.messages.SendReactionRequest(
                    peer=DESTINATION,
                    msg_id=sent_msg.id,
                    reaction=[types.ReactionEmoji(emoticon=reaction)]
                ))
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
    print("🤖 Бот запущен! (Image Generation FIX)")
    client.run_until_disconnected()
