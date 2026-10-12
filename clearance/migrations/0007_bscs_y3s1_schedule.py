from django.db import migrations

OLD = ["GE 302", "ALAR 312", "IAS 313", "CC 314", "THS 304"]


def drop_old(apps, schema_editor):
    """BSCS 3rd year 1st sem now follows the school schedule (GE 10, GE 11, AL 102, AR 101, IAS 101, CC 106, THS 101)."""
    apps.get_model("clearance", "CurriculumSubject").objects.filter(program="BSCS", year=3, semester=1, code__in=OLD).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('clearance', '0006_curriculum_title'),
    ]

    operations = [
        migrations.RunPython(drop_old, migrations.RunPython.noop),
    ]
