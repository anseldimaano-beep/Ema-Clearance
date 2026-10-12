import csv
import os
import re
import unicodedata

from django.core.management.base import BaseCommand
from clearance.models import CurriculumSubject, TeacherLoad, User
from clearance.students import newpw, save_creds


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]


def username(surname):
    s = unicodedata.normalize("NFKD", surname).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


class Command(BaseCommand):
    help = ("Create one Subject Teacher account per surname from data/teachers.csv (existing ones are left alone). "
            "With --loads, also give each teacher their subjects per class from data/teacher_loads.csv.")

    def add_arguments(self, p):
        p.add_argument("--teachers", default=os.environ.get("TEACHERS_CSV", "data/teachers.csv"))
        p.add_argument("--loads", action="store_true", help="also assign subjects from data/teacher_loads.csv")
        p.add_argument("--show", action="store_true", help="print the new usernames and passwords here")
        p.add_argument("--loads-file", default=os.environ.get("TEACHER_LOADS_CSV", "data/teacher_loads.csv"))

    def handle(self, *a, **o):
        creds = []
        for r in read(o["teachers"]):
            name, un = r.get("last_name", ""), username(r.get("last_name", ""))
            if not un:
                continue
            u = User.objects.filter(username=un).first()
            if u:
                self.stdout.write(f"  exists: {un}")
                continue
            pw = newpw()
            u = User(username=un, role="teacher", last_name=name, first_name=r.get("title", ""))
            u.set_password(pw); u.save()
            creds.append((un, u.display, "Teacher", pw))
        if creds:
            save_creds(creds)
            if o["show"]:
                for un, name, _, pw in creds:
                    self.stdout.write(f"  {un:<12} {pw}   ({name})")
            self.stdout.write(f"Created {len(creds)} teacher accounts. Passwords: Registrar > Students > Download login list.")
        if o["loads"]:
            self.assign(o["loads_file"])

    @staticmethod
    def students(cls):
        """Students of a class. 'Year 2' matches every Year 2 student; 'Year 1 - A' only section A."""
        def parse(x):
            y = re.search(r"(\d)", x or "")
            sec = re.search(r"(?:^|[\s-])([A-Za-z])\s*$", re.sub(r"(?i)year", "", x or "").strip())
            return (y.group(1) if y else "", sec.group(1).upper() if sec else "")
        y, sec = parse(cls)
        return [s for s in User.objects.filter(role="student") if parse(s.grade_level)[0] == y and (not sec or parse(s.grade_level)[1] == sec)]

    def assign(self, path):
        if not os.path.exists(path):
            self.stdout.write(f"No loads file at {path}."); return
        made = 0
        for r in read(path):
            cls, code, who = r.get("class", ""), r.get("code", ""), r.get("teacher", "")
            if not who:
                self.stdout.write(f"  skipped {cls} / {code}: no teacher on the schedule"); continue
            t = User.objects.filter(role="teacher", username=username(who)).first()
            if not t:
                self.stdout.write(f"  skipped {cls} / {code}: no account for {who}"); continue
            if not CurriculumSubject.objects.filter(code__iexact=code).exists():
                self.stdout.write(f"  skipped {cls} / {code} ({t.display}): code not in the curriculum"); continue
            for s in self.students(cls):
                if not TeacherLoad.objects.filter(student=s, subject__iexact=code).exists():
                    TeacherLoad.objects.create(student=s, teacher=t, subject=code); made += 1
        self.stdout.write(f"Assigned {made} student-subject loads.")
