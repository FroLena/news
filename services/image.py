import httpx
import urllib.parse
import os
import asyncio
from config import BASE_DIR

async def generate_image(prompt_text):
    clean_prompt = prompt_text.replace('|||', '').replace('=== ПРОМПТ ===', '').strip()
    tech_suffix = " . Shot on Phase One XF IQ4, 150MP, ISO 100, f/8, crystal clear, sharp focus, professional stock photography, no grain, no blur, bright lighting."
    final_prompt = clean_prompt + tech_suffix
    
    import random
    seed = random.randint(1, 1000000)
    filename = os.path.join(BASE_DIR, f"image_{seed}.jpg")
    
    encoded_prompt = urllib.parse.quote(final_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&model=flux&seed={seed}&nologo=true"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    for i in range(3):
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http_client:
            try:
                print(f"🎨 Рисую... ({i+1}/3)")
                response = await http_client.get(url, headers=headers)
                if response.status_code == 200:
                    with open(filename, "wb") as f: f.write(response.content)
                    if os.path.getsize(filename) > 0:
                        return filename
            except: pass
            await asyncio.sleep(2)
    return None
