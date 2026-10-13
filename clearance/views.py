import json
import re
import os
import unicodedata
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib.auth.views import PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count
from django.urls import reverse_lazy
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .students import assign_load, class_students, creds_path, find_teacher, import_students, newpw, parse_csv, parse_loads, save_creds, set_load
from .notify import audit, norm_phone, push, release
from .i18n import T
from .backup import maybe_backup, maybe_remind, temp_snapshot
from django.core.paginator import Paginator
from django.core.validators import validate_email
from django.utils.http import url_has_allowed_host_and_scheme
import csv
from .models import AuditLog, CAT_LABEL, CurriculumSubject, GRADE_CATS, Notification, OFFICE_LABEL, OFFICES, PROGRAMS, User, infer_cat
from .grades import (DEFAULT_COLS, dec, export_xlsx, import_into, missing_map, missing_xlsx, notice_text, parse, recompute,
                     standings, template_xlsx, weights_of)
from .models import DOC_FIELDS, RANK, Clearance, ClearanceItem, GradeColumn, GradeScore, GradeSheet, Payment, TeacherLoad, Term


def role_required(*roles):
    def deco(f):
        @wraps(f)
        def w(request, *a, **k):
            if request.user.role not in roles:
                return HttpResponseForbidden("Not allowed")
            return f(request, *a, **k)
        return login_required(w)
    return deco


MAGIC = {".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8", ".png": b"\x89PNG", ".pdf": b"%PDF", ".webp": b"RIFF"}
CTYPE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf"}


def _doc(f, optional=False):
    if not f:
        if optional:
            return None
        raise ValueError("Please attach every file that is listed.")
    ext = os.path.splitext(f.name)[1].lower()
    mb = settings.MAX_UPLOAD_MB
    if ext not in MAGIC or f.size > mb * 1024 * 1024:
        raise ValueError(f"Files must be a photo (JPG, PNG, WEBP) or PDF, up to {mb} MB each.")
    head = f.read(4); f.seek(0)
    if not head.startswith(MAGIC[ext]):
        raise ValueError(f"{f.name} is not a valid {ext[1:].upper()} file.")
    return f


@login_required
def doc(request, pk, which):
    c = get_object_or_404(Clearance, pk=pk)
    u = request.user
    if not ((u.role == "student" and c.student_id == u.id) or u.role in ("registrar", "admin")):
        return HttpResponseForbidden("Not allowed")
    f = {"id": c.id_file, "idback": c.idback_file, "copy": c.copy_file}.get(which)
    if not f:
        raise Http404
    r = FileResponse(f.open("rb"), content_type=CTYPE.get(os.path.splitext(f.name)[1].lower(), "application/octet-stream"))
    r["X-Content-Type-Options"] = "nosniff"; r["Cache-Control"] = "private, no-store"
    return r


def can_sign(u, i):
    if u.role == "teacher":
        return i.kind == "teacher" and i.signatory_id == u.id
    if u.role == "office":
        return i.kind == "office" and i.office == u.office and (not u.scopes or i.clearance.student.program in u.scopes)
    return u.role in ("cashier", "registrar") and i.kind == u.role


def _curr(student, term, c):
    """Subject checklist for the student's program (None when the program has no curriculum loaded yet)."""
    subs = list(CurriculumSubject.objects.filter(program=student.program)) if student.program else []
    if not subs:
        return None
    m = re.search(r"(\d)", student.grade_level or "")
    yr, sem = (int(m.group(1)) if m else 0), (int(term.semester) if term and term.semester else 0)
    checked = {i.label for i in c.items.filter(kind="teacher")} if c else set()
    groups = {}
    for s in subs:
        g = groups.setdefault((s.year, s.semester), {"y": s.year, "s": s.semester, "codes": []})
        g["codes"].append({"code": s.code, "on": (s.code in checked) if checked else (s.year == yr and s.semester == sem)})
    out = [groups[k] for k in sorted(groups)]
    for g in out:
        g["open"] = any(x["on"] for x in g["codes"])
    return out


def _clean_codes(raw):
    """Subject codes typed by a student (comma/newline separated). Letters, digits, space and . - / & ( ) only."""
    out = []
    for part in re.split(r"[,\n;]+", raw or ""):
        p = re.sub(r"\s+", " ", part).strip()[:40]
        if p and re.fullmatch(r"[\w .\-/&()]+", p) and p.lower() not in {x.lower() for x in out}:
            out.append(p)
    return out[:10]


def _canon(student, codes):
    """Use the curriculum's spelling when a typed code matches one."""
    cur = {s.code.lower(): s.code for s in CurriculumSubject.objects.filter(program=student.program)} if student.program else {}
    return [cur.get(x.lower(), x) for x in codes]


def _money(v):
    try:
        d = Decimal(v); return d if d >= 0 else None
    except (InvalidOperation, TypeError):
        return None


def _next(request, default="inbox"):
    n = request.POST.get("next") or ""
    return n if n and url_has_allowed_host_and_scheme(n, allowed_hosts={request.get_host()}) else default


def _lang(request):
    return "tl" if request.COOKIES.get("lang") == "tl" else "en"


def _qs_without(request, *drop):
    d = request.GET.copy()
    for k in drop:
        d.pop(k, None)
    return d.urlencode()


def _next_step(c, lang):
    """One plain sentence telling the student exactly what to do now."""
    if not c:
        return ""
    if c.status == "requested":
        return T(lang, "ns_requested")
    if c.status == "returned":
        return T(lang, "ns_returned", note=c.note or "")
    if c.status == "cleared":
        return T(lang, "ns_cleared")
    if c.docs_wanted:
        return T(lang, "ns_docs")
    rej = c.items.filter(status="rejected").select_related("signatory").first()
    if rej:
        who = (("si " if lang == "tl" else "") + rej.signatory.display) if rej.signatory_id else ("ang Registrar" if lang == "tl" else "the Registrar")
        return T(lang, "ns_rejected", label=rej.label, why=(f": {rej.remarks}" if rej.remarks else ""), who=who)
    others = list(c.items.exclude(kind="cashier").exclude(status="approved").select_related("signatory"))
    if others:
        nt = next((i for i in others if i.kind == "teacher" and not i.signatory_id), None)
        if nt:
            return T(lang, "ns_noteacher", label=nt.label)
        names = [f"{i.label} ({i.signatory.display})" if i.kind == "teacher" and i.signatory_id else i.label for i in others]
        return T(lang, "ns_waiting", names=", ".join(names[:4]) + ("…" if len(names) > 4 else ""))
    return T(lang, "ns_pay", amt=c.balance) if c.balance > 0 else T(lang, "ns_cashier")


def _stages(c):
    if not c:
        return []
    st = c.status
    rel = st in ("pending", "cleared")
    sig = rel and not c.items.exclude(kind="cashier").exclude(status="approved").exists()
    flags = [st != "returned", rel, sig, rel and c.balance <= 0, st == "cleared"]
    out, found = [], False
    for n, f in zip(("Request", "Released", "Signatures", "Payment", "Print"), flags):
        if f:
            out.append((n, "done"))
        elif not found:
            out.append((n, "now")); found = True
        else:
            out.append((n, "todo"))
    return out


@login_required
def home(request):
    maybe_backup(); maybe_remind()
    r = request.user.role
    return redirect({"student": "student", "cashier": "cashier", "registrar": "requests", "owner": "overview", "admin": "overview"}.get(r, "inbox"))


@role_required("student")
def student(request):
    term = Term.objects.filter(is_active=True).first()
    c = Clearance.objects.filter(student=request.user, term=term).first() if term else None
    if c:
        c.sync()
    curr = _curr(request.user, term, c) if term else None
    ok = [s.code for s in CurriculumSubject.objects.filter(program=request.user.program)] if request.user.program else []
    if request.method == "POST" and term:
        if c and c.status != "returned":
            return redirect("student")
        codes = None
        if curr is not None:
            ticked = set(request.POST.getlist("subjects"))
            codes = [x for x in ok if x in ticked]
            for x in _canon(request.user, _clean_codes(request.POST.get("extra_subjects"))):
                if x.lower() not in {y.lower() for y in codes}:
                    codes.append(x)
            if not codes:
                messages.error(request, "Tick at least one subject you are taking now.")
                return redirect("student")
        try:
            new = {f: _doc(request.FILES.get(f), optional=True) for _, _, f in DOC_FIELDS}  # all three are optional
        except ValueError as e:
            messages.error(request, str(e))
            return redirect("student")
        if not c:
            c = Clearance.build(request.user, term, codes)
        else:
            c.set_subjects(codes); c.sync()
        if curr is not None:
            c.items.filter(kind="teacher").exclude(label__in=ok).update(extra=True)
        else:  # program without a curriculum: extra subjects are added on top of the class teachers
            for x in _canon(request.user, _clean_codes(request.POST.get("extra_subjects"))):
                c.add_subject_code(x)
        for f, up in new.items():  # a new upload replaces the old file; no upload keeps what was already sent
            if up:
                getattr(c, f).delete(save=False)
                setattr(c, f, up)
        c.status, c.note = "requested", ""
        c.save()
        audit(request.user, "requested", c)
        push([(r, f"{request.user.display} requested a clearance", "/requests/") for r in User.objects.filter(role="registrar", is_active=True)])
        messages.success(request, "Request sent to the Registrar. You'll see it move once it is released to the signing offices.")
        return redirect("student")
    extras = ""
    if c and curr is not None:
        known = {x.lower() for x in ok}
        extras = ", ".join(l for l in c.items.filter(kind="teacher").values_list("label", flat=True) if l.lower() not in known)
    prev = []
    if term and curr is not None:
        pc = Clearance.objects.filter(student=request.user).exclude(term=term).order_by("-created").first()
        if pc:
            prev = [l for l in pc.items.filter(kind="teacher").values_list("label", flat=True) if l in ok]
    lang = _lang(request)
    return render(request, "student.html", {"term": term, "c": c, "curr": curr, "extras": extras, "prev_codes": prev,
                                            "next_step": _next_step(c, lang), "stages": _stages(c)})


@role_required("student")
def student_status(request):
    """Tiny JSON the student page polls, so it can offer a refresh instead of reloading by itself."""
    term = Term.objects.filter(is_active=True).first()
    c = Clearance.objects.filter(student=request.user, term=term).first() if term else None
    if not c:
        return JsonResponse({"v": "none"})
    n = request.user.notes.filter(read=False).count()
    sig = ",".join(c.items.order_by("id").values_list("status", flat=True))
    return JsonResponse({"v": f"{c.status}|{sig}|{c.docs_wanted}|{c.balance}|{n}"})


@role_required("student")
def student_add_subject(request):
    """An irregular / transfer student adds a subject after the clearance was released."""
    term = Term.objects.filter(is_active=True).first()
    c = Clearance.objects.filter(student=request.user, term=term).first() if term else None
    if request.method != "POST" or not c:
        return redirect("student")
    if c.status != "pending":
        messages.error(request, "You can add subjects here once your clearance is released and before it is cleared. If it is already cleared, please see the Registrar.")
        return redirect("student")
    codes = _canon(request.user, _clean_codes(request.POST.get("code", "") + "," + request.POST.get("extra", "")))
    if not codes:
        messages.error(request, "Choose a subject or type its code.")
        return redirect("student")
    pairs, added = [], []
    for code in codes:
        item, ld = c.add_subject_code(code)
        if not item:
            continue
        added.append(item.label)
        audit(request.user, "subject_added", c, item.label, "by student")
        for r in User.objects.filter(role="registrar", is_active=True):
            pairs.append((r, f"{request.user.display} added {item.label} to their clearance" + ("" if ld else ". No teacher assigned yet.")[:200], "/inbox/?all=1"))
        if ld:
            pairs.append((ld.teacher, f"{request.user.display} was added to your signing list for {item.label}", "/inbox/"))
    if not added:
        messages.error(request, "That subject is already on your clearance.")
        return redirect("student")
    c.refresh_status(); push(pairs)
    messages.success(request, f"Added {', '.join(added)} to your clearance. The Registrar was notified.")
    return redirect("student")


@role_required("student")
def student_docs(request):
    """The student sends the documents the Registrar asked for, without redoing the whole request."""
    term = Term.objects.filter(is_active=True).first()
    c = Clearance.objects.filter(student=request.user, term=term).first() if term else None
    if request.method != "POST" or not c or not c.docs_wanted or c.status == "cleared":
        return redirect("student")
    wanted = [(k, f) for k, _, f in DOC_FIELDS if k in c.docs_wanted.split(",")]
    try:
        new = {f: _doc(request.FILES.get(f)) for _, f in wanted}
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("student")
    for f, up in new.items():
        getattr(c, f).delete(save=False)
        setattr(c, f, up)
    c.docs_wanted, c.docs_note = "", ""
    c.save()
    push([(r, f"{request.user.display} sent the documents you asked for", "/requests/" if c.status == "requested" else "/inbox/")
          for r in User.objects.filter(role="registrar", is_active=True)])
    messages.success(request, "Documents sent to the Registrar.")
    return redirect("student")


@role_required("registrar")
def add_subject(request, pk):
    """The Registrar adds a subject signature the student forgot to tick, after the clearance was released."""
    c = get_object_or_404(Clearance, pk=pk)
    back = "inbox" if request.POST.get("back") == "inbox" else "requests"
    if request.method != "POST":
        return redirect(back)
    if c.status != "pending":
        messages.error(request, "Subjects can only be added while the clearance is released and not yet cleared.")
        return redirect(back)
    code = request.POST.get("code", "").strip()
    subj = next((s for s in c.addable_subjects if s.code == code), None)
    if not subj:
        messages.error(request, "That subject is not in the student's program, or it is already on the clearance.")
        return redirect(back)
    ld = TeacherLoad.objects.filter(student=c.student, subject__iexact=subj.code).select_related("teacher").first()
    ClearanceItem.objects.create(clearance=c, kind="teacher", label=subj.code, signatory=ld.teacher if ld else None, rank=RANK["teacher"])
    c.refresh_status()
    audit(request.user, "subject_added", c, subj.code, "by registrar")
    pairs = [(c.student, f"The Registrar added {subj.code} to your clearance" + (f". Please see {ld.teacher.display}." if ld else "."), "/student/")]
    if ld:
        pairs.append((ld.teacher, f"{c.student.display} was added to your signing list for {subj.code}", "/inbox/"))
    push(pairs)
    messages.success(request, f"Added {subj.code} to {c.student.display}'s clearance" + ("" if ld else " (no teacher is assigned to it yet)") + ".")
    return redirect(back)


@role_required("registrar")
def ask_docs(request, pk):
    """The Registrar asks the student to upload the Student ID and/or Registration Card."""
    c = get_object_or_404(Clearance, pk=pk)
    back = "inbox" if request.POST.get("back") == "inbox" else "requests"
    if request.method != "POST" or c.status in ("cleared", "returned"):
        return redirect(back)
    keys = [k for k, _, _ in DOC_FIELDS if k in request.POST.getlist("docs")]
    if not keys:
        messages.error(request, "Tick at least one document to ask for.")
        return redirect(back)
    note = request.POST.get("note", "").strip()[:100]
    c.docs_wanted, c.docs_note = ",".join(keys), note
    c.save(update_fields=["docs_wanted", "docs_note"])
    names = ", ".join(lbl for k, lbl, _ in DOC_FIELDS if k in keys)
    push([(c.student, (f"The Registrar needs your {names}. Please upload it on your clearance page." + (f" Note: {note}" if note else ""))[:200], "/student/")])
    messages.success(request, f"Asked {c.student.display} for: {names}.")
    return redirect(back)


def _inbox_base(u):
    if u.role == "teacher":
        q = Q(kind="teacher", signatory=u)
    elif u.role == "office":
        q = Q(kind="office", office=u.office)
        if u.scopes:
            q &= Q(clearance__student__program__in=u.scopes)
    else:
        q = Q(kind=u.role)
    return ClearanceItem.objects.filter(q).exclude(clearance__status__in=["requested", "returned"]).select_related("clearance__student", "signatory")


@role_required("teacher", "office", "cashier", "registrar")
def inbox(request):
    u = request.user
    base = _inbox_base(u)
    st = request.GET.get("status") or ("all" if request.GET.get("all") else "open")
    items = base
    if st == "open":
        items = items.filter(status__in=["pending", "rejected"])
    elif st in ("pending", "rejected", "approved"):
        items = items.filter(status=st)
    qs = request.GET.get("q", "").strip()
    if qs:
        items = items.filter(Q(clearance__student__last_name__icontains=qs) | Q(clearance__student__first_name__icontains=qs)
                             | Q(clearance__student__student_no__icontains=qs) | Q(clearance__student__username__icontains=qs))
    prog, subj = request.GET.get("program", ""), request.GET.get("subject", "")
    if prog:
        items = items.filter(clearance__student__program=prog)
    if subj:
        items = items.filter(label=subj)
    items = items.order_by("label", "clearance__student__grade_level", "clearance__student__last_name", "clearance__student__first_name")
    page = Paginator(items, 100).get_page(request.GET.get("page"))
    for i in page:
        i.gi, i.hnt = i.grade_issues, i.hint
    subjects = sorted(set(base.filter(kind="teacher").values_list("label", flat=True))) if u.role == "teacher" else []
    programs = sorted(p for p in set(base.values_list("clearance__student__program", flat=True)) if p)
    return render(request, "inbox.html", {"page": page, "st": st, "q": qs, "prog": prog, "subj": subj, "subjects": subjects, "programs": programs,
                                          "qstr": _qs_without(request, "page"), "open_n": base.filter(status__in=["pending", "rejected"]).count(),
                                          "rej_n": base.filter(status="rejected").count()})


def _apply(u, item, action, remarks):
    """Sign or reject one item. Returns (ok, message). Used by the single and the bulk actions."""
    c = item.clearance
    if c.status in ("requested", "returned"):
        return False, "This clearance has not been released by the Registrar yet."
    if not can_sign(u, item):
        return False, "Not your item."
    if action not in ("approve", "reject"):
        return False, "Unknown action."
    remarks = (remarks or "").strip()[:255]
    if action == "reject" and not remarks:
        return False, "Please write the reason for rejecting, so the student knows what to fix."
    if action == "approve":
        others = c.items.exclude(pk=item.pk).exclude(status="approved")
        if item.kind == "cashier" and c.balance > 0:
            return False, f"Cannot approve: unpaid balance of ₱{c.balance}."
        if (item.kind == "cashier" and others.exists()) or (item.kind == "registrar" and others.exclude(kind="cashier").exists()):
            return False, "Cannot approve: other signatures are still missing."
        if item.grade_issues and not remarks:
            remarks = "Signed despite gradebook issues"
    item.status = "approved" if action == "approve" else "rejected"
    item.remarks, item.signed_by, item.signed_at = remarks, u, timezone.now()
    item.save(); c.refresh_status()
    audit(u, "signed" if item.status == "approved" else "rejected", c, item.label, remarks)
    stu = c.student
    if item.status == "rejected":
        push([(stu, f"{item.label} was rejected: {remarks}", "/student/")], sms=True)
    elif c.status == "cleared":
        push([(stu, "You're cleared! Open your page to print your clearance.", "/student/")], sms=True)
    elif item.kind != "cashier" and c.items.filter(kind="cashier").exclude(status="approved").exists() \
            and not c.items.exclude(kind="cashier").exclude(status="approved").exists():
        # the last signature before the Cashier is in: tell the Cashier it is their turn, and the student what to do
        due = f" Balance ₱{c.balance} is still unpaid." if c.balance > 0 else ""
        push([(k, f"{stu.display} has every other signature. Your final signature is next.{due}", "/inbox/")
              for k in User.objects.filter(role="cashier", is_active=True)])
        push([(stu, "All signatures are in. " + (f"Please pay your balance of ₱{c.balance} at the Cashier." if c.balance > 0 else "Waiting for the Cashier's final signature."), "/student/")], sms=True)
    return True, f"{item.label}: {item.status}."


@role_required("teacher", "office", "cashier", "registrar")
def act(request, pk):
    item = get_object_or_404(ClearanceItem.objects.select_related("clearance__student"), pk=pk)
    if not can_sign(request.user, item):
        return HttpResponseForbidden("Not your item")
    if request.method == "POST":
        ok, msg = _apply(request.user, item, request.POST.get("action"), request.POST.get("remarks", ""))
        (messages.success if ok else messages.error)(request, msg if ok else msg)
    return redirect(_next(request))


@role_required("teacher", "office", "cashier", "registrar")
def bulk_act(request):
    """Sign every selected item that is ready (no unmet prerequisite, no gradebook issue). Others are skipped and counted."""
    if request.method != "POST":
        return redirect("inbox")
    ids = [x for x in request.POST.getlist("ids") if x.isdigit()][:500]
    done = skipped = 0
    for i in ClearanceItem.objects.filter(pk__in=ids).select_related("clearance__student").order_by("rank", "id"):
        if i.status == "approved":
            continue
        if can_sign(request.user, i) and not i.hint and not i.grade_issues and _apply(request.user, i, "approve", "")[0]:
            done += 1
        else:
            skipped += 1
    if not ids:
        messages.error(request, "Tick at least one student first.")
    else:
        messages.success(request, f"Signed {done}." + (f" Skipped {skipped} (not ready, or has gradebook issues: sign those one by one)." if skipped else ""))
    return redirect(_next(request))


@login_required
def reopen_item(request, pk):
    """Undo a rejection (or, for the Registrar/Admin, any signature) so the item is pending again."""
    item = get_object_or_404(ClearanceItem.objects.select_related("clearance__student"), pk=pk)
    u, c = request.user, item.clearance
    allowed = u.role in ("registrar", "admin") or (can_sign(u, item) and item.status == "rejected")
    if not allowed:
        return HttpResponseForbidden("Not allowed")
    if request.method == "POST" and item.status != "pending":
        was = item.status
        item.status, item.remarks, item.signed_by, item.signed_at = "pending", "", None, None
        item.save(); c.refresh_status()
        audit(u, "reopened", c, item.label, f"was {was}")
        messages.success(request, f"{item.label} for {c.student.display} is pending again.")
    return redirect(_next(request, "inbox"))


@role_required("registrar")
def credit_item(request, pk):
    """Transferee: the subject was already taken/credited, so it needs no teacher signature."""
    item = get_object_or_404(ClearanceItem.objects.select_related("clearance__student"), pk=pk, kind="teacher")
    c = item.clearance
    if request.method == "POST" and item.status != "approved" and c.status in ("pending", "cleared"):
        note = request.POST.get("note", "").strip()[:150]
        item.status, item.remarks, item.signed_by, item.signed_at = "approved", ("Credited by the Registrar" + (f": {note}" if note else "")), request.user, timezone.now()
        item.save(); c.refresh_status()
        audit(request.user, "credited", c, item.label, note)
        messages.success(request, f"{item.label} credited for {c.student.display}.")
    elif request.method == "POST":
        messages.error(request, "Credit a subject after the clearance is released.")
    return redirect(_next(request, "clearances"))


@login_required
def remove_item(request, pk):
    """Remove a subject signature: the Registrar any unsigned one, a student only a subject they added themselves."""
    item = get_object_or_404(ClearanceItem.objects.select_related("clearance__student"), pk=pk, kind="teacher")
    u, c = request.user, item.clearance
    ok = (u.role == "registrar") or (u.role == "student" and c.student_id == u.id and item.extra)
    if not ok:
        return HttpResponseForbidden("Not allowed")
    if request.method == "POST":
        if item.status == "approved":
            messages.error(request, "A signed subject cannot be removed. Reopen it first.")
        else:
            lab = item.label
            item.delete(); c.refresh_status()
            audit(u, "subject_removed", c, lab)
            messages.success(request, f"Removed {lab}.")
    return redirect(_next(request, "student" if u.role == "student" else "clearances"))


def _clr_qs(request):
    term = Term.objects.filter(is_active=True).first()
    qs = Clearance.objects.filter(term=term).select_related("student") if term else Clearance.objects.none()
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(student__last_name__icontains=q) | Q(student__first_name__icontains=q) | Q(student__student_no__icontains=q))
    if request.GET.get("status") in ("requested", "returned", "pending", "cleared"):
        qs = qs.filter(status=request.GET["status"])
    if request.GET.get("program"):
        qs = qs.filter(student__program=request.GET["program"])
    return term, qs.order_by("student__last_name", "student__first_name")


@role_required("registrar", "admin")
def clearances_view(request):
    term, qs = _clr_qs(request)
    page = Paginator(qs.prefetch_related("items__signed_by", "items__signatory"), 40).get_page(request.GET.get("page"))
    return render(request, "clearances.html", {"page": page, "term": term, "q": request.GET.get("q", ""), "st": request.GET.get("status", ""),
                                               "prog": request.GET.get("program", ""), "programs": [p for p, _ in PROGRAMS], "qstr": _qs_without(request, "page")})


def _cell(x):
    x = "" if x is None else str(x)
    return "'" + x if x[:1] in ("=", "+", "-", "@", "\t", "\r") else x


@role_required("registrar", "admin", "owner")
def clearances_export(request):
    term, qs = _clr_qs(request)
    r = HttpResponse(content_type="text/csv")
    r["Content-Disposition"] = 'attachment; filename="clearances.csv"'
    w = csv.writer(r)
    w.writerow(["Student no", "Last name", "First name", "Program", "Class", "Status", "Assessed", "Paid", "Balance", "Waiting for", "Code"])
    for c in qs.prefetch_related("items"):
        s = c.student
        w.writerow([_cell(x) for x in (s.student_no, s.last_name, s.first_name, s.program, s.grade_level, c.status, c.assessed, c.paid, c.balance,
                                       "; ".join(i.label for i in c.items.all() if i.status != "approved"), c.code)])
    return r


@role_required("registrar", "admin", "owner")
def audit_view(request):
    qs = AuditLog.objects.select_related("user")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(student_name__icontains=q) | Q(target__icontains=q) | Q(detail__icontains=q) | Q(user__last_name__icontains=q)
                       | Q(user__first_name__icontains=q) | Q(user__username__icontains=q))
    act_ = request.GET.get("action", "")
    if act_:
        qs = qs.filter(action=act_)
    page = Paginator(qs, 100).get_page(request.GET.get("page"))
    return render(request, "audit.html", {"page": page, "q": q, "action": act_, "actions": sorted(set(AuditLog.objects.values_list("action", flat=True))),
                                          "qstr": _qs_without(request, "page")})


@login_required
def profile(request):
    u = request.user
    if request.method == "POST":
        email, phone = request.POST.get("email", "").strip()[:254], request.POST.get("phone", "").strip()
        err = None
        if email:
            try:
                validate_email(email)
            except ValidationError:
                err = "That email address does not look right."
        if phone and not norm_phone(phone):
            err = "Enter a Philippine mobile number like 09171234567."
        if err:
            messages.error(request, err)
        else:
            u.email, u.phone = email, norm_phone(phone)
            u.save(update_fields=["email", "phone"])
            messages.success(request, "Contact details saved.")
        return redirect("profile")
    return render(request, "profile.html", {"sms_on": bool(settings.SMS_API_KEY)})


def set_lang(request, code):
    r = redirect(request.META.get("HTTP_REFERER") if url_has_allowed_host_and_scheme(request.META.get("HTTP_REFERER") or "", allowed_hosts={request.get_host()}) else "home")
    r.set_cookie("lang", "tl" if code == "tl" else "en", max_age=365 * 86400, samesite="Lax")
    return r


@role_required("admin")
def backup_download(request):
    if not settings.USING_SQLITE:
        messages.error(request, "The database is on Neon. Use Neon's backups (console.neon.tech > Branches / Restore).")
        return redirect("home")
    path = temp_snapshot()
    f = open(path, "rb")
    try:
        os.unlink(path)
    except OSError:
        pass
    audit(request.user, "backup_downloaded")
    return FileResponse(f, as_attachment=True, filename=f"ema_clearance_{timezone.localdate():%Y%m%d}.sqlite3")


@role_required("cashier", "registrar", "admin")
def cashier(request):
    term = Term.objects.filter(is_active=True).first()
    if request.method == "POST":
        if request.user.role != "cashier":
            return HttpResponseForbidden("Only the Cashier records payments")
        c = get_object_or_404(Clearance, pk=request.POST.get("cid"))
        amt = _money(request.POST.get("amount"))
        if amt is None:
            messages.error(request, "Invalid amount.")
        elif request.POST.get("do") == "assess":
            c.assessed = amt; c.save(update_fields=["assessed"]); audit(request.user, "assessed", c, detail=f"₱{amt}"); messages.success(request, "Assessment updated.")
        elif amt > 0:
            Payment.objects.create(clearance=c, amount=amt, reference=request.POST.get("reference", "")[:60],
                                   method=request.POST.get("method", "cash")[:20], recorded_by=request.user)
            audit(request.user, "payment", c, detail=f"₱{amt}")
            messages.success(request, f"Payment of ₱{amt} recorded.")
        return redirect("cashier")
    qs = Clearance.objects.filter(term=term).select_related("student") if term else Clearance.objects.none()
    s = request.GET.get("q")
    if s:
        qs = qs.filter(Q(student__first_name__icontains=s) | Q(student__last_name__icontains=s) | Q(student__student_no__icontains=s))
    rows = list(qs)
    st = {"total": len(rows), "settled": sum(1 for c in rows if c.assessed and c.balance == 0),
          "owing": sum(1 for c in rows if c.balance > 0), "collected": sum(c.paid for c in rows)}
    return render(request, "cashier.html", {"rows": rows, "term": term, "q": s or "", "st": st})


@login_required
def print_view(request, pk):
    c = get_object_or_404(Clearance, pk=pk)
    if request.user.role == "student" and c.student_id != request.user.id:
        return HttpResponseForbidden("Not allowed")
    return render(request, "print.html", {"c": c, "subjects": c.items.filter(kind="teacher"), "offices": c.items.exclude(kind="teacher")})


def verify(request, code):
    return render(request, "verify.html", {"c": Clearance.objects.filter(code=code.upper()).first()})


@role_required("teacher")
def grades(request):
    u = request.user
    term = Term.objects.filter(is_active=True).first()
    subjects = sorted(set(TeacherLoad.objects.filter(teacher=u).values_list("subject", flat=True)))
    if request.method == "POST":
        f, subj = request.FILES.get("file"), request.POST.get("subject")
        if not term or subj not in subjects or not f or not f.name.lower().endswith(".xlsx") or f.size > 5_000_000:
            messages.error(request, "Choose a subject and an .xlsx file (max 5 MB).")
            return redirect("grades")
        try:
            parsed = parse(f)
        except Exception as e:
            messages.error(request, f"Could not read the file: {e}.")
            return redirect("grades")
        sh = _open_sheet(u, term, subj, defaults=False)
        ok, skipped = import_into(sh, parsed)
        messages.success(request, f"Imported {ok} students into {subj}." + (f" {skipped} student number(s) not in your class list were skipped." if skipped else "")
                         + (f" {parsed['texts']} non-numeric cell(s) were treated as missing." if parsed["texts"] else ""))
        return redirect("grade_sheet", pk=sh.pk)
    cards = []
    for s in subjects:
        sh = GradeSheet.objects.filter(teacher=u, term=term, subject=s).first() if term else None
        n = len(TeacherLoad.objects.filter(teacher=u, subject=s))
        cards.append({"subject": s, "sheet": sh, "students": n,
                      "flagged": sh.issues.values("student_id").distinct().count() if sh else 0,
                      "missing": sh.issues.filter(kind="missing").values("student_id").distinct().count() if sh else 0})
    return render(request, "grades.html", {"subjects": subjects, "cards": cards, "term": term})


def _open_sheet(u, term, subj, defaults=True):
    sh, new = GradeSheet.objects.get_or_create(teacher=u, term=term, subject=subj)
    if new and defaults:
        for n, (name, mx, cat) in enumerate(DEFAULT_COLS):
            GradeColumn.objects.create(sheet=sh, name=name, max_score=mx, category=cat, order=n)
    recompute(sh)
    return sh


def _sheet(request, pk):
    return get_object_or_404(GradeSheet, pk=pk, teacher=request.user)


@role_required("teacher")
def grade_open(request):
    term = Term.objects.filter(is_active=True).first()
    subj = request.POST.get("subject", "")
    if request.method != "POST" or not term or not TeacherLoad.objects.filter(teacher=request.user, subject=subj).exists():
        return redirect("grades")
    return redirect("grade_sheet", pk=_open_sheet(request.user, term, subj).pk)


@role_required("teacher")
def grade_sheet(request, pk):
    sh = _sheet(request, pk)
    studs = [l.student for l in TeacherLoad.objects.filter(teacher=sh.teacher, subject=sh.subject)
             .select_related("student").order_by("student__last_name", "student__first_name")]
    cols = list(sh.columns.all())
    sc = {(s.student_id, s.column_id): s.value for s in GradeScore.objects.filter(column__sheet=sh)}
    bad = {(i.student_id, i.activity): i.kind for i in sh.issues.all()}
    st = standings(sh, cols, sc, [s.id for s in studs])
    rows = []
    for s in studs:
        cells = [{"c": c, "v": sc.get((s.id, c.id)), "bad": bad.get((s.id, c.name), "")} for c in cols]
        rows.append({"s": s, "cells": cells, "n": sum(1 for x in cells if x["bad"]),
                     "miss": sum(1 for x in cells if x["bad"] == "missing"), "stand": st.get(s.id)})
    w = weights_of(sh)
    wrows = [{"key": k, "label": l, "w": w[k], "n": sum(1 for c in cols if c.category == k)} for k, l in GRADE_CATS]
    return render(request, "grade_sheet.html", {
        "sh": sh, "cols": cols, "rows": rows, "flagged": sum(1 for r in rows if r["n"]),
        "missing_students": sum(1 for r in rows if r["miss"]), "cats": GRADE_CATS, "wrows": wrows,
        "wtotal": sum(r["w"] for r in wrows if r["n"])})


@role_required("teacher")
def grade_cell(request, pk):
    if request.method != "POST":
        return HttpResponseForbidden("POST only")
    sh = _sheet(request, pk)
    try:
        d = json.loads(request.body)
        col = sh.columns.get(pk=d["col"])
        if d.get("type") == "cat":
            if d.get("value") not in CAT_LABEL:
                raise ValueError
            col.category = d["value"]; col.save(update_fields=["category"])
        else:
            val = dec(d.get("value"))
            if val is not None and not (0 <= val <= 9999):
                raise ValueError
            if d.get("type") == "max":
                if val is not None and val <= 0:
                    raise ValueError
                col.max_score = val; col.save(update_fields=["max_score"]); recompute(sh)
            else:
                st = TeacherLoad.objects.get(teacher=sh.teacher, subject=sh.subject, student_id=d["student"]).student
                GradeScore.objects.update_or_create(column=col, student=st, defaults={"value": val}); recompute(sh, st)
        sh.save()
    except Exception:
        return JsonResponse({"ok": False}, status=400)
    return JsonResponse({"ok": True, "issues": [[a, b, c] for a, b, c in sh.issues.values_list("student_id", "activity", "kind")],
                         "standing": {str(k): v for k, v in standings(sh).items()}})


@role_required("teacher")
def grade_col(request, pk):
    sh = _sheet(request, pk)
    if request.method == "POST":
        a = request.POST.get("action")
        if a == "add":
            name = request.POST.get("name", "").strip()[:80]
            try:
                mx = dec(request.POST.get("max"))
            except ValueError:
                mx = None
            cat = request.POST.get("cat")
            if cat not in CAT_LABEL:
                cat = infer_cat(name)
            if not name or sh.columns.filter(name__iexact=name).exists():
                messages.error(request, "Enter a new, unique activity name.")
            else:
                GradeColumn.objects.create(sheet=sh, name=name, max_score=mx, category=cat, order=sh.columns.count())
                messages.success(request, f"Added {name} ({CAT_LABEL[cat]}).")
        elif a == "delete":
            sh.columns.filter(pk=request.POST.get("col")).delete()
            messages.success(request, "Activity removed.")
        recompute(sh)
    return redirect("grade_sheet", pk=pk)


@role_required("teacher")
def grade_weights(request, pk):
    sh = _sheet(request, pk)
    if request.method == "POST":
        w = {}
        try:
            for k, _ in GRADE_CATS:
                v = dec(request.POST.get("w_" + k))
                w[k] = float(v) if v is not None else 0.0
                if not 0 <= w[k] <= 100:
                    raise ValueError
        except ValueError:
            messages.error(request, "Each weight must be a number from 0 to 100.")
        else:
            sh.weights = w; sh.save(update_fields=["weights"])
            messages.success(request, "Grading system saved.")
    return redirect("grade_sheet", pk=pk)


@role_required("teacher")
def grade_notify(request, pk):
    """Tell students (bell + email if they have one) which requirements they still have to submit."""
    sh = _sheet(request, pk)
    if request.method != "POST":
        return redirect("grade_sheet", pk=pk)
    recompute(sh)
    note = request.POST.get("note", "").strip()[:60]
    m = missing_map(sh)
    only = request.POST.get("student")
    if only:
        m = {s: v for s, v in m.items() if str(s.pk) == only}
    if not m:
        messages.info(request, "Nobody has missing work. Nothing to send.")
        return redirect("grade_sheet", pk=pk)
    now, pairs, dup = timezone.now(), [], 0
    for s, items in m.items():
        text = notice_text(sh.subject, sh.teacher, items, note)
        if Notification.objects.filter(user=s, text=text, created__gte=now - timedelta(hours=12)).exists():
            dup += 1  # same reminder already sent in the last 12 hours
            continue
        pairs.append((s, text, "/my-grades/"))
    if pairs:
        push(pairs)
        sh.notified_at = now; sh.save(update_fields=["notified_at"])
    messages.success(request, f"Notified {len(pairs)} student{'s' if len(pairs) != 1 else ''}." if pairs else "No new reminders to send.")
    if dup:
        messages.info(request, f"{dup} student{'s' if dup != 1 else ''} already got this exact reminder in the last 12 hours.")
    return redirect("grade_sheet", pk=pk)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@role_required("teacher")
def grade_export(request, pk):
    sh = _sheet(request, pk)
    r = HttpResponse(export_xlsx(sh), content_type=XLSX)
    r["Content-Disposition"] = f'attachment; filename="{sh.subject}_gradebook.xlsx"'
    return r


@role_required("teacher")
def grade_missing(request, pk):
    sh = _sheet(request, pk)
    recompute(sh)
    r = HttpResponse(missing_xlsx(sh), content_type=XLSX)
    r["Content-Disposition"] = f'attachment; filename="{sh.subject}_missing_work.xlsx"'
    return r


@role_required("teacher")
def grade_template(request):
    subj = request.GET.get("subject", "")
    studs = [l.student for l in TeacherLoad.objects.filter(teacher=request.user, subject=subj).select_related("student")
             .order_by("student__last_name", "student__first_name")]
    r = HttpResponse(template_xlsx(subj or "Grades", studs), content_type=XLSX)
    r["Content-Disposition"] = 'attachment; filename="gradebook_template.xlsx"'
    return r


@role_required("student")
def my_grades(request):
    """Student view: which requirements are still missing in each subject."""
    u = request.user
    term = Term.objects.filter(is_active=True).first()
    cards = []
    for l in (TeacherLoad.objects.filter(student=u).select_related("teacher").order_by("subject") if term else []):
        sh = GradeSheet.objects.filter(teacher=l.teacher, term=term, subject=l.subject).first()
        if not sh:
            continue
        order = {c.name: i for i, c in enumerate(sh.columns.all())}
        miss = sorted((i.activity for i in sh.issues.filter(student=u, kind="missing")), key=lambda a: order.get(a, 999))
        cards.append({"subject": l.subject, "teacher": l.teacher, "missing": miss, "updated": sh.uploaded_at})
    return render(request, "my_grades.html", {"cards": cards, "term": term})


def _tok(s):
    return re.sub(r"[^a-z ]", "", s.lower()).split()


def _match(u):
    fn = _tok(u.first_name)[:1]
    if not fn:
        return None
    for r in User.objects.filter(role="student", is_active=True, signup=False, last_name__iexact=u.last_name).exclude(pk=u.pk):
        if _tok(r.first_name)[:1] == fn:
            return r


def _plain(x):
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower())


def _surname_taken(last):
    k = _plain(last)
    return any(_plain(n) == k for n in User.objects.filter(role="teacher").values_list("last_name", flat=True))


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    d = {k: request.POST.get(k, "").strip() for k in ("last_name", "first_name", "student_no", "program", "grade_level", "email", "username", "position")}
    d["role"] = "teacher" if request.POST.get("role") == "teacher" else "student"
    if request.method == "POST":
        p1, p2, err, teacher = request.POST.get("password1", ""), request.POST.get("password2", ""), None, d["role"] == "teacher"
        if not d["last_name"] or not d["first_name"]:
            err = "Please fill in your last and first name."
        elif teacher and not re.fullmatch(r"[A-Za-z0-9._@-]{3,40}", d["username"]):
            err = "Choose a username of 3-40 letters, digits, dots or dashes (no spaces)."
        elif teacher and User.objects.filter(username__iexact=d["username"]).exists():
            err = "That username is taken. Choose another one."
        elif teacher and _surname_taken(d["last_name"]):
            err = "A teacher account with this surname already exists. Ask the Registrar for its password."
        elif not teacher and not (d["student_no"] and d["grade_level"]):
            err = "Please fill in your student number and class."
        elif not teacher and not re.fullmatch(r"[A-Za-z0-9-]{4,20}", d["student_no"]):
            err = "Student number should be 4-20 letters, digits or dashes."
        elif not teacher and d["program"] not in dict(PROGRAMS):
            err = "Choose your program."
        elif p1 != p2:
            err = "Passwords do not match."
        elif not teacher and User.objects.filter(Q(username__iexact=d["student_no"]) | Q(student_no__iexact=d["student_no"])).exists():
            err = "This student number already has an account. If you forgot your password, ask the Registrar to reset it."
        else:
            try:
                validate_password(p1)
            except ValidationError as e:
                err = " ".join(e.messages)
        if err:
            messages.error(request, err)
        else:
            if teacher:
                u = User(username=d["username"], first_name=d["first_name"][:150], last_name=d["last_name"][:150], role="teacher",
                         position=d["position"][:80], email=d["email"][:254])
            else:
                u = User(username=d["student_no"], first_name=d["first_name"][:150], last_name=d["last_name"][:150], role="student", student_no=d["student_no"],
                         grade_level=d["grade_level"][:40], program=d["program"], email=d["email"][:254], is_active=False, signup=True)
            u.set_password(p1); u.save()
            if teacher:
                push([(r, f"New teacher account: {u.display}" + (f" (teaches {u.position})" if u.position else "") + ". Assign their subjects.", "/students/")
                      for r in User.objects.filter(role="registrar", is_active=True)])
                messages.success(request, "Your teacher account is ready. You can sign in now.")
                return redirect("login")
            push([(r, f"New account request: {u.display} ({u.student_no})", "/students/") for r in User.objects.filter(role="registrar", is_active=True)])
            messages.success(request, "Account request sent. The Registrar must approve it before you can sign in.")
            return redirect("login")
    return render(request, "signup.html", {"d": d, "programs": PROGRAMS})


STAFF_ROLES = ("student", "teacher", "office", "cashier")


@role_required("registrar", "admin")
def accounts(request):
    me = request.user
    allowed = [k for k, _ in User.ROLES] if me.role == "admin" else list(STAFF_ROLES)
    cred = None
    if request.method == "POST":
        a, g = request.POST.get("action"), request.POST.get
        if a == "create":
            role, first, last = g("role"), g("first_name", "").strip(), g("last_name", "").strip()
            no, un = g("student_no", "").strip(), g("username", "").strip()
            un = un or (no if role == "student" else "")
            if role not in allowed or not first or not last or not un or not re.fullmatch(r"[A-Za-z0-9._@-]{3,40}", un):
                messages.error(request, "Fill in role, first name, last name and a valid username.")
            elif User.objects.filter(username__iexact=un).exists():
                messages.error(request, f"Username {un} is already taken.")
            elif role == "office" and g("office") not in dict(OFFICES):
                messages.error(request, "Choose the office for a Signing Office account.")
            else:
                pw = newpw()
                u = User(username=un, first_name=first, last_name=last, role=role, email=g("email", "").strip(), student_no=no, grade_level=g("grade_level", "").strip()[:40],
                         program=g("program") if g("program") in dict(PROGRAMS) else "", office=g("office") if role == "office" else "",
                         scope=g("scope", "").strip().upper() if role == "office" else "", is_staff=role == "admin", is_superuser=role == "admin")
                u.set_password(pw); u.save()
                cred = {"name": u.display, "username": un, "password": pw}
        elif a in ("reset", "toggle"):
            t = User.objects.filter(pk=g("id")).first()
            if not t or t.role not in allowed or t.pk == me.pk:
                messages.error(request, "You can't change that account.")
            elif a == "reset":
                pw = newpw(); t.set_password(pw); t.save(update_fields=["password"])
                cred = {"name": t.display, "username": t.username, "password": pw}
            else:
                t.is_active = not t.is_active; t.save(update_fields=["is_active"])
                messages.success(request, f"{t.display} is now {'active' if t.is_active else 'deactivated'}.")
        if not cred:
            return redirect("accounts")
    q, rf = request.GET.get("q", "").strip(), request.GET.get("role", "")
    qs = User.objects.filter(role__in=allowed, signup=False)
    if rf in allowed:
        qs = qs.filter(role=rf)
    elif not q:
        qs = qs.exclude(role="student")
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(username__icontains=q))
    return render(request, "accounts.html", {"rows": qs.order_by("role", "last_name")[:80], "roles": [(k, v) for k, v in User.ROLES if k in allowed],
                                             "offices": OFFICES, "programs": PROGRAMS, "q": q, "rf": rf, "cred": cred})


@role_required("registrar", "admin")
def terms(request):
    if request.method == "POST":
        a, pk = request.POST.get("action"), request.POST.get("id")
        if a == "create":
            name = request.POST.get("name", "").strip()[:60]
            sem = request.POST.get("semester") if request.POST.get("semester") in ("1", "2") else ""
            if not name or Term.objects.filter(name__iexact=name).exists():
                messages.error(request, "Enter a new, unique name, e.g. SY 2026-2027 Final Exam.")
            else:
                Term.objects.update(is_active=False)
                Term.objects.create(name=name, semester=sem, is_active=True)
                messages.success(request, f"{name} is now open. Students can request their clearance.")
        elif a == "open":
            t = get_object_or_404(Term, pk=pk)
            Term.objects.update(is_active=False); t.is_active = True; t.save()
            messages.success(request, f"{t.name} is now open.")
        elif a == "close":
            Term.objects.filter(pk=pk).update(is_active=False)
            messages.success(request, "Clearance period closed. Students can no longer request.")
        return redirect("terms")
    return render(request, "terms.html", {"terms": Term.objects.order_by("-id").annotate(n=Count("clearance"))})


class PwChange(SuccessMessageMixin, PasswordChangeView):
    success_url = reverse_lazy("home")
    success_message = "Password updated."


@role_required("registrar", "admin")
def students(request):
    if request.method == "POST":
        a = request.POST.get("action")
        if a == "import":
            f = request.FILES.get("file")
            if not f or not f.name.lower().endswith(".csv") or f.size > 2_000_000:
                messages.error(request, "Choose a .csv file (max 2 MB).")
            else:
                try:
                    rows = parse_csv(f.read())
                    creds, skipped = import_students(rows)
                except Exception as e:
                    messages.error(request, f"Import failed: {e}")
                else:
                    if creds:
                        save_creds(creds)
                    messages.success(request, f"Imported {len(creds)} students" + (f", skipped {skipped} already in the system." if skipped else "."))
        elif a in ("acct_link", "acct_new", "acct_reject"):
            u = User.objects.filter(pk=request.POST.get("id"), signup=True, is_active=False).first()
            if not u:
                messages.error(request, "That request no longer exists.")
            elif a == "acct_reject":
                u.delete(); messages.success(request, "Account request rejected.")
            elif a == "acct_new":
                u.is_active, u.signup = True, False; u.save(update_fields=["is_active", "signup"])
                messages.success(request, f"Approved {u.display}. They can log in now.")
            else:
                r = _match(u)
                if not r:
                    messages.error(request, "No roster match found.")
                else:
                    d = dict(username=u.username, student_no=u.student_no, password=u.password, email=u.email, program=u.program or r.program)
                    u.delete()
                    for k, v in d.items():
                        setattr(r, k, v)
                    r.save()
                    messages.success(request, f"Linked to roster record {r.display}. They can log in with student number {r.student_no}.")
        elif a == "program":
            grp, prog = request.POST.get("group", ""), request.POST.get("program", "")
            if grp and prog in dict(PROGRAMS):
                n = User.objects.filter(role="student", grade_level=grp).update(program=prog)
                for c in Clearance.objects.filter(student__grade_level=grp):
                    c.sync()
                messages.success(request, f"{grp}: program set to {prog} for {n} student(s).")
            else:
                messages.error(request, "Choose a class and a program.")
        elif a == "assign":
            t = User.objects.filter(pk=request.POST.get("teacher"), role="teacher").first()
            subj, grp = " ".join(request.POST.get("subject", "").split())[:80], request.POST.get("group", "")
            subj = CurriculumSubject.objects.filter(code__iexact=subj).values_list("code", flat=True).first() or subj
            if t and subj and grp:
                for c in Clearance.objects.filter(student__grade_level=grp):
                    c.sync()
                messages.success(request, f"{subj} · {grp}: assigned {t.display} to {assign_load(t, subj, grp)} new student(s).")
            else:
                messages.error(request, "Choose a teacher, class and subject.")
        return redirect("students")
    qs = User.objects.filter(role="student")
    groups = list(qs.values("grade_level").annotate(n=Count("id")).order_by("grade_level"))
    g, q = request.GET.get("g", ""), request.GET.get("q", "").strip()
    if g:
        qs = qs.filter(grade_level=g)
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(student_no__icontains=q))
    pend = list(User.objects.filter(role="student", signup=True, is_active=False).order_by("date_joined"))
    for p in pend:
        p.match = _match(p)
    return render(request, "students.html", {"pend": pend, "rows": qs.order_by("grade_level", "last_name", "first_name")[:400], "total": qs.count(), "groups": groups,
        "g": g, "q": q, "programs": PROGRAMS, "codes": CurriculumSubject.objects.order_by("id").values_list("code", flat=True).distinct(), "teachers": User.objects.filter(role="teacher").order_by("last_name"), "creds": creds_path().exists()})


@role_required("registrar", "admin")
def creds_download(request):
    p = creds_path()
    if request.method != "POST" or not p.exists():
        return redirect("students")
    data = p.read_bytes(); p.unlink()
    r = HttpResponse(data, content_type="text/csv")
    r["Content-Disposition"] = 'attachment; filename="student_logins.csv"'
    return r


@role_required("owner", "admin", "registrar", "office")
def overview(request):
    u = request.user
    if u.role == "office" and u.office != "dean":
        return HttpResponseForbidden("Not allowed")
    term = Term.objects.filter(is_active=True).first()
    cl = list(Clearance.objects.filter(term=term).select_related("student")) if term else []
    prog = {}
    for c in cl:
        d = prog.setdefault(c.student.program or "No program set", {"p": c.student.program or "No program set", "req": 0, "cleared": 0})
        d["req"] += 1; d["cleared"] += c.status == "cleared"
    lab = {}
    for r in ClearanceItem.objects.filter(clearance__term=term, clearance__status="pending", status="pending").values("kind", "office").annotate(n=Count("id")):
        k = "Subject teachers" if r["kind"] == "teacher" else OFFICE_LABEL.get(r["office"]) if r["kind"] == "office" else r["kind"].capitalize()
        lab[k] = lab.get(k, 0) + r["n"]
    return render(request, "overview.html", {"term": term, "students": User.objects.filter(role="student").count(), "req": len(cl),
        "cleared": sum(c.status == "cleared" for c in cl), "awaiting": sum(c.status == "requested" for c in cl), "prog": sorted(prog.values(), key=lambda d: d["p"]),
        "bottle": sorted(({"l": k, "n": n} for k, n in lab.items()), key=lambda d: -d["n"]),
        "assessed": sum(c.assessed for c in cl), "collected": sum(c.paid for c in cl), "owing": sum(c.balance for c in cl)})


@role_required("registrar")
def requests_queue(request):
    if request.method == "POST":
        a = request.POST.get("action")
        if a == "release":
            qs = Clearance.objects.filter(status="requested").select_related("student")
            if request.POST.get("ids"):
                qs = qs.filter(pk__in=[x for x in request.POST["ids"].split(",") if x.isdigit()])
            elif not request.POST.get("all"):
                qs = qs.filter(pk=request.POST.get("id"))
            n = release(list(qs), request.user)
            messages.success(request, f"Released {n} clearance{'s' if n != 1 else ''}. Teachers, offices and the cashier were notified." if n else "Nothing to release.")
        elif a == "return":
            c = get_object_or_404(Clearance, pk=request.POST.get("id"), status="requested")
            c.status, c.note = "returned", request.POST.get("note", "").strip()[:200] or "Please see the Registrar."
            c.save(update_fields=["status", "note"])
            audit(request.user, "returned", c, detail=c.note)
            push([(c.student, f"The Registrar returned your clearance request: {c.note}", "/student/")], sms=True)
            messages.success(request, f"Returned to {c.student.display}.")
        return redirect("requests")
    rq = Clearance.objects.filter(status="requested").select_related("student").order_by("created")
    q, prog = request.GET.get("q", "").strip(), request.GET.get("program", "")
    if q:
        rq = rq.filter(Q(student__last_name__icontains=q) | Q(student__first_name__icontains=q) | Q(student__student_no__icontains=q))
    if prog:
        rq = rq.filter(student__program=prog)
    rows = list(rq)
    for c in rows:
        c.docs = [(k, lbl, bool(f) and f.name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))) for k, lbl, f in
                  (("id", "Student ID (front)", c.id_file), ("idback", "Student ID (back, validation)", c.idback_file), ("copy", "Registration card", c.copy_file)) if f]
        c.subs = [i.label for i in c.items.filter(kind="teacher")]
        c.warn = (["No program set"] if not c.student.program else [])
        if c.missing_docs and not c.docs_wanted:
            c.warn.append("Not attached (optional): " + ", ".join(c.missing_docs))
        if Clearance.uses_curriculum(c.student):
            miss = [i.label for i in c.items.filter(kind="teacher", signatory__isnull=True)]
            c.warn += ([f"No teacher assigned yet for: {', '.join(miss)}"] if miss else []) + ([] if c.subs else ["No subjects ticked"])
        elif not c.student.loads.exists():
            c.warn.append("No subject teachers assigned")
    return render(request, "requests.html", {"rows": rows, "q": q, "prog": prog, "programs": [p for p, _ in PROGRAMS], "filtered": bool(q or prog),
                                             "ids": ",".join(str(c.pk) for c in rows)})


@login_required
def notifications(request):
    notes = list(request.user.notes.all()[:60])
    request.user.notes.filter(read=False).update(read=True)
    return render(request, "notifications.html", {"notes": notes})


@role_required("registrar", "admin")
def loads(request):
    """Who teaches which subject to which class. Change it here every semester (one row, or upload a whole schedule)."""
    from .models import Clearance, ClearanceItem, TeacherLoad
    if request.method == "POST":
        a = request.POST.get("action")
        if a == "set":
            cls, subj = request.POST.get("group", ""), " ".join(request.POST.get("subject", "").split())[:80]
            tid = request.POST.get("teacher", "")
            t = User.objects.filter(pk=tid, role="teacher").first() if tid else None
            subj = CurriculumSubject.objects.filter(code__iexact=subj).values_list("code", flat=True).first() or subj
            if not (cls and subj) or (tid and not t):
                messages.error(request, "Choose a class, a subject and a teacher.")
            else:
                n = set_load(cls, subj, t)
                if n is None:
                    messages.error(request, f"No students found in {cls}.")
                else:
                    for c in Clearance.objects.filter(term__is_active=True, student__in=[s.pk for s in class_students(cls)]):
                        c.sync()
                    audit(request.user, "teacher_load", None, f"{cls} / {subj}", f"{t.display if t else 'removed'} ({n} students)")
                    messages.success(request, f"{cls} · {subj}: {t.display if t else 'teacher removed'} ({n} student(s) updated).")
        elif a == "upload":
            f = request.FILES.get("file")
            if not f or not f.name.lower().endswith((".csv", ".xlsx")) or f.size > 2_000_000:
                messages.error(request, "Choose a .csv or .xlsx file (max 2 MB).")
            else:
                try:
                    rows = parse_loads(f.read(), f.name)
                except Exception as e:
                    messages.error(request, f"Could not read the file: {e}")
                else:
                    if request.POST.get("replace"):
                        TeacherLoad.objects.all().delete()
                        ClearanceItem.objects.filter(kind="teacher").exclude(status="approved").update(signatory=None)
                    done, skipped = 0, []
                    for cls, code, who in rows:
                        code = " ".join(code.split())[:80]
                        if not (cls and code):
                            continue
                        code = CurriculumSubject.objects.filter(code__iexact=code).values_list("code", flat=True).first() or code
                        if not who:
                            skipped.append(f"{cls} / {code}: no teacher on the schedule"); continue
                        t = find_teacher(who)
                        if not t:
                            skipped.append(f"{cls} / {code}: no teacher account for {who}"); continue
                        if set_load(cls, code, t) is None:
                            skipped.append(f"{cls} / {code}: no students in {cls}"); continue
                        done += 1
                    for c in Clearance.objects.filter(term__is_active=True):
                        c.sync()
                    audit(request.user, "teacher_load", None, "upload", f"{done} rows applied, {len(skipped)} skipped")
                    messages.success(request, f"Applied {done} subject assignment(s)." + (f" Skipped {len(skipped)}: " + "; ".join(skipped[:8]) + (" …" if len(skipped) > 8 else "") if skipped else ""))
        return redirect("loads")
    rows = list(TeacherLoad.objects.values("student__grade_level", "subject", "teacher_id", "teacher__first_name", "teacher__last_name")
                .annotate(n=Count("id")).order_by("student__grade_level", "subject"))
    for r in rows:
        r["group"] = r["student__grade_level"]
    return render(request, "loads.html", {"rows": rows, "teachers": User.objects.filter(role="teacher").order_by("last_name"),
        "groups": list(User.objects.filter(role="student").values_list("grade_level", flat=True).distinct().order_by("grade_level")),
        "codes": CurriculumSubject.objects.order_by("id").values_list("code", flat=True).distinct()})


@role_required("registrar", "admin")
def loads_export(request):
    from .models import TeacherLoad
    r = HttpResponse(content_type="text/csv")
    r["Content-Disposition"] = 'attachment; filename="teacher_loads.csv"'
    w = csv.writer(r); w.writerow(["class", "code", "teacher"])
    for x in TeacherLoad.objects.values("student__grade_level", "subject", "teacher__last_name").annotate(n=Count("id")).order_by("student__grade_level", "subject"):
        w.writerow([x["student__grade_level"], x["subject"], x["teacher__last_name"]])
    return r


@role_required("teacher")
def my_subjects(request):
    """A teacher picks the subjects they teach (course no. + descriptive title) for each class. BSCS only for now."""
    from .models import Clearance, TeacherLoad
    u = request.user
    term = Term.objects.filter(is_active=True).first()
    sem = int(term.semester) if term and term.semester else 1
    pool = list(CurriculumSubject.objects.filter(program="BSCS", semester=sem))
    by_code = {s.code: s for s in CurriculumSubject.objects.filter(program="BSCS")}
    groups = [g for g in User.objects.filter(role="student").values_list("grade_level", flat=True).distinct().order_by("grade_level") if g]

    def taught_by(cls, code):
        ids = [s.pk for s in class_students(cls)]
        return set(TeacherLoad.objects.filter(student_id__in=ids, subject__iexact=code).values_list("teacher_id", flat=True)), ids

    if request.method == "POST":
        a, cls, code = request.POST.get("action"), request.POST.get("group", "").strip(), request.POST.get("subject", "").strip()
        subj = by_code.get(code)
        if cls not in groups or not subj:
            messages.error(request, "Choose a class and a subject from the list.")
        else:
            who, ids = taught_by(cls, code)
            if not ids:
                messages.error(request, f"No students found in {cls}.")
            elif a == "add":
                m = re.search(r"(\d)", cls)
                if m and int(m.group(1)) != subj.year:
                    messages.error(request, f"{subj.code} is a Year {subj.year} subject, so it cannot be added to {cls}.")
                elif who - {u.pk}:
                    other = User.objects.filter(pk__in=who - {u.pk}).first()
                    messages.error(request, f"{cls} · {subj.code} already has a teacher ({other.display}). Ask the Registrar to change it.")
                elif who == {u.pk} and TeacherLoad.objects.filter(student_id__in=ids, subject__iexact=code).count() == len(ids):
                    messages.info(request, f"{cls} · {subj.code} is already on your list.")
                else:
                    n = set_load(cls, subj.code, u)
                    for c in Clearance.objects.filter(term__is_active=True, student__in=ids):
                        c.sync()
                    audit(u, "teacher_load", None, f"{cls} / {subj.code}", f"chosen by the teacher ({n} students)")
                    messages.success(request, f"Added {subj.code} · {subj.title or ''} for {cls} ({n} student(s)).")
            elif a == "remove":
                if who and who != {u.pk}:
                    messages.error(request, "You can only remove your own subjects.")
                elif not who:
                    messages.info(request, "That subject is not on your list.")
                else:
                    n = set_load(cls, subj.code, None)
                    for c in Clearance.objects.filter(term__is_active=True, student__in=ids):
                        c.sync()
                    audit(u, "teacher_load", None, f"{cls} / {subj.code}", f"removed by the teacher ({n} students)")
                    messages.success(request, f"Removed {subj.code} from {cls}. Signatures already given are kept.")
        return redirect("my_subjects")
    mine = list(TeacherLoad.objects.filter(teacher=u).values("student__grade_level", "subject").annotate(n=Count("id")).order_by("student__grade_level", "subject"))
    for r in mine:
        r["group"], r["title"] = r["student__grade_level"], (by_code[r["subject"]].title if r["subject"] in by_code else "")
    years = {}
    for s in pool:
        years.setdefault(s.year, []).append(s)
    return render(request, "my_subjects.html", {"mine": mine, "groups": groups, "years": sorted(years.items()), "sem": sem})
