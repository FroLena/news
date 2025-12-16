import edge_tts
import os
from config import DESTINATION, PODCAST_FILE
from services.ai import ask_gpt

async def create_and_send_podcast(client):
    print("🎙 Готовлю подкаст...")
    try:
        history_posts = []
        async for message in client.iter_messages(DESTINATION, limit=30):
            if message.text: history_posts.append(message.text)
        
        if not history_posts: return
        full_text = "\n\n".join(history_posts[:20])

        system_prompt = (
            "Ты — профессиональный радиоведущий итогового шоу «Сухой остаток».\n"
            "Твоя задача: Создать увлекательный сценарий на основе предоставленных новостей за день.\n"
            "ТРЕБОВАНИЯ: Живой язык, без ссылок, 60-90 секунд.\n"
            "НАЧАЛО: 'Добрый вечер. В эфире Сухой остаток. Подведем итоги.'\n"
            "КОНЕЦ: 'Таким был этот день. До связи.'"
        )
        
        script = await ask_gpt(system_prompt, full_text)
        if not script: return

        script = script.replace('*', '').replace('#', '')
        communicate = edge_tts.Communicate(script, "ru-RU-DmitryNeural")
        
        await communicate.save(PODCAST_FILE)
        await client.send_file(DESTINATION, PODCAST_FILE, caption="🎙 <b>Итоги дня</b>", parse_mode='html', voice_note=True)
        if os.path.exists(PODCAST_FILE): os.remove(PODCAST_FILE)
    except Exception as e:
        print(f"❌ Ошибка подкаста: {e}")
