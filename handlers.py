import os
import asyncio
from datetime import datetime
from telethon import events, types, functions
from config import SOURCE_CHANNELS, DESTINATION, MAX_VIDEO_SIZE
from database import stats_db, save_to_history
from services.filters import is_duplicate, check_stop_words
from services.news import process_news
from services.image import generate_image

# Для красивого вывода времени в логах
def log_time():
    return datetime.now().strftime("%H:%M:%S")

def register_handlers(client):
    @client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def main_handler(event):
        # --- СБОР ИНФОРМАЦИИ ---
        chat_title = "Неизвестный канал"
        try:
            chat = await event.get_chat()
            if chat.title: chat_title = chat.title
        except: pass

        text = event.message.message
        if not text:
            print(f"[{log_time()}] ⚠️ PURE MEDIA: Пропуск (нет текста)")
            return
            
        # Очистка текста для лога (убираем переносы строк)
        clean_preview = text.replace('\n', ' ').strip()[:75]
        
        # --- ВИЗУАЛЬНЫЙ ЛОГ ВХОДЯЩЕГО ---
        print(f"\n{'='*50}")
        print(f"📥 [{log_time()}] NEW POST from: {chat_title}")
        print(f"📜 TEXT: {clean_preview}...")
        print(f"{'-'*50}")

        if len(text) < 20: 
            print(f"🛑 FILTER: Слишком короткий текст (<20).")
            return

        # --- ЭТАП 1: HARD FILTER (Стоп-слова) ---
        if check_stop_words(text):
            print(f"🛑 HARD FILTER: Найдена РЕКЛАМА или СПАМ.")
            stats_db.increment('rejected_ads')
            return

        # --- ЭТАП 2: DB FILTER (Дубли) ---
        if is_duplicate(text):
            print(f"♻️ DB FILTER: Этот пост уже есть в базе (Дубль).")
            stats_db.increment('rejected_dups')
            return
        
        stats_db.increment('scanned')
        
        # --- ЭТАП 3: AI PROCESSING ---
        print(f"🧠 AI: Анализирую факты и пишу пост...")
        full_response = await process_news(text)
        
        if not full_response:
            print(f"❌ ERROR: GPT вернул пустоту.")
            stats_db.increment('rejected_other')
            return

        # Логика AI ответов
        if "DUPLICATE" in full_response:
            stats_db.increment('rejected_dups')
            print(f"🚫 AI REJECT: GPT определил смысловой дубль.")
            return
        if "SKIP" in full_response:
            stats_db.increment('rejected_ads')
            print(f"🗑 AI REJECT: GPT определил мусор/рекламу.")
            return

        # --- ЭТАП 4: ПАРСИНГ ---
        raw_text = full_response
        image_prompt = None
        
        if "|||" in full_response:
            parts = full_response.split("|||")
            news_text = parts[0].strip()
            if len(parts) > 1 and len(parts[1].strip()) > 5:
                image_prompt = parts[1].strip()
        else:
            news_text = full_response.strip()

        # Парсинг реакции
        reaction = None
        if "||R:" in news_text:
            try:
                parts = news_text.split("||R:")
                subparts = parts[1].split("||")
                reaction = subparts[0].strip()
                news_text = subparts[1].strip()
            except: pass

        # Парсинг опроса
        poll_data = None
        if "||POLL||" in news_text:
            try:
                parts = news_text.split("||POLL||")
                news_text = parts[0].strip()
                poll_raw = parts[1].strip().split('\n')
                poll_lines = [line.strip() for line in poll_raw if line.strip()]
                if len(poll_lines) >= 3:
                    poll_data = {"q": poll_lines[0], "o": poll_lines[1:]}
                print("📊 POLL: Опрос создан.")
            except: pass

        # Авто-промпт, если GPT забыл
        if not image_prompt and event.message.photo:
            print("⚠️ AI WARNING: GPT забыл промпт, генерирую авто.")
            base_prompt = news_text.replace('\n', ' ')[:200]
            image_prompt = f"Commercial photo of {base_prompt}. Bright light, 8k sharp."

        # --- ЭТАП 5: ПУБЛИКАЦИЯ ---
        sent_msg = None
        try:
            has_video = event.message.video is not None
            
            # 1. Видео
            if has_video:
                if event.message.file.size > MAX_VIDEO_SIZE:
                    print("📹 VIDEO: Слишком большое, шлю текст.")
                    sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
                else:
                    print("📹 VIDEO: Скачиваю и отправляю...")
                    path_to_video = await event.download_media()
                    if path_to_video:
                        sent_msg = await client.send_file(DESTINATION, path_to_video, caption=news_text, parse_mode='html')
                        os.remove(path_to_video)
            
            # 2. Картинка (Генерация)
            elif image_prompt:
                print(f"🎨 IMAGE: Генерирую...")
                path_to_image = await generate_image(image_prompt)
                if path_to_image and os.path.exists(path_to_image):
                    sent_msg = await client.send_file(DESTINATION, path_to_image, caption=news_text, parse_mode='html')
                    os.remove(path_to_image)
                else:
                    print("⚠️ IMAGE FAIL: Не вышло, шлю текст.")
                    sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            
            # 3. Просто текст
            else:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')

            if sent_msg:
                stats_db.increment('published')
                print(f"✅ DONE! Опубликовано. ID: {sent_msg.id}")
                print(f"{'='*50}\n")
                
                # Чистка и сохранение
                essence = news_text
                if "<blockquote>" in news_text:
                    try: 
                        raw_essence = news_text.split("<blockquote>")[1].split("</blockquote>")[0]
                        # Чистим от эмодзи и слова Суть
                        clean_essence = raw_essence.replace("📌", "").replace("Суть", "").strip()
                        if len(clean_essence) > 5: essence = clean_essence
                        else: essence = news_text.split("\n")[0]
                    except: pass
                
                save_to_history(essence)
                
                # Реакции
                if reaction:
                    await asyncio.sleep(2)
                    try:
                        await client(functions.messages.SendReactionRequest(
                            peer=DESTINATION, msg_id=sent_msg.id, reaction=[types.ReactionEmoji(emoticon=reaction)]
                        ))
                    except: pass

                # Опрос
                if poll_data:
                    await asyncio.sleep(1)
                    try:
                        await client.send_message(DESTINATION, file=types.InputMediaPoll(
                            poll=types.Poll(id=1, question=poll_data["q"], answers=[types.PollAnswer(text=o, option=bytes([i])) for i, o in enumerate(poll_data["o"])])
                        ))
                    except: pass
        except Exception as e:
            print(f"❌ CRITICAL ERROR: {e}")
            stats_db.increment('rejected_other')
