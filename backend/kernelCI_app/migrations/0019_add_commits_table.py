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
                    """
                    ALTER TABLE checkouts DROP CONSTRAINT IF EXISTS checkouts_pkey;
                    ALTER TABLE checkouts RENAME COLUMN id TO kci_id;
                    ALTER TABLE checkouts ADD COLUMN id BIGINT;
                    CREATE SEQUENCE IF NOT EXISTS checkouts_id_seq OWNED BY checkouts.id;
                    UPDATE checkouts SET id = nextval('checkouts_id_seq')
                    WHERE id IS NULL;
                    ALTER TABLE checkouts
                        ALTER COLUMN id SET DEFAULT nextval('checkouts_id_seq'),
                        ALTER COLUMN id SET NOT NULL;
                    ALTER TABLE checkouts ADD CONSTRAINT checkouts_pkey PRIMARY KEY (id);
                    """,
                    reverse_sql="""
                    ALTER TABLE checkouts DROP CONSTRAINT IF EXISTS checkouts_pkey;
                    ALTER TABLE checkouts DROP COLUMN IF EXISTS id;
                    DROP SEQUENCE IF EXISTS checkouts_id_seq;
                    ALTER TABLE checkouts RENAME COLUMN kci_id TO id;
                    ALTER TABLE checkouts ADD CONSTRAINT checkouts_pkey PRIMARY KEY (id);
                    """,
                ),
                migrations.RunSQL(
                    """
                    CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS checkouts_kci_id_key
                        ON checkouts (kci_id);
                    """,
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS checkouts_kci_id_key;"
                    ),
                ),
            ],
            state_operations=[
                migrations.RenameField(
                    model_name="checkouts",
                    old_name="id",
                    new_name="kci_id",
                ),
                migrations.AlterField(
                    model_name="checkouts",
                    name="kci_id",
                    field=models.TextField(unique=True),
                ),
                migrations.AddField(
                    model_name="checkouts",
                    name="id",
                    field=models.BigAutoField(primary_key=True, serialize=False),
                    preserve_default=False,
                ),
                migrations.AlterField(
                    model_name="builds",
                    name="checkout",
                    field=models.ForeignKey(
                        db_constraint=False,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        to="kernelCI_app.checkouts",
                        to_field="kci_id",
                    ),
                ),
            ],
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
