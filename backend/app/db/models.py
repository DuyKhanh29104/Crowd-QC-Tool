from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, ForeignKey, UniqueConstraint, JSON, Text, Boolean
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc).isoformat()


class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    data_root = Column(String, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(String, default=now, nullable=False)


class Media(Base):
    __tablename__ = "media"
    id = Column(String, primary_key=True)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    source_path = Column(Text, nullable=False)
    name = Column(String, nullable=False)
    camera_id = Column(String, nullable=False)
    evidence_type = Column(String, nullable=False)
    sha256 = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    mtime_ns = Column(String, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    stream_index = Column(Integer, nullable=False)
    time_base_num = Column(Integer, nullable=False)
    time_base_den = Column(Integer, nullable=False)
    start_pts = Column(Integer, nullable=False)
    duration_seconds = Column(Float, nullable=False)
    frame_count = Column(Integer, nullable=False)
    probe = Column(JSON, nullable=False)
    parent_media_id = Column(ForeignKey("media.id"), nullable=True)
    source_offset_seconds = Column(String, nullable=False, default="0")
    created_at = Column(String, default=now, nullable=False)


class Frame(Base):
    __tablename__ = "frames"
    id = Column(String, primary_key=True)
    media_id = Column(ForeignKey("media.id"), nullable=False, index=True)
    ordinal = Column(Integer, nullable=False)
    pts = Column(Integer, nullable=False)
    time_base_num = Column(Integer, nullable=False)
    time_base_den = Column(Integer, nullable=False)
    relative_seconds = Column(Float, nullable=False, index=True)
    __table_args__ = (UniqueConstraint("media_id", "pts"), UniqueConstraint("media_id", "ordinal"))


class Job(Base):
    __tablename__ = "jobs"
    id = Column(String, primary_key=True)
    project_id = Column(ForeignKey("projects.id"), nullable=False)
    kind = Column(String, nullable=False)
    state = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    progress = Column(Integer, default=0, nullable=False)
    result = Column(JSON)
    error = Column(JSON)
    created_at = Column(String, default=now, nullable=False)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    heartbeat = Column(String)


class ProposalRun(Base):
    __tablename__ = "proposal_runs"
    id = Column(String, primary_key=True)
    job_id = Column(ForeignKey("jobs.id"), nullable=False, unique=True)
    media_id = Column(ForeignKey("media.id"), nullable=False, index=True)
    manifest = Column(JSON, nullable=False)
    frame_count = Column(Integer, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class ProposalFrame(Base):
    __tablename__ = "proposal_frames"
    id = Column(String, primary_key=True)
    run_id = Column(ForeignKey("proposal_runs.id"), nullable=False, index=True)
    frame_id = Column(ForeignKey("frames.id"), nullable=False)
    ordinal = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "frame_id"),)


class ROIVersion(Base):
    __tablename__ = "roi_versions"
    id = Column(String, primary_key=True)
    roi_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    media_id = Column(ForeignKey("media.id"), nullable=False)
    name = Column(String, nullable=False)
    polygon_source_px = Column(JSON, nullable=False)
    area_m2 = Column(Float, nullable=False)
    area_evidence = Column(Text, nullable=False)
    evidence_type = Column(String, nullable=False)
    valid_from_seconds = Column(Float, nullable=False)
    valid_to_seconds = Column(Float)
    boundary_policy = Column(String, nullable=False, default="INSIDE")
    created_at = Column(String, default=now, nullable=False)
    __table_args__ = (UniqueConstraint("roi_id", "version"),)


class Annotation(Base):
    __tablename__ = "annotations"
    id = Column(String, primary_key=True)
    frame_id = Column(ForeignKey("frames.id"), nullable=False)
    roi_id = Column(String, nullable=False)
    edit_version = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("frame_id", "roi_id"),)


class AnnotationRevision(Base):
    __tablename__ = "annotation_revisions"
    id = Column(String, primary_key=True)
    annotation_id = Column(ForeignKey("annotations.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    parent_id = Column(ForeignKey("annotation_revisions.id"))
    proposal_run_id = Column(ForeignKey("proposal_runs.id"), nullable=False)
    roi_version_id = Column(ForeignKey("roi_versions.id"), nullable=False)
    objects = Column(JSON, nullable=False)
    rules = Column(JSON, nullable=False)
    dependencies_hash = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    created_at = Column(String, default=now, nullable=False)
    __table_args__ = (UniqueConstraint("annotation_id", "version"),)


class ReviewRecord(Base):
    __tablename__ = "review_records"
    id = Column(String, primary_key=True)
    revision_id = Column(ForeignKey("annotation_revisions.id"), nullable=False, index=True)
    reviewer = Column(String, nullable=False)
    verdict = Column(String, nullable=False)
    scope = Column(JSON, nullable=False)
    completeness = Column(JSON, nullable=False)
    validity = Column(JSON, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class GTSnapshot(Base):
    __tablename__ = "gt_snapshots"
    id = Column(String, primary_key=True)
    media_id = Column(ForeignKey("media.id"), nullable=False, index=True)
    manifest = Column(JSON, nullable=False)
    manifest_hash = Column(String, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id = Column(String, primary_key=True)
    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)
    entity_id = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class GTObservation(Base):
    __tablename__ = "gt_observations"
    id = Column(String, primary_key=True)
    revision_id = Column(ForeignKey("annotation_revisions.id"), nullable=False)
    review_id = Column(ForeignKey("review_records.id"), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class ReviewActivity(Base):
    __tablename__ = "review_activity"
    id = Column(String, primary_key=True)
    annotation_id = Column(ForeignKey("annotations.id"), nullable=False, index=True)
    session_id = Column(String, nullable=False)
    reviewer = Column(String, nullable=False)
    seconds = Column(Float, nullable=False)
    interactions = Column(Integer, nullable=False)
    source = Column(String, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class IndependentCheck(Base):
    __tablename__ = "independent_checks"
    id = Column(String, primary_key=True)
    revision_id = Column(ForeignKey("annotation_revisions.id"), nullable=False, index=True)
    reviewer = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class Adjudication(Base):
    __tablename__ = "adjudications"
    id = Column(String, primary_key=True)
    check_id = Column(ForeignKey("independent_checks.id"), nullable=False, index=True)
    revision_id = Column(ForeignKey("annotation_revisions.id"), nullable=False)
    reviewer = Column(String, nullable=False)
    resolution = Column(String, nullable=False)
    notes = Column(Text, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class PilotReport(Base):
    __tablename__ = "pilot_reports"
    id = Column(String, primary_key=True)
    run_id = Column(ForeignKey("proposal_runs.id"), nullable=False)
    payload = Column(JSON, nullable=False)
    report_hash = Column(String, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class TrainingReview(Base):
    __tablename__ = "training_reviews"
    id = Column(String, primary_key=True)
    revision_id = Column(ForeignKey("annotation_revisions.id"), nullable=False, index=True)
    review_id = Column(ForeignKey("review_records.id"), nullable=False)
    scope_key = Column(String, nullable=False)
    reviewer = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class MediaPartition(Base):
    __tablename__ = "media_partitions"
    media_id = Column(ForeignKey("media.id"), primary_key=True)
    payload = Column(JSON, nullable=False)


class DatasetSnapshot(Base):
    __tablename__ = "dataset_snapshots"
    id = Column(String, primary_key=True)
    project_id = Column(ForeignKey("projects.id"), nullable=False, index=True)
    manifest = Column(JSON, nullable=False)
    manifest_hash = Column(String, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class SplitLock(Base):
    __tablename__ = "split_locks"
    token = Column(String, primary_key=True)
    split = Column(String, nullable=False)
    dataset_id = Column(ForeignKey("dataset_snapshots.id"), nullable=False)


class DatasetNotice(Base):
    __tablename__ = "dataset_notices"
    id = Column(String, primary_key=True)
    dataset_id = Column(ForeignKey("dataset_snapshots.id"), nullable=False, index=True)
    actor = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class WorkArtifact(Base):
    __tablename__ = 'work_artifacts'
    id = Column(String, primary_key=True)
    project_id = Column(ForeignKey('projects.id'), nullable=True, index=True)
    kind = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    content_hash = Column(String, nullable=False)
    created_at = Column(String, default=now, nullable=False)


class WorkPointer(Base):
    __tablename__ = 'work_pointers'
    key = Column(String, primary_key=True)
    artifact_id = Column(ForeignKey('work_artifacts.id'), nullable=False)
    version = Column(Integer, nullable=False)
