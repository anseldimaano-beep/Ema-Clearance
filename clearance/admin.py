from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import *

@admin.register(User)
class U(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("EMA", {"fields": ("role", "office", "scope", "program", "student_no", "grade_level", "position")}),)
    list_display = ("username", "first_name", "last_name", "role", "office", "program", "student_no")
    list_filter = ("role", "office", "program")

class ItemInline(admin.TabularInline):
    model = ClearanceItem; extra = 0

@admin.register(Clearance)
class C(admin.ModelAdmin):
    list_display = ("student", "term", "assessed", "status", "code"); inlines = [ItemInline]

admin.site.register([CurriculumSubject, Term, TeacherLoad, Payment, Notification])
