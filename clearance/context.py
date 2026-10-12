from .models import Clearance


def nav(request):
    from django.conf import settings
    u = request.user
    lang = "tl" if request.COOKIES.get("lang") == "tl" else "en"
    if not u.is_authenticated:
        return {"lang": lang}
    d = {"lang": lang, "show_backup": settings.USING_SQLITE, "MAX_UPLOAD_MB": settings.MAX_UPLOAD_MB, "unread": u.notes.filter(read=False).count()}
    if u.role == "registrar":
        d["req_count"] = Clearance.objects.filter(status="requested").count()
    return d
