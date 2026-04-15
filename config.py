import os

SECRET_KEY = os.environ.get("SECRET_KEY", "sachinova-timecard-secret-key-change-me")

# Google Sheets設定
GOOGLE_CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Sachinova_タイムカード")

# 店舗パスワード（1日1回認証）
STORE_PASSWORDS = {
    "kiyosumi": os.environ.get("STORE_PW_KIYOSUMI", "kiyosumi2026"),
    "hikifune": os.environ.get("STORE_PW_HIKIFUNE", "hikifune2026"),
}

STORE_NAMES = {
    "kiyosumi": "清澄白河店",
    "hikifune": "曳舟店",
}

# セッション失効時刻（毎日この時刻にリセット）
SESSION_RESET_HOUR = 4  # AM 4:00

# デフォルト管理者
DEFAULT_ADMIN_NAME = "管理者"
DEFAULT_ADMIN_EMAIL = "admin@sachinova.co.jp"
DEFAULT_ADMIN_PASSWORD = "admin123"

# ━━━━━ LINE Messaging API ━━━━━
LINE_MESSAGING_ENABLED = os.environ.get("LINE_MESSAGING_ENABLED", "false").lower() == "true"
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_ADMIN_USER_IDS = [
    uid.strip()
    for uid in os.environ.get("LINE_ADMIN_USER_IDS", "").split(",")
    if uid.strip()
]

# 各通知のON/OFF
LINE_NOTIFY_SETTINGS = {
    "forgot_clock_out": True,       # 退勤忘れアラート
    "late_night_punch": True,       # 深夜異常打刻（AM2-5時）
    "pin_failure": True,            # PIN連続ミス
    "monthly_reminder": True,       # 月次締め日リマインド
    "backup_failure": True,         # バックアップ失敗
    "new_employee_first": True,     # 新規従業員初打刻
}

# PIN連続ミスのロック設定
PIN_MAX_FAILURES = 5
PIN_LOCK_MINUTES = 10

# 自動バックアップ
BACKUP_ENABLED = os.environ.get("BACKUP_ENABLED", "true").lower() == "true"
BACKUP_FOLDER_NAME = "timecard_backups"
BACKUP_RETENTION_DAYS = 30

# キャッシュ設定
EMPLOYEE_CACHE_TTL = 300  # 5分

# 顔写真アップロード
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "photos")
MAX_PHOTO_SIZE = 2 * 1024 * 1024  # 2MB
PHOTO_MAX_DIMENSION = 300  # px

# タイムゾーン
TIMEZONE = "Asia/Tokyo"
