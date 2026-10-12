from django.db import migrations

OLD = {"cashier": "Cashier (account cleared)", "registrar": "Registrar (final)"}
NEW = {"cashier": "Cashier (account cleared, final)", "registrar": "Registrar"}
RANK_NEW = {"registrar": 8, "cashier": 9}
RANK_OLD = {"cashier": 8, "registrar": 9}


def _apply(apps, labels_to, ranks_to):
    Item = apps.get_model("clearance", "ClearanceItem")
    for kind in ("cashier", "registrar"):
        Item.objects.filter(kind=kind).update(rank=ranks_to[kind], label=labels_to[kind])


def forward(apps, schema_editor):
    """Existing clearances: the Registrar now signs before the Cashier, who signs last."""
    _apply(apps, NEW, RANK_NEW)


def backward(apps, schema_editor):
    _apply(apps, OLD, RANK_OLD)


class Migration(migrations.Migration):

    dependencies = [
        ('clearance', '0009_clearance_docs_request'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
