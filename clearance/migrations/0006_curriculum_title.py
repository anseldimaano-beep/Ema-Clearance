from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('clearance', '0005_curriculum'),
    ]

    operations = [
        migrations.AddField(
            model_name='curriculumsubject',
            name='title',
            field=models.CharField(blank=True, max_length=120, verbose_name='descriptive title'),
        ),
    ]
