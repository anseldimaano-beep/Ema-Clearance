from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from clearance.models import ClearanceItem, Notification, User
from clearance.notify import push


class Command(BaseCommand):
    help = "Remind signers (once a day) about signatures waiting longer than REMIND_AFTER_DAYS. Run daily from cron."

    def add_arguments(self, p):
        p.add_argument("--days", type=int, default=settings.REMIND_AFTER_DAYS)

    def handle(self, *a, days=3, **k):
        cut = timezone.now() - timedelta(days=days)
        items = ClearanceItem.objects.filter(status="pending", clearance__status="pending", clearance__released_at__lt=cut).select_related("clearance__student")
        users = list(User.objects.filter(is_active=True).exclude(role__in=("student", "admin", "owner")))
        counts = {}
        for i in items:
            if i.kind in ("registrar", "cashier") and i.hint:
                continue  # not their turn yet
            prog = i.clearance.student.program
            if i.kind == "teacher":
                rec = [i.signatory] if i.signatory_id else [u for u in users if u.role == "registrar"]
            elif i.kind == "office":
                rec = [u for u in users if u.role == "office" and u.office == i.office and (not u.scopes or prog in u.scopes)]
            else:
                rec = [u for u in users if u.role == i.kind]
            for u in rec:
                counts[u.id] = counts.get(u.id, 0) + 1
        since = timezone.now() - timedelta(hours=20)
        by_id = {u.id: u for u in users}
        pairs = []
        for uid, n in counts.items():
            u = by_id.get(uid)
            if not u or Notification.objects.filter(user=u, text__startswith="⏰", created__gt=since).exists():
                continue
            pairs.append((u, f"⏰ {n} clearance{'s' if n != 1 else ''} waiting more than {days} days for your signature", "/inbox/"))
        push(pairs)
        self.stdout.write(f"Reminded {len(pairs)} people.")
