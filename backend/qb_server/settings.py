"""题有据：仅本机使用的设置。"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("QB_DJANGO_SECRET_KEY", "local-only-question-bank-card")
DEBUG = os.environ.get("QB_DEBUG", "0") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
INSTALLED_APPS = ["django.contrib.contenttypes", "core"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "qb_server.urls"
TEMPLATES = []
WSGI_APPLICATION = "qb_server.wsgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(os.environ.get("QB_DATABASE", BASE_DIR / "db.sqlite3")),
        # IMMEDIATE, not the default DEFERRED: a deferred transaction opens as a
        # reader and only asks for the write lock when it first writes.  If
        # another connection has written in between, SQLite reports "database is
        # locked" straight away and the busy handler never runs, so ``timeout``
        # cannot help.  Taking the write lock at BEGIN makes every writer queue
        # behind ``timeout`` instead of failing — which is what killed a MinerU
        # run whose heartbeat happened to land on a locked database.
        "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE"},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = "Asia/Shanghai"
USE_TZ = True
DATA_ROOT = Path(os.environ.get("QB_DATA_ROOT", BASE_DIR / "data")).resolve()
FRONTEND_ROOT = Path(os.environ.get("QB_FRONTEND_ROOT", BASE_DIR.parent / "frontend")).resolve()
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# 长 PDF 会在本机流式保存后按页切片；源文件大小仍遵守 MinerU 当前的 200 MB 上限。
MAX_PDF_UPLOAD_BYTES = 200_000_000
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"core": {"handlers": ["console"], "level": "INFO"}},
}
