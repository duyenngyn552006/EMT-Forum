from django.db import migrations


def rename_site(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    Site.objects.filter(pk=1).update(name="EMT Forum")


def rename_back(apps, schema_editor):
    Site = apps.get_model("sites", "Site")
    Site.objects.filter(pk=1).update(name="EC Forum")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_alter_user_role"),
    ]

    operations = [
        migrations.RunPython(rename_site, rename_back),
    ]
