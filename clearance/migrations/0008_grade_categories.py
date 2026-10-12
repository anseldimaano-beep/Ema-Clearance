from django.db import migrations, models

from clearance.models import GRADE_CATS, infer_cat


def backfill(apps, schema_editor):
    """Give every existing activity column a category based on its name (Quiz 1 -> Quiz, Project 2 -> Project...)."""
    Col = apps.get_model("clearance", "GradeColumn")
    for c in Col.objects.all():
        k = infer_cat(c.name)
        if k != c.category:
            c.category = k
            c.save(update_fields=["category"])


class Migration(migrations.Migration):

    dependencies = [
        ('clearance', '0007_bscs_y3s1_schedule'),
    ]

    operations = [
        migrations.AddField(model_name='gradecolumn', name='category',
                            field=models.CharField(choices=GRADE_CATS, default='other', max_length=12)),
        migrations.AddField(model_name='gradesheet', name='weights',
                            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='gradesheet', name='notified_at',
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
