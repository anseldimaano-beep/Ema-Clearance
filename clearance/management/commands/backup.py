from django.core.management.base import BaseCommand
from clearance.backup import make_backup


class Command(BaseCommand):
    help = "Save a consistent copy of the database to DATA_DIR/backups (keeps the newest BACKUP_KEEP)."

    def handle(self, *a, **k):
        from django.conf import settings
        if not settings.USING_SQLITE:
            self.stdout.write("Database is PostgreSQL (Neon): use Neon's backups / point-in-time restore. Nothing to do here.")
            return
        if not settings.DATABASES["default"]["NAME"] or not __import__("os").path.exists(str(settings.DATABASES["default"]["NAME"])):
            self.stdout.write("No database yet; nothing to back up.")
            return
        self.stdout.write(f"Backup saved: {make_backup()}")
