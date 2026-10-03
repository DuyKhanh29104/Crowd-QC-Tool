import copy
import os
from pathlib import Path
import subprocess
import uuid
from typing import Literal
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from backend.app.db.models import Job, Media, ProposalRun, ProposalFrame, ROIVersion, WorkPointer
from backend.app.errors import DomainError
from backend.app.jobs.profiles import ROOT, profile, json_hash
from backend.app.media.service import digest
from backend.app.roi.geometry import membership


class AutoLabelInput(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)
    profile_id: Literal["person_gpu_v1","person_cpu_v1"]="person_gpu_v1"
    start_seconds: float=Field(default=0,ge=0)
    end_seconds: float|None=Field(default=None,ge=0)
    every_seconds: float=Field(default=1,gt=0)
    max_frames: int=Field(default=300,ge=1,le=5000)


def register(app,root,Session,require,serialize,processes):
    router=APIRouter(prefix="/api")

    def launch(job_id):
        python=Path(os.environ.get("CROWD_QC_WORKER_PYTHON",ROOT/".venv-gpu/Scripts/python.exe"))
        folder=root/"job_logs"
        folder.mkdir(parents=True,exist_ok=True)
        try:
            with (folder/f"{job_id}.log").open("ab") as log:
                processes[job_id]=subprocess.Popen([str(python),"-m","backend.app.jobs.worker",str(root),job_id],
                    cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0,stdout=log,stderr=log)
        except OSError as exc:
            with Session.begin() as db:
                job=db.get(Job,job_id)
                job.state="FAILED"
                job.error={"code":"WORKER_START_FAILED","message":str(exc),"retryable":True}

    @router.get("/model-profiles")
    def profiles():
        return [{"id":id,"name":name,"device":profile(id)["device"],
                 "weights_available":Path(profile(id)["weights_path"]).is_file()}
            for id,name in (("person_gpu_v1","YOLO11n · GPU (mặc định)"),("person_cpu_v1","YOLO11n · CPU"))]

    @router.post("/media/{id}/auto-label",status_code=202)
    def submit(id:str,value:AutoLabelInput):
        p=profile(value.profile_id)
        weights=Path(p["weights_path"])
        with Session.begin() as db:
            media=require(db,Media,id)
            pointer=db.get(WorkPointer,'active_model:'+media.evidence_type)
            if pointer or not weights.is_file():
                from backend.app.workflows.store import get,model_path,baseline
                model=get(db,pointer.artifact_id,'MODEL') if pointer else baseline(db)
                p=copy.deepcopy(model.payload['profile']);p['device']=profile(value.profile_id)['device'];p['id']=value.profile_id;p['model_version_id']=model.id
                p['weights_path']=str(model_path(db,model.payload))
                weights=Path(p['weights_path']);weights_hash=digest(weights)
                if weights_hash!=model.payload['weights_sha256']:raise DomainError('WEIGHTS_HASH_MISMATCH','Active weights đã đổi.',409)
            weights_hash=digest(weights)
            end=media.duration_seconds if value.end_seconds is None else value.end_seconds
            if value.start_seconds>end or end>media.duration_seconds or value.start_seconds>media.duration_seconds:
                raise DomainError("INVALID_SCOPE","Phạm vi lấy mẫu vượt video hoặc kết thúc trước bắt đầu.",422)
            payload={**value.model_dump(),"end_seconds":end,"media_id":id,"profile":p,
                "weights_sha256":weights_hash,"config_sha256":json_hash(p["config"])}
            job=Job(id=uuid.uuid4().hex,project_id=media.project_id,kind="AUTO_LABEL",state="QUEUED",progress=0,payload=payload)
            db.add(job)
            db.flush()
            job_id=job.id
        launch(job_id)
        return {"job_id":job_id}

    @router.post("/jobs/{id}/cancel")
    def cancel(id:str):
        with Session.begin() as db:
            job=require(db,Job,id)
            if job.kind not in ("AUTO_LABEL","TRAIN","HELPER_EVAL","AUTO_TRACK","QC_ANALYSIS","QC_EXPORT") or job.state not in ("QUEUED","RUNNING"):
                raise DomainError("CANCEL_NOT_ALLOWED","Tác vụ này đã kết thúc hoặc không hỗ trợ hủy.",409)
            changed=db.execute(update(Job).where(Job.id==id,Job.state.in_(["QUEUED","RUNNING"]))
                .values(cancel_requested=True))
            if not changed.rowcount: raise DomainError("CANCEL_NOT_ALLOWED","Tác vụ đã kết thúc.",409)
            return {"job_id":id,"cancel_requested":True}

    @router.get("/media/{id}/proposal-runs")
    def runs(id:str):
        with Session() as db:
            require(db,Media,id)
            return [serialize(r) for r in db.scalars(select(ProposalRun).where(ProposalRun.media_id==id).order_by(ProposalRun.created_at.desc()))]

    @router.get("/proposal-runs/{id}/frames")
    def proposal_frames(id:str,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),
                        frame_id:str|None=None,roi_version_id:str|None=None):
        with Session() as db:
            run=require(db,ProposalRun,id)
            roi=require(db,ROIVersion,roi_version_id) if roi_version_id else None
            if roi and roi.media_id!=run.media_id: raise DomainError("ROI_SCOPE_MISMATCH","ROI thuộc video khác.",422)
            query=select(ProposalFrame).where(ProposalFrame.run_id==id)
            if frame_id: query=query.where(ProposalFrame.frame_id==frame_id)
            items=[]
            for row in db.scalars(query.order_by(ProposalFrame.ordinal).offset(offset).limit(limit)):
                item=copy.deepcopy(row.payload)
                if roi:
                    valid=roi.valid_from_seconds<=item["relative_seconds"] and (roi.valid_to_seconds is None or item["relative_seconds"]<=roi.valid_to_seconds)
                    item["roi_version_id"]=roi.id
                    item["roi_status"]="VALID" if valid else "OUTSIDE_VALID_RANGE"
                    for obj in item["objects"]:
                        # Geometric hint is separate from reviewed membership; no heuristic becomes GT.
                        obj["roi_hint"]=membership(obj["ground_anchor"]["xy_source_px"],roi.polygon_source_px,True) if valid else "UNCERTAIN"
                items.append(item)
            return {"items":items,"total":run.frame_count,"annotation_state":"PROPOSAL","ground_truth":False}

    def retry(current):
        if current["state"] not in ("FAILED","INTERRUPTED","CANCELLED"):
            raise DomainError("RETRY_NOT_ALLOWED","Chỉ thử lại tác vụ đã lỗi, bị ngắt hoặc hủy.",409)
        # Retry preserves the original model/config/hash contract, not a newly selected checkpoint.
        with Session.begin() as db:
            job=Job(id=uuid.uuid4().hex,project_id=current["project_id"],kind="AUTO_LABEL",state="QUEUED",progress=0,
                    payload={**current["payload"],"retry_of":current["id"]})
            db.add(job)
            db.flush()
            job_id=job.id
        launch(job_id)
        return {"job_id":job_id}

    app.include_router(router)
    return retry
