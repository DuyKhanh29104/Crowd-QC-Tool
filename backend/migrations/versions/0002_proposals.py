"""Durable inference jobs and immutable proposal runs; no GT tables."""
from alembic import op
import sqlalchemy as sa

revision = "0002_proposals"
down_revision = "0001_media_roi"
branch_labels = depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("jobs", sa.Column("heartbeat", sa.String()))
    op.create_table("proposal_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("media_id", sa.String(), sa.ForeignKey("media.id"), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False))
    op.create_index("ix_proposal_runs_media_id", "proposal_runs", ["media_id"])
    op.create_table("proposal_frames",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("proposal_runs.id"), nullable=False),
        sa.Column("frame_id", sa.String(), sa.ForeignKey("frames.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "frame_id"))
    op.create_index("ix_proposal_frames_run_id", "proposal_frames", ["run_id"])


def downgrade():
    op.drop_table("proposal_frames")
    op.drop_table("proposal_runs")
    op.drop_column("jobs", "heartbeat")
    op.drop_column("jobs", "cancel_requested")
