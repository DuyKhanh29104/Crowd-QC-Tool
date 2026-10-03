"""One durable job per process. An OS lease serializes model work across projects."""
from fractions import Fraction
import io
import json
from pathlib import Path
import sys
import time
import uuid
from sqlalchemy import select, update
from backend.app.db.models import Job, Media, Frame, ProposalRun, ProposalFrame, now
from backend.app.db.session import make_engine, sessions
from backend.app.errors import DomainError
from backend.app.media.lease import WorkerLease
from backend.app.media.service import verify_source, digest, frame_image
from backend.app.models.detector import PersonDetector
from backend.app.jobs.profiles import gpu_lease, cache_key, json_hash, runtime_versions


class Cancelled(Exception):
    pass


def selected_frames(db, media, payload):
    """Exact PTS sampling: first available frame >= each grid point; never duplicate a frame."""
    step = Fraction(str(payload["every_seconds"]))
    target = Fraction(str(payload["start_seconds"]))
    end = Fraction(str(payload["end_seconds"]))
    start = Fraction(media.start_pts*media.time_base_num, media.time_base_den)
    selected_ids = []
    # Stream only sampling metadata; hydrate ORM objects only for selected frames.
    query = select(Frame.id, Frame.pts, Frame.time_base_num, Frame.time_base_den).where(Frame.media_id==media.id).order_by(Frame.ordinal)
    for frame in db.execute(query).yield_per(500):
        stamp = Fraction(frame.pts*frame.time_base_num, frame.time_base_den)-start
        if stamp > end: break
        if stamp < target: continue
        selected_ids.append(frame.id)
        target += (int((stamp-target)//step)+1)*step
        if len(selected_ids) == payload["max_frames"]: break
    result = []
    for offset in range(0, len(selected_ids), 500):
        batch = selected_ids[offset:offset+500]
        frames = {frame.id: frame for frame in db.scalars(select(Frame).where(Frame.id.in_(batch)))}
        result.extend(frames[id] for id in batch)
    return result


def run(root, job_id, detector_factory=PersonDetector):
    root = Path(root)
    engine = make_engine(root)
    Session = sessions(engine)
    lease = WorkerLease(root,job_id)
    resource = gpu_lease()
    if not lease.acquire():
        engine.dispose()
        return
    staging = root/"proposal_staging"/f"{job_id}.jsonl.partial"
    detector = None

    def checkpoint(progress=None):
        with Session.begin() as db:
            job = db.get(Job,job_id)
            if job.cancel_requested or job.state not in ("QUEUED","RUNNING"):
                raise Cancelled()
            job.heartbeat = now()
            if progress is not None: job.progress = progress

    try:
        with Session() as db:
            job = db.get(Job,job_id)
            if not job or job.kind != "AUTO_LABEL" or job.state != "QUEUED": return
            payload = job.payload
            media = db.get(Media,payload["media_id"])
            frames = selected_frames(db,media,payload)
        if not frames: raise ValueError("NO_SAMPLED_FRAMES")
        # Waiting remains QUEUED, cancel is responsive even when another process holds the GPU.
        while not resource.acquire():
            checkpoint()
            time.sleep(.25)
        checkpoint()
        with Session.begin() as db:
            claimed = db.execute(update(Job).where(Job.id==job_id,Job.state=="QUEUED",Job.cancel_requested==False)
                .values(state="RUNNING",heartbeat=now()))
            if claimed.rowcount != 1: raise Cancelled()
        verify_source(media,full=True)
        p = payload["profile"]
        weights = Path(p["weights_path"])
        if not weights.is_file(): raise ValueError("WEIGHTS_MISSING")
        if digest(weights) != payload["weights_sha256"]: raise ValueError("WEIGHTS_HASH_MISMATCH")
        if json_hash(p["config"]) != payload["config_sha256"]: raise ValueError("CONFIG_HASH_MISMATCH")
        cache_dir = root/"detector_cache"
        cache_dir.mkdir(parents=True,exist_ok=True)
        staging.parent.mkdir(parents=True,exist_ok=True)
        detector, hits = None, 0
        run_id = uuid.uuid4().hex
        started = time.perf_counter()
        with staging.open("w",encoding="utf-8") as output:
            for i, frame in enumerate(frames):
                checkpoint(int(i*95/len(frames)))
                key = cache_key(media,frame,payload)
                cache = cache_dir/f"{key}.json"
                raw = None
                if cache.exists():
                    try:
                        stored = json.loads(cache.read_text(encoding="utf-8"))
                        if stored["key"]==key and json_hash(stored["raw"])==stored["sha256"]:
                            raw = stored["raw"]
                    except (ValueError,KeyError): pass  # Invalid cache is recomputed, never trusted.
                if raw is None:
                    if detector is None: detector = detector_factory(weights,p["config"],p["device"])
                    from PIL import Image
                    image = Image.open(io.BytesIO(frame_image(media,frame,root/"worker_frame_cache",64*1024*1024))).convert("RGB")
                    objects = detector.predict({"width":media.width,"height":media.height,"pts":frame.pts},image)
                    raw = {"objects":objects,"actual_model_device":detector.actual_device}
                    temporary = cache.with_suffix(f".{job_id}.partial")
                    temporary.write_text(json.dumps({"key":key,"raw":raw,"sha256":json_hash(raw)},allow_nan=False),encoding="utf-8")
                    temporary.replace(cache)
                else: hits += 1
                checkpoint()
                record = {"frame_id":frame.id,"ordinal":frame.ordinal,"pts":frame.pts,
                    "time_base_num":frame.time_base_num,"time_base_den":frame.time_base_den,
                    "relative_seconds":frame.relative_seconds,"annotation_state":"PROPOSAL","reviewed":False,
                    "ground_truth":False,"cache_key":key,"coordinate_mapping":p["transform"],**raw}
                # Each rerun gets a new lineage, even when raw detector outputs come from cache.
                for obj in record["objects"]:
                    obj["source_proposal_id"] = f"{run_id}:{frame.id}:{obj['object_id']}"
                output.write(json.dumps(record,allow_nan=False)+"\n")
        verify_source(media,full=True)
        if digest(weights)!=payload["weights_sha256"]: raise ValueError("WEIGHTS_HASH_MISMATCH")
        checkpoint(95)
        manifest = {"schema_version":"proposal_run_v1","annotation_state":"PROPOSAL","ground_truth":False,
            "evidence_type":media.evidence_type,"source_sha256":media.sha256,"stream_index":media.stream_index,
            "weights_sha256":payload["weights_sha256"],"config_sha256":payload["config_sha256"],
            "profile":p,"sampling":{k:payload[k] for k in ("start_seconds","end_seconds","every_seconds","max_frames")},
            "runtime":runtime_versions(),"cache_hits":hits,"inferred_frames":len(frames)-hits,
            "elapsed_seconds":time.perf_counter()-started,"payload_sha256":digest(staging),
            "actual_model_device":raw["actual_model_device"]}
        # The transaction is the publish boundary. No partial run is visible via API.
        with Session.begin() as db:
            committed = db.execute(update(Job).where(Job.id==job_id,Job.state=="RUNNING",Job.cancel_requested==False)
                .values(state="SUCCEEDED",progress=100,heartbeat=now(),result={"proposal_run_id":run_id,"frames":len(frames),"cache_hits":hits}))
            if committed.rowcount != 1: raise Cancelled()
            db.add(ProposalRun(id=run_id,job_id=job_id,media_id=media.id,manifest=manifest,frame_count=len(frames)))
            db.flush()
            with staging.open(encoding="utf-8") as data:
                batch=[]
                for line in data:
                    record=json.loads(line)
                    batch.append({"id":uuid.uuid4().hex,"run_id":run_id,"frame_id":record["frame_id"],"ordinal":record["ordinal"],"payload":record})
                    if len(batch)==100:
                        db.execute(ProposalFrame.__table__.insert(),batch)
                        batch=[]
                if batch: db.execute(ProposalFrame.__table__.insert(),batch)
    except Exception as exc:
        with Session.begin() as db:
            job=db.get(Job,job_id)
            if job and job.state in ("QUEUED","RUNNING"):
                cancelled=isinstance(exc,Cancelled) or job.cancel_requested
                job.state="CANCELLED" if cancelled else "FAILED"
                message=str(exc)
                code=(exc.code if isinstance(exc,DomainError) else
                      "CUDA_OOM" if "out of memory" in message.lower() else
                      message.split(":")[0] if message.split(":")[0].isupper() else "INFERENCE_FAILED")
                job.error=None if cancelled else {"code":code,"message":message or type(exc).__name__,"retryable":True}
                job.heartbeat=now()
    finally:
        try:
            # Release model allocations before another worker can acquire the GPU lease.
            if detector is not None:
                del detector
                import gc
                gc.collect()
                if "torch" in sys.modules:
                    torch=sys.modules["torch"]
                    if torch.cuda.is_available(): torch.cuda.empty_cache()
            staging.unlink(missing_ok=True)
        finally:
            resource.release()
            lease.release()
            engine.dispose()


if __name__ == "__main__":
    run(Path(sys.argv[1]),sys.argv[2])
