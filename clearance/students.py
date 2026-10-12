import csv, io, re, secrets
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import TeacherLoad, User

ALPHA = "abcdefghjkmnpqrstuvwxyz23456789"
NONE = {"", "—", "–", "-", "n/a", "none"}


def newpw():
    return "".join(secrets.choice(ALPHA) for _ in range(8))


def creds_path():
    return settings.DATA_DIR / "credentials.csv"


def parse_csv(raw):
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        name = r.get("name") or r.get("student name") or ""
        if name:
            out.append({"name": name, "year": r.get("year", ""), "section": r.get("section", ""),
                        "no": r.get("student no") or r.get("student_no") or "",
                        "program": (r.get("program") or r.get("course") or "").upper()})
    return out


def _group(year, section):
    y = re.sub(r"\D", "", year)
    g = f"Year {y}" if y else "Unassigned"
    return g + (f" - {section}" if section.lower() not in NONE else "")


def import_students(rows):
    """Create student accounts. Returns (creds, skipped). Initial passwords are random; they exist only in `creds`."""
    yr = timezone.localdate().year
    have = User.objects.filter(role="student")
    existing = {(u.last_name.lower(), u.first_name.lower(), u.grade_level.lower()) for u in have}
    nos = set(User.objects.values_list("username", flat=True))
    seq = max([int(n.split("-")[1]) for n in nos if re.fullmatch(rf"{yr}-\d+", n)] or [0])
    creds, skipped = [], 0
    with transaction.atomic():
        for r in rows:
            last, _, first = r["name"].partition(",")
            if not first:
                p = r["name"].split(); last, first = p[-1], " ".join(p[:-1])
            last, first, gl = last.strip(), first.strip(), _group(r["year"], r["section"])
            key = (last.lower(), first.lower(), gl.lower())
            if key in existing:
                skipped += 1; continue
            existing.add(key)
            no = r["no"]
            while not no or no in nos:
                seq += 1; no = f"{yr}-{seq:04d}"
            nos.add(no)
            pw = newpw()
            u = User(username=no, first_name=first[:150], last_name=last[:150], role="student", student_no=no, grade_level=gl,
                     program=r.get("program") if r.get("program") in ("BSCS", "BEED", "BSBA", "BSHM") else "")
            u.set_password(pw); u.save()
            creds.append((no, f"{last}, {first}", gl, pw))
    return creds, skipped


def save_creds(creds):
    p = creds_path(); new = not p.exists()
    with open(p, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["Username / Student No", "Name", "Class", "Initial password"])
        w.writerows(creds)


def assign_load(teacher, subject, group):
    n = 0
    for s in User.objects.filter(role="student", grade_level=group):
        _, new = TeacherLoad.objects.get_or_create(student=s, teacher=teacher, subject=subject)
        n += new
    return n
