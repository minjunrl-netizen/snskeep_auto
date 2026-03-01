import os
from dotenv import load_dotenv

load_dotenv()

# 카페24
CAFE24_MALL_ID = os.getenv("CAFE24_MALL_ID")
CAFE24_CLIENT_ID = os.getenv("CAFE24_CLIENT_ID")
CAFE24_CLIENT_SECRET = os.getenv("CAFE24_CLIENT_SECRET")
CAFE24_REDIRECT_URI = os.getenv("CAFE24_REDIRECT_URI", "http://localhost:5000/oauth/callback")
CAFE24_API_BASE = f"https://{CAFE24_MALL_ID}.cafe24api.com/api/v2"

# 인스타몬스터
INSTAMONSTER_API_KEY = os.getenv("INSTAMONSTER_API_KEY")
INSTAMONSTER_API_URL = os.getenv("INSTAMONSTER_API_URL", "https://instamonster.co.kr/api/v2")

# 인스타몬스터 Admin API v2 (프로필 추출용)
INSTAMONSTER_ADMIN_API_KEY = os.getenv("INSTAMONSTER_ADMIN_API_KEY")
INSTAMONSTER_ADMIN_API_URL = "https://instamonster.co.kr/adminapi/v2"

# Apify (인스타그램 프로필 스크래핑)
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")

# superap.io (캠페인 자동 등록)
SUPERAP_USERNAME = os.getenv("SUPERAP_USERNAME")
SUPERAP_PASSWORD = os.getenv("SUPERAP_PASSWORD")

# Flask
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

# DB
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, 'data', 'app.db')}"

# 텔레그램 알림
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 텔레그램 환불 알림 (별도 봇)
TELEGRAM_REFUND_BOT_TOKEN = os.getenv("TELEGRAM_REFUND_BOT_TOKEN", "")
TELEGRAM_REFUND_CHAT_ID = os.getenv("TELEGRAM_REFUND_CHAT_ID", "")

# 폴링
POLLING_INTERVAL = int(os.getenv("POLLING_INTERVAL", "90"))
