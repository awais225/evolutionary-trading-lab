"""Baseline migration: per-instrument schema.

Registry tables (instruments / application_settings / global_events) are
deliberately EXCLUDED — they live in the registry DB only. Subsequent
migrations must be explicit op.* operations.
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from hero1_storage import instrument_models  # noqa: F401
    from hero1_storage.base import Base
    instrument_tables = [t for name, t in Base.metadata.tables.items()
                         if t.info.get("scope") != "registry" and name not in {
                             "instruments", "application_settings", "global_events",
                         }]
    Base.metadata.create_all(bind=op.get_bind(), tables=instrument_tables)


def downgrade():
    from hero1_storage import instrument_models
    for table in reversed(instrument_models.Node.metadata.sorted_tables):
        if table.name not in {"instruments", "application_settings", "global_events"}:
            table.drop(bind=op.get_bind(), checkfirst=True)
