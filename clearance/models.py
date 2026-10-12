import os
import uuid
from decimal import Decimal
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Sum
from django.utils import timezone


def doc_path(instance, filename):
    return f"docs/{uuid.uuid4().hex}{os.path.splitext(filename)[1].lower()}"


def new_code():
    return uuid.uuid4().hex[:10].upper()


PROGRAMS = [("BSCS", "BSCS"), ("BEED", "BEED"), ("BSBA", "BSBA"), ("BSHM", "BSHM")]
OFFICES = [("dept_head", "Department Head"), ("dean", "College Dean"), ("dsa", "Dean of Student Affairs"),
           ("dir_research", "Director of Research"), ("dir_instruction", "Director of Instruction"),
           ("dir_preservice", "Director of Pre-Service Teaching"), ("librarian", "Librarian"),
           ("elpt", "ELPT Office (English Language Proficiency Test)"), ("eemg", "EEMG President"), ("intrams", "INTRAMS (Intramurals)")]
OFFICE_LABEL = dict(OFFICES)
EVERYONE = ["dir_research", "dir_instruction", "dir_preservice", "eemg", "elpt", "intrams", "librarian", "dsa", "dean"]  # sign for every student, in the order of the paper form
PROGRAM_NAMES = {"BSCS": "Bachelor of Science in Computer Science", "BEED": "Bachelor of Elementary Education",
                 "BSBA": "Bachelor of Science in Business Administration", "BSHM": "Bachelor of Science in Hospitality Management"}
RANK = {"teacher": 1, "office": 2, "registrar": 8, "cashier": 9}  # the Cashier signs last


class User(AbstractUser):
    ROLES = [("admin", "System Admin"), ("owner", "School Owner"), ("registrar", "Registrar"), ("cashier", "Cashier"),
             ("office", "Signing Office"), ("teacher", "Subject Teacher"), ("student", "Student")]
    role = models.CharField(max_length=12, choices=ROLES, default="student")
    office = models.CharField(max_length=20, choices=OFFICES, blank=True, help_text="Only for role 'Signing Office'")
    scope = models.CharField(max_length=60, blank=True, help_text="Programs this office covers, comma-separated (e.g. BSCS). Blank = all.")
    program = models.CharField(max_length=10, choices=PROGRAMS, blank=True, help_text="Student's program")
    student_no = models.CharField(max_length=30, blank=True)
    grade_level = models.CharField(max_length=40, blank=True)
    position = models.CharField("position/title", max_length=80, blank=True)
    signup = models.BooleanField(default=False, help_text="Self-registered, waiting for Registrar approval")
    phone = models.CharField(max_length=20, blank=True, help_text="Mobile number for SMS (optional)")

    @property
    def scopes(self):
        return [x.strip().upper() for x in self.scope.split(",") if x.strip()]

    @property
    def program_name(self):
        return PROGRAM_NAMES.get(self.program, self.program)

    @property
    def display(self):
        return self.get_full_name() or self.username

    @property
    def title(self):
        return OFFICE_LABEL.get(self.office, "") + (f" – {self.scope}" if self.office == "dept_head" and self.scope else "") if self.role == "office" else self.get_role_display()


class Term(models.Model):
    name = models.CharField(max_length=60, help_text="e.g. SY 2026-2027 Final Exam")
    semester = models.CharField(max_length=1, blank=True, choices=[("1", "1st Semester"), ("2", "2nd Semester")])
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class CurriculumSubject(models.Model):
    """Subjects of a program by year and semester (students tick the ones they take now)."""
    program = models.CharField(max_length=10, choices=PROGRAMS)
    year = models.PositiveSmallIntegerField()
    semester = models.PositiveSmallIntegerField()
    code = models.CharField(max_length=40)
    title = models.CharField("descriptive title", max_length=120, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["program", "year", "semester", "order", "id"]
        unique_together = ("program", "year", "semester", "code")

    def __str__(self):
        return f"{self.program} Y{self.year}S{self.semester} {self.code}"


class TeacherLoad(models.Model):
    """Which teacher teaches which subject to which student (managed by Registrar in admin)."""
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="loads", limit_choices_to={"role": "student"})
    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name="teaching", limit_choices_to={"role": "teacher"})
    subject = models.CharField(max_length=80)

    def __str__(self):
        return f"{self.student} - {self.subject} ({self.teacher})"


# the three optional documents a student may attach (key, label, model field)
DOC_FIELDS = (("id", "Student ID (front)", "id_file"), ("idback", "Student ID (back, validation)", "idback_file"),
              ("copy", "Registration card (student's copy)", "copy_file"))


class Clearance(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="clearances")
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    assessed = models.DecimalField("total assessed fees", max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, default="requested")  # requested | returned | pending | cleared
    note = models.CharField(max_length=200, blank=True)
    id_file = models.FileField("Student ID (front)", upload_to=doc_path, blank=True)
    idback_file = models.FileField("Student ID (back, validation)", upload_to=doc_path, blank=True)
    copy_file = models.FileField("Student copy", upload_to=doc_path, blank=True)
    docs_wanted = models.CharField(max_length=40, blank=True, default="")  # keys from DOC_FIELDS the Registrar asked for
    docs_note = models.CharField(max_length=100, blank=True, default="")
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    code = models.CharField(max_length=10, unique=True, default=new_code)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student", "term")

    @property
    def paid(self):
        return self.payments.aggregate(t=Sum("amount"))["t"] or Decimal("0")

    @property
    def balance(self):
        return max(self.assessed - self.paid, Decimal("0"))

    @property
    def addable_subjects(self):
        """Curriculum subjects of the student's program that are not on this clearance yet (for the Registrar to add)."""
        if not self.student.program:
            return []
        have = {l.lower() for l in self.items.filter(kind="teacher").values_list("label", flat=True)}
        return [s for s in CurriculumSubject.objects.filter(program=self.student.program) if s.code.lower() not in have]

    @property
    def doc_options(self):
        return [(k, lbl, bool(getattr(self, f))) for k, lbl, f in DOC_FIELDS]

    @property
    def missing_docs(self):
        return [lbl for k, lbl, has in self.doc_options if not has]

    @property
    def wanted_docs(self):
        keys = self.docs_wanted.split(",")
        return [(k, lbl) for k, lbl, _ in DOC_FIELDS if k in keys]

    @property
    def total(self):
        return self.items.count()

    @property
    def done(self):
        return self.items.filter(status="approved").count()

    @property
    def percent(self):
        return int(self.done * 100 / self.total) if self.total else 0

    @property
    def pay_percent(self):
        return min(int(self.paid * 100 / self.assessed), 100) if self.assessed else 0

    @property
    def waiting(self):
        return [i.label for i in self.items.exclude(status="approved")]

    @property
    def rejected(self):
        return self.items.filter(status="rejected")

    def refresh_status(self):
        if self.status in ("requested", "returned"):
            return
        self.status = "cleared" if not self.items.exclude(status="approved").exists() else "pending"
        self.save(update_fields=["status"])

    @staticmethod
    def uses_curriculum(student):
        return bool(student.program) and CurriculumSubject.objects.filter(program=student.program).exists()

    @classmethod
    def build(cls, student, term, subjects=None):
        c = cls.objects.create(student=student, term=term)
        c.set_subjects(subjects)
        c.sync()
        return c

    def set_subjects(self, codes):
        """Create one teacher signature per subject the student ticked. The teacher comes from the Registrar's teacher loads."""
        if codes is None:
            return
        self.items.filter(kind="teacher").delete()
        for code in codes:
            ld = TeacherLoad.objects.filter(student=self.student, subject__iexact=code).select_related("teacher").first()
            ClearanceItem.objects.create(clearance=self, kind="teacher", label=code, signatory=ld.teacher if ld else None, rank=RANK["teacher"])

    def add_subject_code(self, code, extra=True):
        """Add one subject signature (irregular / transfer students). Returns (item, teacher_load) or (None, None) if already there."""
        code = code.strip()
        if not code or self.items.filter(kind="teacher", label__iexact=code).exists():
            return None, None
        ld = TeacherLoad.objects.filter(student=self.student, subject__iexact=code).select_related("teacher").first()
        item = ClearanceItem.objects.create(clearance=self, kind="teacher", label=code, signatory=ld.teacher if ld else None, rank=RANK["teacher"], extra=extra)
        return item, ld

    def sync(self):
        """Add any signature the student now needs (new teacher load, program set later, ...)."""
        st = self.student
        have = {(i.kind, i.office, i.label) for i in self.items.all()}
        if self.uses_curriculum(st):
            for i in self.items.filter(kind="teacher", signatory__isnull=True):
                ld = TeacherLoad.objects.filter(student=st, subject__iexact=i.label).select_related("teacher").first()
                if ld:
                    i.signatory = ld.teacher; i.save(update_fields=["signatory"])
            want = []
            if not self.items.filter(kind="teacher").exists():  # nothing ticked yet: use the teachers assigned to the student's class
                want = [("teacher", "", ld.subject, ld.teacher) for ld in st.loads.select_related("teacher")]
        else:
            want = [("teacher", "", ld.subject, ld.teacher) for ld in st.loads.select_related("teacher")]
        skip = settings.OFFICES_NOT_FOR
        want += [("office", o, OFFICE_LABEL[o], None) for o in EVERYONE if (st.program or "").upper() not in skip.get(o, ())]
        if st.program and settings.REQUIRE_DEPT_HEAD:
            want.append(("office", "dept_head", f"Department Head – {st.program}", None))
        want += [("registrar", "", "Registrar", None), ("cashier", "", "Cashier (account cleared, final)", None)]
        added = False
        for kind, off, label, t in want:
            if (kind, off, label) not in have:
                ClearanceItem.objects.create(clearance=self, kind=kind, office=off, label=label, signatory=t, rank=RANK[kind]); added = True; have.add((kind, off, label))
        if added:
            self.refresh_status()

    def __str__(self):
        return f"{self.student} / {self.term}"


class ClearanceItem(models.Model):
    clearance = models.ForeignKey(Clearance, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=10)  # teacher | office | cashier | registrar
    office = models.CharField(max_length=20, blank=True)
    rank = models.PositiveSmallIntegerField(default=1)
    label = models.CharField(max_length=120)
    signatory = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=10, default="pending")  # pending | approved | rejected
    remarks = models.CharField(max_length=255, blank=True)
    signed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="signed")
    signed_at = models.DateTimeField(null=True, blank=True)
    extra = models.BooleanField(default=False, help_text="Added by the student (irregular / transferee)")

    class Meta:
        ordering = ["rank", "id"]

    @property
    def subject_title(self):
        if self.kind != "teacher":
            return ""
        return CurriculumSubject.objects.filter(code__iexact=self.label).exclude(title="").values_list("title", flat=True).first() or ""

    @property
    def teacher_line(self):
        if self.kind != "teacher":
            return ""
        return f"Teacher: {self.signatory.display}" if self.signatory_id else "Teacher: not assigned yet"

    @property
    def grade_issues(self):
        if self.kind != "teacher" or not self.signatory_id:
            return []
        return list(GradeIssue.objects.filter(sheet__teacher_id=self.signatory_id, sheet__subject=self.label,
                                              student=self.clearance.student))

    @property
    def hint(self):
        c = self.clearance
        if self.status == "approved":
            return ""
        others = c.items.exclude(pk=self.pk).exclude(status="approved")
        if self.kind == "cashier":  # signs last: every other signature first, and the balance must be 0
            if c.balance > 0:
                return f"Unpaid balance ₱{c.balance}"
            if others.exists():
                return "Waiting for other signatures"
        if self.kind == "registrar" and others.exclude(kind="cashier").exists():
            return "Waiting for other signatures"
        return ""


class Payment(models.Model):
    clearance = models.ForeignKey(Clearance, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reference = models.CharField("OR / reference no.", max_length=60, blank=True)
    method = models.CharField(max_length=20, default="cash")
    recorded_by = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    paid_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-paid_at"]


GRADE_CATS = [("quiz", "Quiz"), ("activity", "Activity"), ("project", "Project"),
              ("recitation", "Recitation / Attendance"), ("exam", "Exam"), ("other", "Other")]
CAT_LABEL = dict(GRADE_CATS)
# regular class-standing + major-exam split; categories a class does not use are left out and the rest are scaled to 100%
DEFAULT_WEIGHTS = {"quiz": 20, "activity": 15, "project": 15, "recitation": 10, "exam": 40, "other": 0}
_CAT_WORDS = [("quiz", ("quiz",)), ("project", ("project", "proj ", "output", "capstone", "case study", "portfolio")),
              ("exam", ("exam", "midterm", "prelim", "finals", "periodical")), ("recitation", ("recit", "attend", "particip")),
              ("activity", ("activity", "act ", "lab", "assignment", "seatwork", "homework", "exercise", "drill"))]


def infer_cat(name):
    """Guess the grading category from an activity name ('Quiz 2' -> quiz). Falls back to 'other'."""
    n = (name or "").lower() + " "
    for k, words in _CAT_WORDS:
        if any(w in n for w in words):
            return k
    return "other"


class GradeSheet(models.Model):
    teacher = models.ForeignKey(User, on_delete=models.CASCADE, related_name="gradesheets")
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    subject = models.CharField(max_length=80)
    filename = models.CharField(max_length=200)
    activities = models.TextField(blank=True)
    students = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now=True)  # last change
    weights = models.JSONField(default=dict, blank=True)  # category -> percent; missing keys use DEFAULT_WEIGHTS
    notified_at = models.DateTimeField(null=True, blank=True)  # last time the teacher notified students of missing work

    class Meta:
        unique_together = ("teacher", "term", "subject")


class GradeIssue(models.Model):
    LABEL = {"missing": "missing", "invalid": "invalid", "over_max": "over max", "absent": "not in gradebook", "unknown": "unknown student"}
    sheet = models.ForeignKey(GradeSheet, on_delete=models.CASCADE, related_name="issues")
    student = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    student_no = models.CharField(max_length=30)
    activity = models.CharField(max_length=80, blank=True)
    kind = models.CharField(max_length=10)
    detail = models.CharField(max_length=200, blank=True)

    @property
    def short(self):
        return f"{self.activity} ({self.LABEL[self.kind]})" if self.activity else self.LABEL[self.kind].capitalize()


class GradeColumn(models.Model):
    sheet = models.ForeignKey(GradeSheet, on_delete=models.CASCADE, related_name="columns")
    name = models.CharField(max_length=80)
    max_score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    category = models.CharField(max_length=12, choices=GRADE_CATS, default="other")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        unique_together = ("sheet", "name")


class GradeScore(models.Model):
    column = models.ForeignKey(GradeColumn, on_delete=models.CASCADE, related_name="scores")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    value = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = ("column", "student")


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notes")
    text = models.CharField(max_length=200)
    url = models.CharField(max_length=100, default="/")
    created = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created"]


class AuditLog(models.Model):
    """Who did what on a clearance (signed, rejected, reopened, credited, released, ...)."""
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=30)
    clearance = models.ForeignKey(Clearance, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    student_name = models.CharField(max_length=160, blank=True)
    target = models.CharField(max_length=120, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]
