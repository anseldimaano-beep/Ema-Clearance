import sqlite3
import tempfile
import time
from datetime import datetime
from django.conf import settings


def enabled():
    return settings.USING_SQLITE  # on PostgreSQL / Neon, use Neon's own backups and branches (console.neon.tech)


def _dir():
    d = settings.DATA_DIR / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot(dest):
    """Consistent copy of the live SQLite database (safe while the site is running)."""
    src = sqlite3.connect(str(settings.DATABASES["default"]["NAME"]), timeout=30)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close(); src.close()


def make_backup(keep=None):
    if not enabled():
        return None
    keep = keep or settings.BACKUP_KEEP
    d = _dir()
    out = d / f"db-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    tmp = out.with_suffix(".tmp")
    snapshot(tmp); tmp.replace(out)
    files = sorted(d.glob("db-*.sqlite3"))
    for old in files[:-keep]:
        old.unlink(missing_ok=True)
    return out


def maybe_backup(hours=24):
    """Cheap check called on login: make a backup if the newest one is older than `hours`."""
    if not enabled():
        return
    try:
        files = sorted(_dir().glob("db-*.sqlite3"))
        if not files or time.time() - files[-1].stat().st_mtime > hours * 3600:
            make_backup()
    except Exception:
        pass


def temp_snapshot():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    f.close()
    snapshot(f.name)
    return f.name


def maybe_remind(hours=24):
    """Once a day (on the first login after that), remind signers about old pending signatures. No cron needed."""
    try:
        stamp = settings.DATA_DIR / "reminders.stamp"
        if stamp.exists() and time.time() - stamp.stat().st_mtime < hours * 3600:
            return
        stamp.touch()
        from django.core.management import call_command
        call_command("send_reminders", verbosity=0)
    except Exception:
        pass
