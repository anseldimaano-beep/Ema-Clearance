import csv, io, re, secrets, unicodedata
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import ClearanceItem, TeacherLoad, User

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
    p = creds_path(); p.parent.mkdir(parents=True, exist_ok=True); new = not p.exists()
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


def name_key(s):
    """'ENGR. MAJABA' -> 'majaba', 'Bañez' -> 'banez' (matches the teacher usernames)."""
    s = " ".join(w for w in (s or "").split() if not w.endswith("."))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def find_teacher(name):
    k = name_key(name)
    if not k:
        return None
    return User.objects.filter(role="teacher", username=k).first() or next(
        (t for t in User.objects.filter(role="teacher") if name_key(t.last_name) == k), None)


def class_students(cls):
    """'Year 2' = every Year 2 student; 'Year 1 - A' = only section A."""
    def parse(x):
        y = re.search(r"(\d)", x or "")
        sec = re.search(r"(?:^|[\s-])([A-Za-z])\s*$", re.sub(r"(?i)year", "", x or "").strip())
        return (y.group(1) if y else "", sec.group(1).upper() if sec else "")
    y, sec = parse(cls)
    if not y:
        return []
    return [s for s in User.objects.filter(role="student") if parse(s.grade_level)[0] == y and (not sec or parse(s.grade_level)[1] == sec)]


def set_load(cls, subject, teacher):
    """Make `teacher` the teacher of `subject` for the class (None = remove the teacher). Returns how many students changed,
    or None if the class has no students. Signed signatures are never touched; pending ones follow the new teacher."""
    studs = class_students(cls)
    if not studs:
        return None
    ids = [s.pk for s in studs]
    q = TeacherLoad.objects.filter(student_id__in=ids, subject__iexact=subject)
    if teacher is None:
        n = q.count(); q.delete()
    else:
        have = set(q.values_list("student_id", flat=True))
        n = q.exclude(teacher=teacher).count()
        q.exclude(teacher=teacher).update(teacher=teacher)
        TeacherLoad.objects.bulk_create([TeacherLoad(student_id=i, teacher=teacher, subject=subject) for i in ids if i not in have])
        n += len(ids) - len(have)
    ClearanceItem.objects.filter(clearance__student_id__in=ids, kind="teacher", label__iexact=subject).exclude(status="approved").update(signatory=teacher)
    return n


def parse_loads(raw, filename):
    """Read class / code / teacher rows from a .csv or .xlsx upload. Returns a list of (class, code, teacher)."""
    if filename.lower().endswith(".xlsx"):
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True).active
        rows = [[("" if c is None else str(c)).strip() for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    head = [h.lower() for h in rows[0]]
    def col(*names):
        return next((head.index(n) for n in names if n in head), None)
    ci, cc, ct = col("class", "section", "group"), col("code", "course no.", "course", "subject"), col("teacher", "instructor")
    if None in (ci, cc, ct):
        raise ValueError("The first row must have the headers: class, code, teacher")
    return [(r[ci] if ci < len(r) else "", r[cc] if cc < len(r) else "", r[ct] if ct < len(r) else "") for r in rows[1:]]
