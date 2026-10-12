import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
if os.environ.get("RENDER_EXTERNAL_URL"):  # Render: trust the public https URL automatically
    CSRF_TRUSTED_ORIGINS.append(os.environ["RENDER_EXTERNAL_URL"])
if not DEBUG:  # behind Render's / any HTTPS proxy
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "clearance"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "clearance.context.nav"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR))
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "db.sqlite3",
                         "OPTIONS": {"timeout": 20}}}  # several gunicorn workers share the file: wait for a lock instead of failing
_url = os.environ.get("DATABASE_URL", "")
if _url.startswith(("postgres://", "postgresql://")):  # Neon (neon.tech) or any PostgreSQL
    from urllib.parse import parse_qs, unquote, urlparse
    _u = urlparse(_url); _q = {k: v[0] for k, v in parse_qs(_u.query).items()}
    _opt = {"sslmode": _q.get("sslmode", "require"), "connect_timeout": 20}  # Neon needs SSL; allow time for a suspended compute to wake up
    if _q.get("channel_binding"):
        _opt["channel_binding"] = _q["channel_binding"]
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": _u.path.lstrip("/"), "USER": unquote(_u.username or ""),
                             "PASSWORD": unquote(_u.password or ""), "HOST": _u.hostname, "PORT": _u.port or 5432, "OPTIONS": _opt,
                             "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": True, "DISABLE_SERVER_SIDE_CURSORS": True}}  # safe with Neon's pooled (-pooler) host
USING_SQLITE = DATABASES["default"]["ENGINE"].endswith("sqlite3")
PASSWORD_HASHERS = ["config.hashers.FastPBKDF2PasswordHasher", "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [{"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}}]
AUTH_USER_MODEL = "clearance.User"
LOGIN_URL = "login"; LOGIN_REDIRECT_URL = "home"; LOGOUT_REDIRECT_URL = "login"
TIME_ZONE = "Asia/Manila"; USE_TZ = True
STATIC_URL = "static/"; STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", ""); EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", ""); EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True; DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "EMA Clearance <no-reply@localhost>")

MEDIA_ROOT = DATA_DIR / "media"
REQUIRE_DEPT_HEAD = os.environ.get("REQUIRE_DEPT_HEAD", "1") == "1"  # set 0 if the Department Head does not sign the clearance form
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))  # per file: Student ID front/back, Registration Card
DATA_UPLOAD_MAX_MEMORY_SIZE = 12_000_000

# --- extras ---
OFFICES_NOT_FOR = {}  # e.g. OFFICES_NOT_FOR="dir_preservice:BSBA|BSHM;elpt:BSHM" -> that office does not sign for those programs
for _part in os.environ.get("OFFICES_NOT_FOR", "").split(";"):
    if ":" in _part:
        _o, _ps = _part.split(":", 1)
        OFFICES_NOT_FOR[_o.strip()] = {x.strip().upper() for x in _ps.split("|") if x.strip()}
SMS_API_KEY = os.environ.get("SMS_API_KEY", "")  # optional: Semaphore (semaphore.co) API key for student SMS
SMS_SENDER = os.environ.get("SMS_SENDER", "")
REMIND_AFTER_DAYS = int(os.environ.get("REMIND_AFTER_DAYS", "3"))
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "14"))
