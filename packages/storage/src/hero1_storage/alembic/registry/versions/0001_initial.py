"""Baseline migration: registry schema (instruments, application_settings, global_events).

Implementation note: the baseline uses the model metadata directly so the
migrated schema can never drift from the models. All SUBSEQUENT migrations
must be explicit op.* operations.
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    from hero1_storage import registry_models  # noqa: F401
    from hero1_storage.base import Base
    Base.metadata.create_all(bind=op.get_bind(), tables=[
        registry_models.Instrument.__table__,
        registry_models.ApplicationSetting.__table__,
        registry_models.GlobalEvent.__table__,
    ])


def downgrade():
    from hero1_storage import registry_models
    registry_models.GlobalEvent.__table__.drop(bind=op.get_bind(), checkfirst=True)
    registry_models.ApplicationSetting.__table__.drop(bind=op.get_bind(), checkfirst=True)
    registry_models.Instrument.__table__.drop(bind=op.get_bind(), checkfirst=True)
