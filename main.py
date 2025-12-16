import time
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, SESSION_STRING
from handlers import register_handlers
from scheduler_setup import start_scheduler

if __name__ == '__main__':
    print("🚀 Запуск NewsBot Modular...")
    
    if not API_ID or not API_HASH:
        print("❌ Ошибка конфига: Проверь переменные окружения")
        time.sleep(30)
        exit(1)

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    
    register_handlers(client)
    
    client.start()
    start_scheduler(client)
    
    print("🤖 Бот работает в модульном режиме!")
    client.run_until_disconnected()
