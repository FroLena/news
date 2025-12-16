import httpx
import asyncio
from config import OPENAI_KEY, AI_MODEL

async def ask_gpt(system_prompt, user_text):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://amvera.ru",
        "X-Title": "NewsBot"
    }
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}]
    }
    
    last_error = None
    for i in range(3):
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            try:
                response = await http_client.post(url, headers=headers, json=payload)
                if response.status_code == 200:
                    return response.json()['choices'][0]['message']['content']
            except Exception as e:
                last_error = e
            await asyncio.sleep(5)
            
    print(f"❌ GPT Error: {last_error}")
    return None
