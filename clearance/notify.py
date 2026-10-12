import re
import threading
import urllib.parse
import urllib.request
from django.conf import settings
from django.core.mail import send_mass_mail
from django.utils import timezone
from .models import AuditLog, Notification, User


def audit(user, action, clearance=None, target="", detail=""):
    AuditLog.objects.create(user=user if getattr(user, "pk", None) else None, action=action, clearance=clearance,
                            student_name=(clearance.student.display if clearance else "")[:160], target=target[:120], detail=detail[:255])


def norm_phone(v):
    """09171234567 / +639171234567 / 639171234567 -> 09171234567 (empty if it is not a PH mobile number)."""
    v = re.sub(r"[\s\-()]", "", v or "")
    m = re.fullmatch(r"(?:\+?63|0)(9\d{9})", v)
    return "0" + m.group(1) if m else ""


def _send_sms(items):
    key = settings.SMS_API_KEY
    for number, text in items:
        try:
            data = {"apikey": key, "number": number, "message": text}
            if settings.SMS_SENDER:
                data["sendername"] = settings.SMS_SENDER
            urllib.request.urlopen(urllib.request.Request("https://api.semaphore.co/api/v4/messages",
                                   data=urllib.parse.urlencode(data).encode()), timeout=10).read()
        except Exception:
            pass


def push(pairs, sms=False):
    """pairs: [(user, text, url)] -> in-app notification + email (if the user has an address) + SMS (sms=True, needs SMS_API_KEY)."""
    pairs = list(pairs)
    if not pairs:
        return
    Notification.objects.bulk_create([Notification(user=u, text=t[:200], url=url) for u, t, url in pairs])
    mails = [("EMA Clearance", t, None, [u.email]) for u, t, _ in pairs if u.email]
    if mails:
        send_mass_mail(mails, fail_silently=True)
    if sms and settings.SMS_API_KEY:
        out = [(norm_phone(u.phone), "EMA Clearance: " + t[:150]) for u, t, _ in pairs if norm_phone(u.phone)]
        if out:
            threading.Thread(target=_send_sms, args=(out,), daemon=True).start()


def release(clearances, by):
    """Registrar releases requested clearances: every signing account (and owner/admin) is notified. Students are not."""
    cl = [c for c in clearances if c.status == "requested"]
    if not cl:
        return 0
    users = list(User.objects.filter(is_active=True).exclude(role="student"))
    offices = [u for u in users if u.role == "office"]
    cashiers = [u for u in users if u.role == "cashier"]
    counts, now = {}, timezone.now()
    for c in cl:
        c.sync()
        c.status, c.released_at, c.released_by, c.note = "pending", now, by, ""
        c.save(update_fields=["status", "released_at", "released_by", "note"])
        prog = c.student.program
        for i in c.items.all():
            if i.kind == "teacher" and i.signatory_id:
                rec = [i.signatory]
            elif i.kind == "office":
                rec = [u for u in offices if u.office == i.office and (not u.scopes or prog in u.scopes)]
            elif i.kind == "cashier":
                rec = cashiers
            else:
                rec = []
            for u in rec:
                counts[u.id] = counts.get(u.id, 0) + 1
    n, pairs, by_id = len(cl), [], {u.id: u for u in users}
    for uid, k in counts.items():
        pairs.append((by_id[uid], f"{k} student clearance{'s' if k > 1 else ''} waiting for your signature", "/inbox/"))
    for u in users:
        if u.id not in counts and u.id != by.id and u.role in ("owner", "admin", "registrar"):
            pairs.append((u, f"{n} clearance{'s' if n > 1 else ''} released to the signing offices by {by.display}", "/overview/"))
    push(pairs)
    push([(c.student, "Your clearance was released to your teachers and offices. Track the signatures on your page.", "/student/") for c in cl], sms=True)
    for c in cl:
        audit(by, "released", c)
    return n
