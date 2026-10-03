"""Initial project/media/frame/import-job/ROI schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_media_roi"
down_revision = None
branch_labels = depends_on = None


def upgrade():
    op.create_table("projects", sa.Column("id",sa.String(),primary_key=True),sa.Column("name",sa.String(),nullable=False),sa.Column("data_root",sa.String(),nullable=False),sa.Column("schema_version",sa.Integer(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_table("media",sa.Column("id",sa.String(),primary_key=True),sa.Column("project_id",sa.String(),sa.ForeignKey("projects.id"),nullable=False),sa.Column("source_path",sa.Text(),nullable=False),sa.Column("name",sa.String(),nullable=False),sa.Column("camera_id",sa.String(),nullable=False),sa.Column("evidence_type",sa.String(),nullable=False),sa.Column("sha256",sa.String(),nullable=False),sa.Column("size_bytes",sa.Integer(),nullable=False),sa.Column("mtime_ns",sa.String(),nullable=False),sa.Column("width",sa.Integer(),nullable=False),sa.Column("height",sa.Integer(),nullable=False),sa.Column("stream_index",sa.Integer(),nullable=False),sa.Column("time_base_num",sa.Integer(),nullable=False),sa.Column("time_base_den",sa.Integer(),nullable=False),sa.Column("start_pts",sa.Integer(),nullable=False),sa.Column("duration_seconds",sa.Float(),nullable=False),sa.Column("frame_count",sa.Integer(),nullable=False),sa.Column("probe",sa.JSON(),nullable=False),sa.Column("parent_media_id",sa.String(),sa.ForeignKey("media.id")),sa.Column("source_offset_seconds",sa.String(),nullable=False),sa.Column("created_at",sa.String(),nullable=False))
    op.create_index("ix_media_project_id","media",["project_id"])
    op.create_table("frames",sa.Column("id",sa.String(),primary_key=True),sa.Column("media_id",sa.String(),sa.ForeignKey("media.id"),nullable=False),sa.Column("ordinal",sa.Integer(),nullable=False),sa.Column("pts",sa.Integer(),nullable=False),sa.Column("time_base_num",sa.Integer(),nullable=False),sa.Column("time_base_den",sa.Integer(),nullable=False),sa.Column("relative_seconds",sa.Float(),nullable=False),sa.UniqueConstraint("media_id","pts"),sa.UniqueConstraint("media_id","ordinal"))
    op.create_index("ix_frames_media_id","frames",["media_id"])
    op.create_index("ix_frames_relative_seconds","frames",["relative_seconds"])
    op.create_table("jobs",sa.Column("id",sa.String(),primary_key=True),sa.Column("project_id",sa.String(),sa.ForeignKey("projects.id"),nullable=False),sa.Column("kind",sa.String(),nullable=False),sa.Column("state",sa.String(),nullable=False),sa.Column("payload",sa.JSON(),nullable=False),sa.Column("progress",sa.Integer(),nullable=False),sa.Column("result",sa.JSON()),sa.Column("error",sa.JSON()),sa.Column("created_at",sa.String(),nullable=False))
    op.create_table("roi_versions",sa.Column("id",sa.String(),primary_key=True),sa.Column("roi_id",sa.String(),nullable=False),sa.Column("version",sa.Integer(),nullable=False),sa.Column("media_id",sa.String(),sa.ForeignKey("media.id"),nullable=False),sa.Column("name",sa.String(),nullable=False),sa.Column("polygon_source_px",sa.JSON(),nullable=False),sa.Column("area_m2",sa.Float(),nullable=False),sa.Column("area_evidence",sa.Text(),nullable=False),sa.Column("evidence_type",sa.String(),nullable=False),sa.Column("valid_from_seconds",sa.Float(),nullable=False),sa.Column("valid_to_seconds",sa.Float()),sa.Column("boundary_policy",sa.String(),nullable=False),sa.Column("created_at",sa.String(),nullable=False),sa.UniqueConstraint("roi_id","version"))
    op.create_index("ix_roi_versions_roi_id","roi_versions",["roi_id"])


def downgrade():
    for name in ("roi_versions","jobs","frames","media","projects"):
        op.drop_table(name)
