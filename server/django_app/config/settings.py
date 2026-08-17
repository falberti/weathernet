"""
Django settings for the WeatherNet server.

Configuration is read from environment variables (populated from
server/.env by docker-compose) rather than hardcoded, so the same image
can be deployed with different secrets/hosts without a code change.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name, default=""):
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

# Deliberately defaults to False: this app is only ever meant to run in
# production-like environments (see PROJECT_SPEC.md Section 5.6).
DEBUG = _env_bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")

# Django is never reachable except through nginx (see the trust-boundary
# comment in telemetry/views.py for the same reasoning), which always
# sets X-Forwarded-Proto -- so this header can be trusted. Without it,
# request.is_secure() is always False (the django<->nginx hop is plain
# HTTP), which breaks admin login: Django's CSRF Origin check compares
# the browser's real "https://..." Origin against what it thinks the
# scheme is, and rejects the request as cross-origin when they disagree.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Now that is_secure() is correct, these can be enforced: the admin is
# only ever actually served over HTTPS (see server/nginx), so cookies
# should never go out over plain HTTP.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "probes",
    "telemetry",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves collected static assets (Admin's CSS/JS) directly from
    # gunicorn, since nginx proxies everything except /api/v1/ straight
    # through to Django rather than serving /static/ itself.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("DB_HOST", "postgres"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    # Identity for the ingestion endpoint comes from the mTLS client
    # certificate (verified by nginx), not from a DRF auth class -- see
    # the trust-boundary comment in telemetry/views.py.
}

# Django's own default logging only sends request-handling exceptions
# (i.e. anything that becomes a 500) to the console when DEBUG=True --
# with DEBUG=False (correct for this deployment) they'd otherwise only
# go to mail_admins, which does nothing without ADMINS configured. That
# makes `docker compose logs django` useless for exactly the errors an
# operator most needs to see. Force a console handler regardless of
# DEBUG so tracebacks always end up in the container's logs.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# Used by the telemetry app's migrations to configure TimescaleDB
# compression/retention policies (PROJECT_SPEC.md Section 5.3).
TELEMETRY_RETENTION_DAYS = int(os.environ.get("TELEMETRY_RETENTION_DAYS", "90"))
TELEMETRY_COMPRESS_AFTER_DAYS = int(os.environ.get("TELEMETRY_COMPRESS_AFTER_DAYS", "7"))

# --- Zero-touch probe enrollment (PROJECT_SPEC.md Section 5.7) ---
ENROLLMENT_TOKEN_TTL_MINUTES = int(os.environ.get("ENROLLMENT_TOKEN_TTL_MINUTES", "30"))

# The server's own public IP and WireGuard listen port -- needed to
# build the enroll response's server_url/server_endpoint (probes/views.py)
# and the ready-to-paste command admin.py prints when a token is created.
SERVER_PUBLIC_IP = os.environ.get("SERVER_PUBLIC_IP", "")
WIREGUARD_SUBNET = os.environ.get("WIREGUARD_SUBNET", "10.10.0.0/24")
WIREGUARD_LISTEN_PORT = int(os.environ.get("WIREGUARD_LISTEN_PORT", "51820"))

# The CA's key and cert, and the server's WireGuard public key, are
# bind-mounted read-only into this container (see docker-compose.yml)
# so probes/ca.py can sign CSRs and probes/wireguard.py can hand back
# the server's WireGuard identity during enrollment. This is the one
# meaningful new attack-surface trade-off zero-touch enrollment
# introduces -- see PROJECT_SPEC.md Section 5.5/5.7 for the reasoning.
WEATHERNET_PKI_DIR = os.environ.get("WEATHERNET_PKI_DIR", "/etc/weathernet/pki")
CA_KEY_PATH = os.path.join(WEATHERNET_PKI_DIR, "ca.key.pem")
CA_CERT_PATH = os.path.join(WEATHERNET_PKI_DIR, "ca.cert.pem")
# Only the cert (public), not the server's private key -- read to compute
# the SHA-256 fingerprint admin.py prints for TLS pinning, so the
# operator never has to compute or copy it by hand.
SERVER_CERT_PATH = os.path.join(WEATHERNET_PKI_DIR, "server.cert.pem")
WIREGUARD_SERVER_PUBLIC_KEY_PATH = os.environ.get(
    "WIREGUARD_SERVER_PUBLIC_KEY_PATH", "/etc/weathernet/wireguard/server_public.key"
)
