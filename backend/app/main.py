from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fractions import Fraction
import math
import os
import re
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import uuid
from typing import Literal
from fastapi import FastAPI, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from backend.app.db.models import Project, Media, Frame, Job, ROIVersion
from backend.app.db.session import make_engine, migrate, sessions
from backend.app.errors import DomainError
from backend.app.media.service import local_file, verify_source, digest, frame_image
from backend.app.media.lease import worker_active
from backend.app.roi.geometry import validate_polygon, membership

ROOT=Path(__file__).resolve().parents[2]


class Input(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False,str_strip_whitespace=True)


class ProjectInput(Input):
    name: str=Field(min_length=1,max_length=120)


class MediaInput(Input):
    source_path: str=Field(min_length=1)
    camera_id: str=Field(min_length=1,max_length=120)
    evidence_type: Literal["DEMO","REAL"]="DEMO"
    video_stream: int=Field(default=0,ge=0)
    parent_media_id: str|None=None
    source_offset_seconds: float=Field(default=0,ge=0)


class RemapInput(Input):
    source_path: str=Field(min_length=1)


class ROIInput(Input):
    name: str=Field(min_length=1,max_length=120)
    polygon_source_px: list[tuple[float,float]]
    area_m2: float=Field(gt=0)
    area_evidence: str=Field(default="USER_ENTERED_AREA; not independently verified",min_length=1,max_length=4000)
    evidence_type: Literal["DEMO","MEASURED","USER_DECLARED"]="USER_DECLARED"
    valid_from_seconds: Literal[0]=0
    valid_to_seconds: None=None
    base_version_id: str|None=None


class MembershipInput(Input):
    point: tuple[float,float]|None=None
    anchor_verified: bool=False


def serialize(row):
    return {c.name:getattr(row,c.name) for c in row.__table__.columns}


def require(db,kind,id):
    obj=db.get(kind,id)
    if obj is None: raise DomainError("NOT_FOUND","Không tìm thấy bản ghi.",404)
    return obj


def create_app(data_root=None,cache_bytes=None):
    root=Path(data_root or os.environ.get("CROWD_QC_DATA_ROOT",ROOT/"workspace_data")).resolve()
    budget=cache_bytes if cache_bytes is not None else int(os.environ.get("CROWD_QC_CACHE_BYTES",256*1024*1024))
    if budget<0: raise ValueError("Cache budget must be nonnegative")
    engine=make_engine(root)
    migrate(engine)
    Session=sessions(engine)
    processes={}

    @asynccontextmanager
    async def lifespan(app):
        yield
        # Import subprocesses are allowed to finish and commit without a running UI/API.
        from backend.app.media.decoder import decoders
        decoders.close()
        engine.dispose()

    app=FastAPI(title="Crowd QC local",lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["localhost","127.0.0.1","testserver"])
    app.state.engine=engine
    app.state.data_root=root

    @app.middleware("http")
    async def local_origin(request:Request,call_next):
        # Local app has filesystem access: reject cross-origin browser writes.
        origin=request.headers.get("origin")
        allowed={f"http://{host}:{port}" for host in ("localhost","127.0.0.1") for port in (8765,5173)}
        if origin and origin not in allowed:
            return JSONResponse({"code":"ORIGIN_REJECTED","message":"Chỉ cho phép UI local.","retryable":False},status_code=403)
        return await call_next(request)

    @app.exception_handler(DomainError)
    async def domain_error(_,exc):
        return JSONResponse(exc.payload(),status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_,exc):
        details=[{"field":".".join(map(str,e["loc"])),"message":e["msg"]} for e in exc.errors()]
        return JSONResponse({"code":"VALIDATION_ERROR","message":"; ".join(e["field"]+": "+e["message"] for e in details),"details":details,"retryable":False},status_code=422)

    @app.get("/api/health")
    def health(): return {"status":"ok","schema_version":"0006_workflows","scope":"T01-T31_DEMO_AND_REFERENCE"}

    @app.get("/api/projects")
    def projects():
        with Session() as db: return [serialize(p) for p in db.scalars(select(Project).order_by(Project.created_at))]

    @app.post("/api/projects",status_code=201)
    def new_project(value:ProjectInput):
        id=uuid.uuid4().hex
        folder=root/"projects"/id
        folder.mkdir(parents=True)
        with Session.begin() as db:
            project=Project(id=id,name=value.name,data_root=str(folder),schema_version=1)
            db.add(project)
            db.flush()
            return serialize(project)

    @app.get("/api/projects/{id}/media")
    def media_list(id:str):
        with Session() as db:
            require(db,Project,id)
            return [serialize(m) for m in db.scalars(select(Media).where(Media.project_id==id).order_by(Media.created_at))]

    @app.post("/api/projects/{id}/media",status_code=202)
    def import_media(id:str,value:MediaInput):
        path=local_file(value.source_path)
        if value.source_offset_seconds and not value.parent_media_id:
            raise DomainError("CLIP_PARENT_REQUIRED","Offset cần source media gốc.",422)
        with Session.begin() as db:
            require(db,Project,id)
            job=Job(id=uuid.uuid4().hex,project_id=id,kind="MEDIA_IMPORT",state="QUEUED",progress=0,payload={**value.model_dump(),"source_path":str(path)})
            db.add(job)
            db.flush()
            job_id=job.id
        flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
        try:
            processes[job_id]=subprocess.Popen([sys.executable,"-m","backend.app.media.importer",str(root),job_id],cwd=ROOT,creationflags=flags,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except OSError as exc:
            with Session.begin() as db:
                job=db.get(Job,job_id)
                job.state="FAILED"
                job.error={"code":"WORKER_START_FAILED","message":str(exc)}
            raise DomainError("WORKER_START_FAILED",str(exc),500)
        return {"job_id":job_id}

    @app.post("/api/projects/{id}/media/upload",status_code=202)
    async def upload_media(id:str,request:Request,filename:str=Query(min_length=1,max_length=255),
                           camera_id:str=Query(min_length=1,max_length=120),
                           evidence_type:Literal["DEMO","REAL"]="REAL",
                           parent_media_id:str|None=None,source_offset_seconds:float=Query(default=0,ge=0)):
        # The browser provides bytes, never a usable absolute local path.
        with Session() as db:
            require(db,Project,id)
            if parent_media_id:
                parent=require(db,Media,parent_media_id)
                if parent.project_id!=id:
                    raise DomainError("INVALID_PARENT","Video gốc phải thuộc cùng dự án.",422)
        if source_offset_seconds and not parent_media_id:
            raise DomainError("CLIP_PARENT_REQUIRED","Offset cần video gốc.",422)
        name=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_',filename.replace('\\','/').split('/')[-1]).strip(' .')
        if not name or len(name)>180 or PureWindowsPath(name).is_reserved():
            raise DomainError("INVALID_FILENAME","Tên video không hợp lệ hoặc quá dài.",422)
        try:
            value=MediaInput(source_path=name,camera_id=camera_id,evidence_type=evidence_type,
                             parent_media_id=parent_media_id,source_offset_seconds=source_offset_seconds)
        except ValidationError:
            raise DomainError("INVALID_UPLOAD_METADATA","Kiểm tra tên camera và thời gian clip.",422)
        folder=root/"projects"/id/"sources"/uuid.uuid4().hex
        folder.mkdir(parents=True)
        target=folder/name
        partial=folder/(name+".partial")
        size=0
        try:
            with partial.open("xb") as output:
                async for chunk in request.stream():
                    size+=len(chunk)
                    await run_in_threadpool(output.write,chunk)
            if not size:
                raise DomainError("EMPTY_UPLOAD","File video rỗng. Hãy chọn file khác.",422)
            partial.rename(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            folder.rmdir()
            raise
        return await run_in_threadpool(import_media,id,value.model_copy(update={"source_path":str(target)}))

    @app.get("/api/jobs/{id}")
    def job_status(id:str):
        with Session.begin() as db:
            job=require(db,Job,id)
            process=processes.get(id)
            if process and process.poll() is not None:
                processes.pop(id,None)
            if job.state in ("QUEUED", "RUNNING"):
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(job.heartbeat or job.created_at)).total_seconds()
                if (job.state == "RUNNING" or age > 30) and not worker_active(root,id):
                    db.execute(update(Job).where(Job.id==id,Job.state==job.state).values(
                        state="INTERRUPTED",error={"code":"WORKER_INTERRUPTED","message":"Tác vụ bị ngắt trước khi hoàn tất. Có thể thử lại; dữ liệu đã lưu không đổi.","retryable":True}),execution_options={"synchronize_session":False})
                    db.refresh(job)
            return serialize(job)

    @app.get("/api/projects/{id}/jobs")
    def project_jobs(id:str):
        with Session() as db:
            require(db,Project,id)
            # Include every active job even if older than the recent history page.
            active=list(db.scalars(select(Job.id).where(Job.project_id==id,Job.state.in_(["QUEUED","RUNNING"]))))
            recent=list(db.scalars(select(Job.id).where(Job.project_id==id).order_by(Job.created_at.desc()).limit(25)))
        return [job_status(job_id) for job_id in dict.fromkeys(active+recent)]

    @app.post("/api/jobs/{id}/retry",status_code=202)
    def retry_import(id:str):
        current=job_status(id)
        if current["kind"] == "AUTO_LABEL": return retry_auto_label(current)
        if current["kind"] != "MEDIA_IMPORT" or current["state"] not in ("FAILED","INTERRUPTED"):
            raise DomainError("RETRY_NOT_ALLOWED","Chỉ thử lại import đã lỗi hoặc bị ngắt.",409)
        return import_media(current["project_id"],MediaInput(**current["payload"]))

    @app.post("/api/demo",status_code=202)
    def demo():
        from backend.app.media.demo import make_video
        project=new_project(ProjectInput(name="DEMO · Sảnh mô phỏng"))
        folder=Path(project["data_root"])/"sources"
        cfr=make_video(folder/"demo_cfr.mkv",list(range(0,2000,100)))
        vfr=make_video(folder/"demo_vfr.mkv",[1000,1100,1370,1900,2300,2600,3200,3500])
        job_ids=[import_media(project["id"],MediaInput(source_path=str(path),camera_id="camera_demo",evidence_type="DEMO"))["job_id"] for path in (cfr,vfr)]
        return {"project_id":project["id"],"job_ids":job_ids,"evidence_type":"DEMO"}

    @app.get("/api/media/{id}")
    def media_detail(id:str):
        with Session() as db:
            media=require(db,Media,id)
            result=serialize(media)
            try:
                verify_source(media,full=True)
                result["source_status"]="VERIFIED"
            except DomainError as exc:
                result["source_status"]=exc.code
                result["source_error"]=exc.payload()
            return result

    @app.post("/api/media/{id}/remap")
    def remap(id:str,value:RemapInput):
        path=local_file(value.source_path)
        new_hash=digest(path)
        with Session.begin() as db:
            media=require(db,Media,id)
            if new_hash!=media.sha256: raise DomainError("SOURCE_HASH_MISMATCH","File mới không trùng SHA-256 của source gốc.",409)
            stat=path.stat()
            media.source_path=str(path)
            media.size_bytes=stat.st_size
            media.mtime_ns=str(stat.st_mtime_ns)
            return {"status":"VERIFIED","media_id":id}

    def frame_payload(frame,media,db):
        result=serialize(frame)
        timestamp=Fraction(frame.pts*frame.time_base_num,frame.time_base_den)
        result["source_timestamp_seconds"]=float(timestamp)
        result["image_url"]=f"/api/frames/{frame.id}/image"
        result["source_media_id"]=media.parent_media_id or media.id
        if media.parent_media_id:
            parent=require(db,Media,media.parent_media_id)
            mapped=Fraction(parent.start_pts*parent.time_base_num,parent.time_base_den)+Fraction(media.source_offset_seconds)+(timestamp-Fraction(media.start_pts*media.time_base_num,media.time_base_den))
            result["mapped_source_timestamp_seconds"]=float(mapped)
            result["source_mapping_status"]="USER_DECLARED_OFFSET"
        else:
            result["mapped_source_timestamp_seconds"]=float(timestamp)
            result["source_mapping_status"]="ORIGINAL"
        return result

    @app.get("/api/media/{id}/frames")
    def frames(id:str,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),timestamp:float|None=Query(None,ge=0)):
        with Session() as db:
            media=require(db,Media,id)
            query=select(Frame).where(Frame.media_id==id)
            if timestamp is not None:
                if not math.isfinite(timestamp): raise DomainError("INVALID_TIMESTAMP","Timestamp phải hữu hạn.",422)
                # Explicit selection: first source frame at or after requested relative time.
                query=query.where(Frame.relative_seconds>=timestamp)
            result=list(db.scalars(query.order_by(Frame.ordinal).offset(offset).limit(limit)))
            return {"items":[frame_payload(f,media,db) for f in result],"total":media.frame_count,"selection":"at_or_after","offset":offset}

    @app.get("/api/frames/{id}/image")
    def image(id:str):
        with Session() as db:
            frame=require(db,Frame,id)
            media=require(db,Media,frame.media_id)
            data=frame_image(media,frame,root/"frame_cache",budget)
            return Response(data,media_type="image/png",headers={"X-Source-PTS":str(frame.pts),"X-Time-Base":f"{frame.time_base_num}/{frame.time_base_den}","Cache-Control":"no-store"})

    @app.get("/api/media/{id}/rois")
    def rois(id:str):
        with Session() as db:
            require(db,Media,id)
            return [serialize(r) for r in db.scalars(select(ROIVersion).where(ROIVersion.media_id==id).order_by(ROIVersion.created_at,ROIVersion.version))]

    def save_roi(db,media,value,roi_id,version):
        validate_polygon(value.polygon_source_px,media.width,media.height)
        if value.valid_to_seconds is not None and value.valid_to_seconds<=value.valid_from_seconds:
            raise DomainError("INVALID_VALID_RANGE","Thời gian kết thúc phải lớn hơn bắt đầu.",422)
        if value.valid_from_seconds>media.duration_seconds or (value.valid_to_seconds is not None and value.valid_to_seconds>media.duration_seconds):
            raise DomainError("INVALID_VALID_RANGE","Phạm vi hiệu lực vượt video.",422)
        if media.evidence_type=="DEMO" and value.evidence_type=="MEASURED":
            raise DomainError("DEMO_EVIDENCE_REQUIRED","Video DEMO chỉ dùng diện tích mô phỏng.",422)
        if media.evidence_type=="DEMO":value=value.model_copy(update={'evidence_type':'DEMO'})
        row=ROIVersion(id=uuid.uuid4().hex,roi_id=roi_id,version=version,media_id=media.id,boundary_policy="INSIDE",**value.model_dump(exclude={"base_version_id"}))
        db.add(row)
        db.flush()
        return serialize(row)

    @app.post("/api/media/{id}/rois",status_code=201)
    def create_roi(id:str,value:ROIInput):
        if value.base_version_id: raise DomainError("INVALID_BASE","ROI mới không có base version.",422)
        with Session.begin() as db:
            return save_roi(db,require(db,Media,id),value,uuid.uuid4().hex,1)

    @app.post("/api/rois/{id}/versions",status_code=201)
    def version_roi(id:str,value:ROIInput):
        try:
            with Session.begin() as db:
                latest=db.scalar(select(ROIVersion).where(ROIVersion.roi_id==id).order_by(ROIVersion.version.desc()).limit(1))
                if not latest: raise DomainError("NOT_FOUND","ROI không tồn tại.",404)
                if value.base_version_id!=latest.id:
                    raise DomainError("VERSION_CONFLICT","ROI đã có phiên bản mới; tải lại trước khi lưu.",409)
                return save_roi(db,require(db,Media,latest.media_id),value,id,latest.version+1)
        except IntegrityError:
            raise DomainError("VERSION_CONFLICT","Có thay đổi đồng thời; tải lại ROI.",409)

    @app.post("/api/roi-versions/{id}/membership")
    def test_membership(id:str,value:MembershipInput):
        with Session() as db:
            roi=require(db,ROIVersion,id)
            return {"membership":membership(value.point,roi.polygon_source_px,value.anchor_verified),"roi_version_id":id,"boundary_policy":roi.boundary_policy}

    from backend.app.jobs.api import register
    retry_auto_label=register(app,root,Session,require,serialize,processes)
    from backend.app.annotations.api import register as register_review
    register_review(app,Session,require,serialize)
    from backend.app.datasets.api import register as register_datasets
    register_datasets(app,root,Session,require,serialize)
    from backend.app.workflows.api import register as register_workflows
    register_workflows(app,root,Session,require,processes)
    from backend.app.qc.api import register as register_qc
    register_qc(app,root,Session,require,processes)
    dist=ROOT/"frontend/dist"
    if dist.is_dir(): app.mount("/",StaticFiles(directory=dist,html=True),name="frontend")
    return app
