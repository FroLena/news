import os
import asyncio
from telethon import events, types, functions
from config import SOURCE_CHANNELS, DESTINATION, MAX_VIDEO_SIZE
from database import stats_db, save_to_history
from services.filters import is_duplicate
from services.news import process_news
from services.image import generate_image

def register_handlers(client):
    @client.on(events.NewMessage(chats=SOURCE_CHANNELS))
    async def main_handler(event):
        text = event.message.message
        if not text or len(text) < 20: return

        # 1. Фильтр дублей (Локальный)
        if is_duplicate(text):
            print("♻️ Fuzzy-дубль (>65%)")
            stats_db.increment('rejected_dups')
            return
        
        stats_db.increment('scanned')
        try:
            chat = await event.get_chat()
            print(f"🔎 NEWS: {chat.title}")
        except: pass
        
        # 2. Рерайт (AI)
        full_response = await process_news(text)
        if not full_response:
            stats_db.increment('rejected_other')
            return

        if "DUPLICATE" in full_response:
            stats_db.increment('rejected_dups')
            print("❌ GPT: Дубль")
            return
        if "SKIP" in full_response:
            stats_db.increment('rejected_ads')
            print("🗑 GPT: Мусор")
            return

        # 3. Парсинг ответа
        raw_text = full_response
        image_prompt = None
        if "|||" in full_response:
            parts = full_response.split("|||")
            news_text = parts[0].strip()
            if len(parts) > 1: image_prompt = parts[1].strip()
        else:
            news_text = full_response.strip()

        reaction = None
        if "||R:" in news_text:
            try:
                parts = news_text.split("||R:")
                subparts = parts[1].split("||")
                reaction = subparts[0].strip()
                news_text = subparts[1].strip()
            except: pass

        poll_data = None
        if "||POLL||" in news_text:
            try:
                parts = news_text.split("||POLL||")
                news_text = parts[0].strip()
                poll_raw = parts[1].strip().split('\n')
                poll_lines = [line.strip() for line in poll_raw if line.strip()]
                if len(poll_lines) >= 3:
                    poll_data = {"q": poll_lines[0], "o": poll_lines[1:]}
            except: pass

        if not image_prompt and event.message.photo:
            base_prompt = news_text.replace('\n', ' ')[:200]
            image_prompt = f"Commercial photo of {base_prompt}. Bright light, 8k sharp."

        # 4. Отправка
        sent_msg = None
        try:
            has_video = event.message.video is not None
            if has_video:
                if event.message.file.size > MAX_VIDEO_SIZE:
                    sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
                else:
                    path_to_video = await event.download_media()
                    if path_to_video:
                        sent_msg = await client.send_file(DESTINATION, path_to_video, caption=news_text, parse_mode='html')
                        os.remove(path_to_video)
            elif image_prompt:
                path_to_image = await generate_image(image_prompt)
                if path_to_image and os.path.exists(path_to_image):
                    sent_msg = await client.send_file(DESTINATION, path_to_image, caption=news_text, parse_mode='html')
                    os.remove(path_to_image)
                else:
                    sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')
            else:
                sent_msg = await client.send_message(DESTINATION, news_text, parse_mode='html')

            if sent_msg:
                stats_db.increment('published')
                print(f"✅ ОПУБЛИКОВАНО! ID: {sent_msg.id}")
                
                essence = news_text
                if "<blockquote>" in news_text:
                    try: essence = news_text.split("<blockquote>")[1].split("</blockquote>")[0].strip()
                    except: pass
                save_to_history(essence)
                
                if reaction:
                    await asyncio.sleep(2)
                    try:
                        await client(functions.messages.SendReactionRequest(
                            peer=DESTINATION, msg_id=sent_msg.id, reaction=[types.ReactionEmoji(emoticon=reaction)]
                        ))
                    except: pass

                if poll_data:
                    await asyncio.sleep(1)
                    try:
                        await client.send_message(DESTINATION, file=types.InputMediaPoll(
                            poll=types.Poll(id=1, question=poll_data["q"], answers=[types.PollAnswer(text=o, option=bytes([i])) for i, o in enumerate(poll_data["o"])])
                        ))
                    except: pass
        except Exception as e:
            print(f"❌ Ошибка отправки: {e}")
            stats_db.increment('rejected_other')
