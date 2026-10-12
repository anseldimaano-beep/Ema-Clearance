from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clearance', '0008_grade_categories'),
    ]

    operations = [
        migrations.AddField(model_name='clearance', name='docs_wanted',
                            field=models.CharField(blank=True, default='', max_length=40)),
        migrations.AddField(model_name='clearance', name='docs_note',
                            field=models.CharField(blank=True, default='', max_length=100)),
    ]
