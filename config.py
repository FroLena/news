import os
import pytz

# API Keys
API_ID = int(os.environ.get('TG_API_ID', 0))
API_HASH = os.environ.get('TG_API_HASH')
SESSION_STRING = os.environ.get('TG_SESSION_STR')
OPENAI_KEY = os.environ.get('OPENAI_API_KEY')

# Channels
SOURCE_CHANNELS = [
    'rian_ru', 'rentv_channel', 'breakingmash', 'bazabazon', 
    'shot_shot', 'ostorozhno_novosti', 'rbc_news'
]
DESTINATION = '@s_ostatok'

# Paths
BASE_DIR = '/data' if os.path.exists('/data') else '.'
DB_PATH = os.path.join(BASE_DIR, 'stats.db')
HISTORY_FILE = os.path.join(BASE_DIR, 'history.json')
PODCAST_FILE = os.path.join(BASE_DIR, 'podcast.mp3')

# Settings
MSK_TZ = pytz.timezone('Europe/Moscow')
AI_MODEL = "openai/gpt-4o-mini"
MAX_VIDEO_SIZE = 50 * 1024 * 1024
