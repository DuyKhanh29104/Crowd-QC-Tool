"""Immutable annotation revisions, review attestations and snapshot manifests."""
from alembic import op
import sqlalchemy as sa

revision = "0003_review"
down_revision = "0002_proposals"
branch_labels = depends_on = None


def upgrade():
    op.create_table("annotations",sa.Column("id",sa.String(),primary_key=True),sa.Column("frame_id",sa.String(),sa.ForeignKey("frames.id"),nullable=False),sa.Column("roi_id",sa.String(),nullable=False),sa.Column("edit_version",sa.Integer(),nullable=False),sa.UniqueConstraint("frame_id","roi_id"))
    op.create_table("annotation_revisions",sa.Column("id",sa.String(),primary_key=True),sa.Column("annotation_id",sa.String(),sa.ForeignKey("annotations.id"),nullable=False),sa.Column("version",sa.Integer(),nullable=False),sa.Column("parent_id",sa.String(),sa.ForeignKey("annotation_revisions.id")),sa.Column("proposal_run_id",sa.String(),sa.ForeignKey("proposal_runs.id"),nullable=False),sa.Column("roi_version_id",sa.String(),sa.ForeignKey("roi_versions.id"),nullable=False),sa.Column("objects",sa.JSON(),nullable=False),sa.Column("rules",sa.JSON(),nullable=False),sa.Column("dependencies_hash",sa.String(),nullable=False),sa.Column("actor",sa.String(),nullable=False),sa.Column("created_at",sa.String(),nullable=False),sa.UniqueConstraint("annotation_id","version"))
    op.create_index("ix_annotation_revisions_annotation_id","annotation_revisions",["annotation_id"])
    op.create_table("review_records",sa.Column("id",sa.String(),primary_key=True),sa.Column("revision_id",sa.String(),sa.ForeignKey("annotation_revisions.id"),nullable=False),sa.Column("reviewer",sa.String(),nullable=False),sa.Column("verdict",sa.String(),nullable=False),sa.Column("scope",sa.JSON(),nullable=False),sa.Column("completeness",sa.JSON(),nullable=False),sa.Column("validity",sa.JSON(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_index("ix_review_records_revision_id","review_records",["revision_id"])
    op.create_table("gt_snapshots",sa.Column("id",sa.String(),primary_key=True),sa.Column("media_id",sa.String(),sa.ForeignKey("media.id"),nullable=False),sa.Column("manifest",sa.JSON(),nullable=False),sa.Column("manifest_hash",sa.String(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_index("ix_gt_snapshots_media_id","gt_snapshots",["media_id"])
    op.create_table("audit_events",sa.Column("id",sa.String(),primary_key=True),sa.Column("actor",sa.String(),nullable=False),sa.Column("action",sa.String(),nullable=False),sa.Column("entity_id",sa.String(),nullable=False),sa.Column("payload",sa.JSON(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_index("ix_audit_events_entity_id","audit_events",["entity_id"])


def downgrade():
    for table in ("audit_events","gt_snapshots","review_records","annotation_revisions","annotations"): op.drop_table(table)
