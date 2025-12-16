import httpx
import urllib.parse
import os
import asyncio
from config import BASE_DIR

async def generate_image(prompt_text):
    # Чистим промпт от мусора GPT
    clean_prompt = prompt_text.replace('|||', '').replace('=== ПРОМПТ ===', '').strip()
    
    # СУФФИКС ДЛЯ КАЧЕСТВА (Здесь магия резкости)
    # Добавляем HDR, Ray Tracing, Sharp focus, чтобы убрать мыло
    tech_suffix = " . Hyper-realistic, 8k resolution, ray tracing, sharp focus, incredibly detailed, cinematic lighting, shot on 35mm lens, depth of field, f/1.8, high contrast, professional photography."
    
    # Собираем
    final_prompt = clean_prompt + tech_suffix
    encoded_prompt = urllib.parse.quote(final_prompt)
    
    import random
    seed = random.randint(1, 1000000)
    filename = os.path.join(BASE_DIR, f"image_{seed}.jpg")
    
    # URL с настройками:
    # model=flux (самая стабильная)
    # enhance=true (включает авто-улучшение промпта на стороне сервера)
    # nologo=true (убирает вотермарки)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&model=flux&seed={seed}&nologo=true&enhance=true"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    for i in range(3):
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http_client:
            try:
                print(f"🎨 Рисую (Попытка {i+1})...")
                response = await http_client.get(url, headers=headers)
                if response.status_code == 200:
                    with open(filename, "wb") as f: f.write(response.content)
                    # Проверяем, что файл не пустой
                    if os.path.getsize(filename) > 1000: 
                        return filename
            except Exception as e:
                print(f"⚠️ Ошибка генерации: {e}")
            await asyncio.sleep(2)
            
    return None
