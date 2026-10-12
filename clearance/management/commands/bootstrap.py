import os
from django.core.management import call_command
from django.core.management.base import BaseCommand
from clearance.models import CurriculumSubject, User
from clearance.students import import_students, parse_csv, save_creds


class Command(BaseCommand):
    help = "First-run setup: create the registrar account and load the student list (idempotent)."

    def handle(self, *a, **k):
        weak = {"change-me-now", "change-me-too", "change-me", "admin", "password", "12345678"}
        for name in ("REGISTRAR_PASSWORD", "ADMIN_PASSWORD", "SECRET_KEY"):
            if os.environ.get(name, "") in weak:
                self.stderr.write(f"WARNING: {name} is still a default value. Set a strong one in .env before real use.")
        u, pw = os.environ.get("REGISTRAR_USERNAME", "registrar"), os.environ.get("REGISTRAR_PASSWORD")
        if pw and not User.objects.filter(role="registrar").exists():
            r = User.objects.create_user(u, password=pw, role="registrar", first_name="Registrar")
            self.stdout.write(f"Created registrar account '{r.username}'.")
        ap = os.environ.get("ADMIN_PASSWORD")
        if ap and not User.objects.filter(role="admin").exists():
            User.objects.create_user(os.environ.get("ADMIN_USERNAME", "admin"), password=ap, role="admin", first_name="System", last_name="Admin", is_staff=True, is_superuser=True)
            self.stdout.write("Created admin account.")
        cp = os.environ.get("CURRICULUM_CSV", "data/curriculum.csv")
        if os.path.exists(cp):
            import csv
            rows = list(csv.DictReader(open(cp, encoding="utf-8-sig")))
            have = set(CurriculumSubject.objects.values_list("program", "year", "semester"))
            added = 0
            for n, r in enumerate(rows):  # add a program/year/semester that is not loaded yet (never touches ones you edited)
                g = (r["program"].strip().upper(), int(r["year"]), int(r["semester"]))
                if g not in have:
                    CurriculumSubject.objects.get_or_create(program=g[0], year=g[1], semester=g[2], code=r["code"].strip(), defaults={"order": n})
                    added += 1
            if added:
                self.stdout.write(f"Loaded {added} curriculum subjects.")
            for r in rows:  # fill in descriptive titles (never overwrites one you edited in /admin)
                if (r.get("title") or "").strip():
                    CurriculumSubject.objects.filter(program=r["program"].strip().upper(), code=r["code"].strip(), title="").update(title=r["title"].strip())
        path = os.environ.get("STUDENTS_CSV")
        if path and os.path.exists(path) and not User.objects.filter(role="student").exists():
            creds, skipped = import_students(parse_csv(open(path, "rb").read()))
            save_creds(creds)
            self.stdout.write(f"Imported {len(creds)} students. Login list saved; download it from Students > Download login list.")
        if os.path.exists(os.environ.get("TEACHERS_CSV", "data/teachers.csv")) and not User.objects.filter(role="teacher").exists():
            call_command("seed_teachers", loads=True, stdout=self.stdout)
