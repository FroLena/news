from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telethon import TelegramClient
import pytz
from stats import stats_db  # Импортируем нашу БД

# ID твоего канала, куда слать отчет (или username строкой)
# Можно также использовать 'me' для тестов в Избранное
REPORT_DESTINATION = '@s_ostatok' # Поменяй на ID канала или username, например '@my_news_channel'

async def send_daily_report(client: TelegramClient):
    """Формирует и отправляет отчет"""
    data = stats_db.get_stats()
    
    if not data:
        # Если за сегодня данных нет, ничего не шлем или шлем пустой отчет
        return

    # Расчет сэкономленного времени (примерно 2 мин на пост)
    saved_minutes = (data['scanned'] - data['published']) * 2
    saved_hours = round(saved_minutes / 60, 1)

    text = (
        f"🌙 **Итоги дня: {data['date']}**\n\n"
        f"Сегодня я просеял для вас весь информационный шум.\n\n"
        f"📊 **Сухие цифры:**\n"
        f"• Просканировано постов: {data['scanned']}\n"
        f"• Опубликовано в канале: {data['published']}\n"
        f"• Отсеяно мусора: {data['scanned'] - data['published']}\n"
        f"  ├ 🛑 Реклама: {data['rejected_ads']}\n"
        f"  ├ 👯 Дубли: {data['rejected_dups']}\n"
        f"  └ 📉 Несущественное: {data['rejected_other']}\n\n"
        f"⏳ **Ваша выгода:**\n"
        f"Вы сэкономили ~{saved_hours} часа времени, не читая лишнее.\n"
        f"Спокойной ночи! 🤖"
    )

    try:
        await client.send_message(REPORT_DESTINATION, text)
        print("Ежедневный отчет отправлен.")
    except Exception as e:
        print(f"Ошибка отправки отчета: {e}")

def start_scheduler(client: TelegramClient):
    scheduler = AsyncIOScheduler()
    
    # Задача: каждый день в 21:30 по Москве
    scheduler.add_job(
        send_daily_report,
        trigger=CronTrigger(hour=21, minute=30, timezone=pytz.timezone('Europe/Moscow')),
        args=[client]
    )
    
    scheduler.start()
    print("Планировщик запущен (21:30 MSK).")
