from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from database import stats_db
from config import DESTINATION, MSK_TZ
from services.podcast import create_and_send_podcast

async def send_daily_report(client):
    data = stats_db.get_stats()
    if data:
        text = (
            f"🌙 **Итоги дня: {data['date']}**\n\n"
            f"📊 Просканировано: {data['scanned']}\n"
            f"✅ Опубликовано: {data['published']}\n"
            f"🗑 Мусора отсеяно: {data['rejected_ads'] + data['rejected_dups'] + data['rejected_other']}\n"
            f"Спокойной ночи! 🤖"
        )
        try: await client.send_message(DESTINATION, text)
        except: pass

def start_scheduler(client):
    scheduler = AsyncIOScheduler(event_loop=client.loop)
    scheduler.add_job(create_and_send_podcast, 'cron', hour=18, minute=0, args=[client])
    scheduler.add_job(send_daily_report, CronTrigger(hour=0, minute=0, timezone=MSK_TZ), args=[client])
    scheduler.start()
