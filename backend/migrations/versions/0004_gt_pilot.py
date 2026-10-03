"""Immutable numeric observations and pilot evidence."""
from alembic import op
import sqlalchemy as sa
revision="0004_gt_pilot"
down_revision="0003_review"
branch_labels=depends_on=None


def upgrade():
    op.create_table("gt_observations",sa.Column("id",sa.String(),primary_key=True),sa.Column("revision_id",sa.String(),sa.ForeignKey("annotation_revisions.id"),nullable=False),sa.Column("review_id",sa.String(),sa.ForeignKey("review_records.id"),nullable=False),sa.Column("payload",sa.JSON(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_table("review_activity",sa.Column("id",sa.String(),primary_key=True),sa.Column("annotation_id",sa.String(),sa.ForeignKey("annotations.id"),nullable=False),sa.Column("session_id",sa.String(),nullable=False),sa.Column("reviewer",sa.String(),nullable=False),sa.Column("seconds",sa.Float(),nullable=False),sa.Column("interactions",sa.Integer(),nullable=False),sa.Column("source",sa.String(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_index("ix_review_activity_annotation_id","review_activity",["annotation_id"])
    op.create_table("independent_checks",sa.Column("id",sa.String(),primary_key=True),sa.Column("revision_id",sa.String(),sa.ForeignKey("annotation_revisions.id"),nullable=False),sa.Column("reviewer",sa.String(),nullable=False),sa.Column("payload",sa.JSON(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_index("ix_independent_checks_revision_id","independent_checks",["revision_id"])
    op.create_table("adjudications",sa.Column("id",sa.String(),primary_key=True),sa.Column("check_id",sa.String(),sa.ForeignKey("independent_checks.id"),nullable=False),sa.Column("revision_id",sa.String(),sa.ForeignKey("annotation_revisions.id"),nullable=False),sa.Column("reviewer",sa.String(),nullable=False),sa.Column("resolution",sa.String(),nullable=False),sa.Column("notes",sa.Text(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_index("ix_adjudications_check_id","adjudications",["check_id"])
    op.create_table("pilot_reports",sa.Column("id",sa.String(),primary_key=True),sa.Column("run_id",sa.String(),sa.ForeignKey("proposal_runs.id"),nullable=False),sa.Column("payload",sa.JSON(),nullable=False),sa.Column("report_hash",sa.String(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))


def downgrade():
    for table in ("pilot_reports","adjudications","independent_checks","review_activity","gt_observations"):op.drop_table(table)
