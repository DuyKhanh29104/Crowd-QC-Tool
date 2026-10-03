"""Dedicated metadata subprocess with bounded frame-index buffering."""
import json
import sys
import uuid
from pathlib import Path
from sqlalchemy import update
from backend.app.db.models import Job, Media, Frame
from backend.app.db.session import make_engine, sessions
from backend.app.media.service import local_file, digest, inspect_video
from backend.app.media.lease import WorkerLease
from backend.app.errors import DomainError


def run(root, job_id):
    lease = WorkerLease(root, job_id)
    if not lease.acquire():
        return
    engine = make_engine(root)
    Session = sessions(engine)
    staging = Path(root) / "import_staging" / f"{job_id}.jsonl.partial"
    try:
        with Session.begin() as db:
            claim = db.execute(update(Job).where(Job.id == job_id, Job.state == "QUEUED").values(state="RUNNING"))
            if claim.rowcount != 1:
                return
            job = db.get(Job, job_id)
            payload, project_id = job.payload, job.project_id
        path = local_file(payload["source_path"])
        before = path.stat()
        source_hash = digest(path)
        staging.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("w", encoding="utf-8") as output:
            def on_frame(record):
                output.write(json.dumps(record) + "\n")
            metadata, _ = inspect_video(path, payload["video_stream"], on_frame=on_frame)
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or digest(path) != source_hash:
            raise DomainError("SOURCE_CHANGED_DURING_IMPORT", "Video đổi trong lúc import.", 409)
        media_id = uuid.uuid4().hex
        with Session.begin() as db:
            job = db.get(Job, job_id)
            if job.state != "RUNNING":
                raise DomainError("IMPORT_INVALIDATED", "Job không còn ở trạng thái RUNNING.", 409)
            parent_id = payload.get("parent_media_id")
            if parent_id:
                parent = db.get(Media, parent_id)
                if parent is None or parent.project_id != project_id or parent.parent_media_id:
                    raise DomainError("INVALID_CLIP_PARENT", "Chọn source gốc trong cùng project, không nối chuỗi clip.", 422)
                if float(payload["source_offset_seconds"]) + metadata["duration_seconds"] > parent.duration_seconds + 1e-6:
                    raise DomainError("CLIP_OUT_OF_RANGE", "Clip vượt thời lượng source gốc.", 422)
            db.add(Media(id=media_id, project_id=project_id, source_path=str(path), name=path.name,
                         camera_id=payload["camera_id"], evidence_type=payload["evidence_type"],
                         sha256=source_hash, size_bytes=after.st_size, mtime_ns=str(after.st_mtime_ns),
                         parent_media_id=parent_id, source_offset_seconds=str(payload["source_offset_seconds"]), **metadata))
            db.flush()
            batch = []
            with staging.open(encoding="utf-8") as indexed:
                for line in indexed:
                    batch.append(dict(id=uuid.uuid4().hex, media_id=media_id, **json.loads(line)))
                    if len(batch) == 1000:
                        db.bulk_insert_mappings(Frame, batch)
                        batch.clear()
                if batch:
                    db.bulk_insert_mappings(Frame, batch)
            job.state, job.progress, job.result = "SUCCEEDED", 100, {"media_id": media_id}
    except Exception as exc:
        with Session.begin() as db:
            error = exc.payload() if isinstance(exc, DomainError) else {"code": "IMPORT_FAILED", "message": str(exc), "retryable": False}
            db.execute(update(Job).where(Job.id == job_id, Job.state == "RUNNING").values(state="FAILED", error=error))
    finally:
        staging.unlink(missing_ok=True)
        engine.dispose()
        lease.release()


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2])
