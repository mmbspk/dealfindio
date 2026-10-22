"""add immutability trigger to package_size_remediation_log

Revision ID: s4n5o6p7q8r9
Revises: r3m4n5o6p7q8
Create Date: 2026-10-22

Adds a BEFORE UPDATE OR DELETE trigger on package_size_remediation_log
so that the append-only guarantee is enforced at the database level rather
than relying solely on application code never calling UPDATE/DELETE.

PostgreSQL only — skipped on other dialects (e.g. SQLite used in tests).
"""

from alembic import op
from sqlalchemy import text

revision = "s4n5o6p7q8r9"
down_revision = "r3m4n5o6p7q8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute(
        text("""
            CREATE OR REPLACE FUNCTION _block_remediation_log_mutation()
            RETURNS trigger LANGUAGE plpgsql AS
            $$
            BEGIN
                RAISE EXCEPTION
                    'package_size_remediation_log is append-only; '
                    'UPDATE and DELETE are not permitted';
            END;
            $$
        """)
    )
    op.execute(
        text("""
            CREATE TRIGGER remediation_log_append_only
            BEFORE UPDATE OR DELETE ON package_size_remediation_log
            FOR EACH ROW EXECUTE FUNCTION _block_remediation_log_mutation()
        """)
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "postgresql":
        return

    op.execute(
        text("DROP TRIGGER IF EXISTS remediation_log_append_only ON package_size_remediation_log")
    )
    op.execute(text("DROP FUNCTION IF EXISTS _block_remediation_log_mutation"))
