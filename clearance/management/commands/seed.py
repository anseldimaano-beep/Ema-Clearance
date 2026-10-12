from django.core.management.base import BaseCommand
from clearance.models import *

PW = "ema12345"


class Command(BaseCommand):
    help = "Create demo accounts for every role, a term, and sample students"

    def handle(self, *a, **k):
        def mk(u, role, first, last, **x):
            o, new = User.objects.get_or_create(username=u, defaults=dict(role=role, first_name=first, last_name=last, **x))
            if new:
                o.set_password(PW); o.save()
            return o
        mk("admin", "admin", "System", "Admin", is_staff=True, is_superuser=True)
        mk("owner", "owner", "School", "Owner")
        mk("registrar", "registrar", "Reg", "Istrar")
        mk("cashier", "cashier", "Cash", "Ier")
        for p in ("BSCS", "BEED", "BSBA", "BSHM"):
            mk(f"head_{p.lower()}", "office", "Head", p, office="dept_head", scope=p)
        for u, o, n in [("dean", "dean", "College Dean"), ("dsa", "dsa", "Student Affairs"), ("dir_research", "dir_research", "Research"),
                        ("dir_instruction", "dir_instruction", "Instruction"), ("librarian", "librarian", "Librarian"),
                        ("elpt", "elpt", "ELPT"), ("eemg", "eemg", "EEMG President")]:
            mk(u, "office", n, "Demo", office=o)
        mk("dir_preservice", "office", "PreService", "Demo", office="dir_preservice")
        mk("intrams", "office", "Intramurals", "Demo", office="intrams")
        t1 = mk("t_math", "teacher", "Mara", "Math")
        t2 = mk("t_sci", "teacher", "Sam", "Science")
        term, _ = Term.objects.get_or_create(name="SY 2026-2027 Final Exam", defaults={"is_active": True, "semester": "1"})
        for u, f, l, no, prog in [("student1", "Juan", "Dela Cruz", "2026-9001", "BSCS"), ("student2", "Maria", "Santos", "2026-9002", "BEED")]:
            s = mk(u, "student", f, l, student_no=no, grade_level="Year 1 - A", program=prog)
            TeacherLoad.objects.get_or_create(student=s, teacher=t1, subject="Mathematics")
            TeacherLoad.objects.get_or_create(student=s, teacher=t2, subject="Science")
        self.stdout.write(f"Seeded. All demo passwords: {PW}")
