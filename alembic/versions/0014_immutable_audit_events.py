"""make audit_events immutable at the database level

Revision ID: 0014_immutable_audit_events
Revises: 0013_enterprise_readiness
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op

revision = "0014_immutable_audit_events"
down_revision = "0013_enterprise_readiness"
branch_labels = None
depends_on = None

_CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION prevent_audit_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'audit_events are append-only and cannot be updated or deleted (id: %)', OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

_DROP_FUNCTION = "DROP FUNCTION IF EXISTS prevent_audit_event_mutation();"

_CREATE_TRIGGER = """
CREATE TRIGGER audit_events_immutable
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
"""

_DROP_TRIGGER = "DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events;"


def upgrade() -> None:
    op.execute(_CREATE_FUNCTION)
    op.execute(_CREATE_TRIGGER)


def downgrade() -> None:
    op.execute(_DROP_TRIGGER)
    op.execute(_DROP_FUNCTION)
