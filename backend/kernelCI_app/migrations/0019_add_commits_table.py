import django.contrib.postgres.fields
import django.contrib.postgres.indexes
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("kernelCI_app", "0018_hardwareregistryplatformvendor_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Commits",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "field_timestamp",
                    models.DateTimeField(blank=True, db_column="_timestamp", null=True),
                ),
                ("tree_name", models.TextField(blank=True, null=True)),
                ("git_repository_url", models.TextField(blank=True, null=True)),
                ("git_commit_hash", models.TextField()),
                ("git_commit_name", models.TextField(blank=True, null=True)),
                ("git_repository_branch", models.TextField(blank=True, null=True)),
                ("git_commit_message", models.TextField(blank=True, null=True)),
                (
                    "git_repository_branch_tip",
                    models.BooleanField(blank=True, null=True),
                ),
                (
                    "git_commit_tags",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.TextField(), blank=True, null=True, size=None
                    ),
                ),
                ("patchset_files", models.JSONField(blank=True, null=True)),
                ("patchset_hash", models.TextField(blank=True, null=True)),
                ("message_id", models.TextField(blank=True, null=True)),
                ("comment", models.TextField(blank=True, null=True)),
                ("commit_time", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "commits",
                "indexes": [
                    models.Index(fields=["field_timestamp"], name="commits__timestamp"),
                    models.Index(fields=["git_commit_hash"], name="commits_hash"),
                    django.contrib.postgres.indexes.GinIndex(
                        fields=["git_commit_tags"], name="commits_tags"
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "tree_name",
                            "git_repository_url",
                            "git_repository_branch",
                            "git_commit_hash",
                        ),
                        name="commits_context_unique",
                        nulls_distinct=False,
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="checkouts",
            name="commit",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                to="kernelCI_app.commits",
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                    "checkouts_commit_origin_time "
                    "ON checkouts (commit_id, origin, start_time);",
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "checkouts_commit_origin_time;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="checkouts",
                    index=models.Index(
                        fields=["commit", "origin", "start_time"],
                        name="checkouts_commit_origin_time",
                    ),
                ),
            ],
        ),
    ]
