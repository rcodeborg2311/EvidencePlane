"""make review_events immutable at the database level

Revision ID: 0005_immutable_review_events
Revises: 0004_add_review_events
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import op

revision = "0005_immutable_review_events"
down_revision = "0004_add_review_events"
branch_labels = None
depends_on = None

_CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION prevent_review_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'review_events are append-only and cannot be updated or deleted (id: %)', OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""

_DROP_FUNCTION = "DROP FUNCTION IF EXISTS prevent_review_event_mutation();"

_CREATE_TRIGGER = """
CREATE TRIGGER review_events_immutable
BEFORE UPDATE OR DELETE ON review_events
FOR EACH ROW EXECUTE FUNCTION prevent_review_event_mutation();
"""

_DROP_TRIGGER = "DROP TRIGGER IF EXISTS review_events_immutable ON review_events;"


def upgrade() -> None:
    op.execute(_CREATE_FUNCTION)
    op.execute(_CREATE_TRIGGER)


def downgrade() -> None:
    op.execute(_DROP_TRIGGER)
    op.execute(_DROP_FUNCTION)
