import copy
import json
from pathlib import Path
from typing import Literal
import uuid
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update, text
from backend.app.db.models import Annotation, AnnotationRevision, ReviewRecord, GTSnapshot, AuditEvent, ProposalRun, ProposalFrame, Frame, Media, ROIVersion, GTObservation
from backend.app.errors import DomainError
from backend.app.jobs.profiles import ROOT, json_hash
from backend.app.media.service import verify_source
from backend.app.annotations.quality import flags, POLICY
from backend.app.gt.observations import derive, observation


class Input(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False,str_strip_whitespace=True)


class Seed(Input):
    proposal_run_id: str
    roi_version_id: str
    actor: str=Field(default="local_operator",min_length=1,max_length=120)


class ObjectInput(Input):
    id: str=Field(min_length=1,max_length=100)
    box: tuple[float,float,float,float]|None=None
    marker: tuple[float,float]
    anchor: tuple[float,float]|None=None
    anchor_verified: bool=False


class Save(Input):
    expected_edit_version: int=Field(ge=1)
    roi_version_id: str
    objects: list[ObjectInput]=Field(max_length=1000)
    actor: str=Field(default="local_operator",min_length=1,max_length=120)


class Review(Input):
    expected_edit_version: int=Field(ge=1)
    reviewer: str=Field(min_length=1,max_length=120)
    verdict: Literal["APPROVED","INCOMPLETE"]
    reviewed_scope: Literal["ROI","FULL_FRAME"]
    viewed: Literal[True]
    count_complete: bool=False
    boxes_complete: bool=False
    anchors_complete: bool=False
    notes: str=Field(default="",max_length=4000)


class Snapshot(Input):
    revision_ids: list[str]=Field(min_length=1,max_length=5000)
    actor: str=Field(min_length=1,max_length=120)


class Preview(Input):
    expected_edit_version: int=Field(ge=1)
    objects: list[ObjectInput]=Field(max_length=1000)
    count_complete: bool=False


def rules():
    return json.loads((ROOT/"configs/rules_v1.json").read_text(encoding="utf-8"))


def seed_objects(payload):
    return [{"id":o["object_id"],"box":o["geometry"]["xyxy_source_px"],"marker":o["person_marker"],
        "anchor":o["ground_anchor"]["xy_source_px"],"anchor_verified":False,"origin":"model",
        "anchor_origin":"model_heuristic","source_proposal_id":o["source_proposal_id"],"score":o["score"]}
        for o in payload["objects"]]


def register(app,Session,require,serialize):
    router=APIRouter(prefix="/api/review")

    def audit(db,actor,action,entity,payload):
        db.add(AuditEvent(id=uuid.uuid4().hex,actor=actor,action=action,entity_id=entity,payload=payload))

    def current(db,ann):
        return db.scalar(select(AnnotationRevision).where(AnnotationRevision.annotation_id==ann.id,AnnotationRevision.version==ann.edit_version))

    def last_review(db,rev):
        return db.scalar(select(ReviewRecord).where(ReviewRecord.revision_id==rev.id).order_by(ReviewRecord.created_at.desc()).limit(1))

    def scope(db,frame,roi_id):
        roi=require(db,ROIVersion,roi_id)
        if roi.media_id!=frame.media_id or frame.relative_seconds<roi.valid_from_seconds or (roi.valid_to_seconds is not None and frame.relative_seconds>roi.valid_to_seconds):
            raise DomainError("ROI_SCOPE_MISMATCH","ROI không áp dụng cho frame này.",422)
        return roi

    def dependency(frame,media,roi,config):
        return json_hash({"frame_id":frame.id,"pts":frame.pts,"time_base":[frame.time_base_num,frame.time_base_den],
            "source_sha256":media.sha256,"roi":serialize(roi),"rules":config})

    def stale(db,rev,frame):
        roi=db.get(ROIVersion,rev.roi_version_id)
        latest=db.scalar(select(ROIVersion).where(ROIVersion.roi_id==roi.roi_id).order_by(ROIVersion.version.desc()).limit(1))
        media=db.get(Media,frame.media_id)
        return latest.id!=roi.id or rev.dependencies_hash!=dependency(frame,media,roi,rules())

    def output(db,ann):
        rev=current(db,ann)
        frame=db.get(Frame,ann.frame_id)
        review=last_review(db,rev)
        roi=db.get(ROIVersion,rev.roi_version_id)
        previous=db.scalar(select(ProposalFrame).where(ProposalFrame.run_id==rev.proposal_run_id,ProposalFrame.ordinal<frame.ordinal).order_by(ProposalFrame.ordinal.desc()).limit(1))
        numeric=observation(serialize(rev),serialize(roi),serialize(review) if review else None,serialize(db.get(Media,frame.media_id)),stale(db,rev,frame))
        return {"annotation":serialize(ann),"revision":serialize(rev),"review":serialize(review) if review else None,"observation":numeric,
            "stale":stale(db,rev,frame),"flags":flags(rev.objects,roi,rev.rules,len(previous.payload["objects"]) if previous else None),
            "flag_policy":POLICY,"frame":serialize(frame),"roi":serialize(roi)}

    @router.post("/frames/{id}/draft")
    def draft(id:str,value:Seed):
        with Session.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            frame=require(db,Frame,id)
            roi=scope(db,frame,value.roi_version_id)
            run=require(db,ProposalRun,value.proposal_run_id)
            proposal=db.scalar(select(ProposalFrame).where(ProposalFrame.run_id==run.id,ProposalFrame.frame_id==id))
            if not proposal or run.media_id!=frame.media_id: raise DomainError("PROPOSAL_SCOPE_MISMATCH","Proposal không thuộc frame này.",422)
            existing=db.scalar(select(Annotation).where(Annotation.frame_id==id,Annotation.roi_id==roi.roi_id))
            if existing: return output(db,existing) # A later AI run never replaces human edits.
            config=rules()
            media=db.get(Media,frame.media_id)
            ann=Annotation(id=uuid.uuid4().hex,frame_id=id,roi_id=roi.roi_id,edit_version=1)
            db.add(ann); db.flush()
            rev=AnnotationRevision(id=uuid.uuid4().hex,annotation_id=ann.id,version=1,proposal_run_id=run.id,
                roi_version_id=roi.id,objects=seed_objects(proposal.payload),rules=config,
                dependencies_hash=dependency(frame,media,roi,config),actor=value.actor)
            db.add(rev); db.flush()
            audit(db,value.actor,"DRAFT_FROM_PROPOSAL",ann.id,{"revision_id":rev.id,"proposal_run_id":run.id})
            return output(db,ann)

    @router.get("/annotations/{id}")
    def get_annotation(id:str):
        with Session() as db: return output(db,require(db,Annotation,id))

    @router.post("/annotations/{id}/preview")
    def preview(id:str,value:Preview):
        with Session() as db:
            ann=require(db,Annotation,id)
            if ann.edit_version!=value.expected_edit_version: raise DomainError("EDIT_CONFLICT","Preview dùng revision cũ; tải lại trước khi sửa.",409)
            rev=current(db,ann);frame=db.get(Frame,ann.frame_id);roi=db.get(ROIVersion,rev.roi_version_id)
            media=db.get(Media,frame.media_id)
            if len({o.id for o in value.objects})!=len(value.objects): raise DomainError("DUPLICATE_OBJECT","ID người bị trùng.",422)
            for obj in value.objects:
                if obj.anchor and not(0<=obj.anchor[0]<=media.width and 0<=obj.anchor[1]<=media.height):raise DomainError("INVALID_ANCHOR","Điểm chân ngoài ảnh.",422)
            return {**derive([o.model_dump(mode="json") for o in value.objects],roi.polygon_source_px,roi.area_m2,rev.rules,value.count_complete),
                "release_state":"DRAFT","gt_eligible":False,"evidence_type":media.evidence_type,"stale":stale(db,rev,frame)}

    @router.get("/annotations/{id}/history")
    def history(id:str):
        with Session() as db:
            require(db,Annotation,id)
            return [{"revision":serialize(r),"reviews":[serialize(x) for x in db.scalars(select(ReviewRecord).where(ReviewRecord.revision_id==r.id).order_by(ReviewRecord.created_at))]}
                for r in db.scalars(select(AnnotationRevision).where(AnnotationRevision.annotation_id==id).order_by(AnnotationRevision.version))]

    @router.post("/annotations/{id}/revisions",status_code=201)
    def save(id:str,value:Save):
        with Session.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            ann=require(db,Annotation,id)
            if ann.edit_version!=value.expected_edit_version: raise DomainError("EDIT_CONFLICT","Có phiên bản mới từ tab khác. Tải lại trước khi sửa tiếp.",409)
            old=current(db,ann)
            frame=db.get(Frame,ann.frame_id)
            roi=scope(db,frame,value.roi_version_id)
            if roi.roi_id!=ann.roi_id: raise DomainError("ROI_SCOPE_MISMATCH","Không đổi sang ROI khác trong cùng annotation.",422)
            media=db.get(Media,frame.media_id)
            prior={o["id"]:o for o in old.objects}
            # Undo can restore an object deleted in an earlier saved revision; retain its lineage.
            for historical in db.scalars(select(AnnotationRevision).where(AnnotationRevision.annotation_id==id).order_by(AnnotationRevision.version.desc())):
                for obj in historical.objects: prior.setdefault(obj["id"],obj)
            objects=[]
            if len({o.id for o in value.objects})!=len(value.objects): raise DomainError("DUPLICATE_OBJECT","ID người bị trùng.",422)
            for obj in value.objects:
                points=[obj.marker]+([obj.anchor] if obj.anchor is not None else [])
                if obj.box:
                    x1,y1,x2,y2=obj.box
                    if not (0<=x1<x2<=media.width and 0<=y1<y2<=media.height): raise DomainError("INVALID_BOX","Box vượt ảnh hoặc không có diện tích.",422)
                if any(not(0<=p[0]<=media.width and 0<=p[1]<=media.height) for p in points) or (obj.anchor_verified and obj.anchor is None):
                    raise DomainError("INVALID_ANCHOR","Marker/anchor phải trong ảnh; anchor xác nhận không được rỗng.",422)
                saved=obj.model_dump(mode="json")
                original=prior.get(obj.id)
                saved.update({"origin":original["origin"] if original and original.get("box")==saved["box"] and original["marker"]==saved["marker"] else "human_edit" if original else "human",
                    "source_proposal_id":original.get("source_proposal_id") if original else None,"score":original.get("score") if original else None,
                    "anchor_origin":original["anchor_origin"] if original and original.get("anchor")==saved["anchor"] else "human"})
                objects.append(saved)
            config=rules()
            rev=AnnotationRevision(id=uuid.uuid4().hex,annotation_id=id,version=ann.edit_version+1,parent_id=old.id,
                proposal_run_id=old.proposal_run_id,roi_version_id=roi.id,objects=objects,rules=config,
                dependencies_hash=dependency(frame,media,roi,config),actor=value.actor)
            ann.edit_version+=1
            db.add(rev); db.flush()
            audit(db,value.actor,"SAVE_REVISION",id,{"before":old.id,"after":rev.id})
            return output(db,ann)

    @router.post("/annotations/{id}/reviews",status_code=201)
    def review(id:str,value:Review):
        with Session.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            ann=require(db,Annotation,id)
            if ann.edit_version!=value.expected_edit_version: raise DomainError("EDIT_CONFLICT","Review phải dùng phiên bản mới nhất.",409)
            rev=current(db,ann); frame=db.get(Frame,ann.frame_id)
            if stale(db,rev,frame): raise DomainError("STALE_DEPENDENCIES","ROI/config đã đổi. Lưu revision mới và xem lại trước khi duyệt.",409)
            verify_source(db.get(Media,frame.media_id),full=True)
            if value.boxes_complete and any(o["box"] is None for o in rev.objects): raise DomainError("BOXES_INCOMPLETE","Có người chỉ có marker, chưa đủ box.",422)
            if value.anchors_complete and any(not o["anchor_verified"] for o in rev.objects): raise DomainError("ANCHORS_INCOMPLETE","Còn điểm chân chưa xác nhận.",422)
            approved=value.verdict=="APPROVED"
            count_valid=approved and value.count_complete and value.anchors_complete
            record=ReviewRecord(id=uuid.uuid4().hex,revision_id=rev.id,reviewer=value.reviewer,verdict=value.verdict,
                scope={"kind":value.reviewed_scope,"frame_id":frame.id,"pts":frame.pts,"roi_version_id":rev.roi_version_id,"viewed":True,"notes":value.notes},
                completeness={"count":value.count_complete,"person_boxes":value.boxes_complete,"ground_anchors":value.anchors_complete,"speed":False,"training_eligibility":"NOT_EVALUATED"},
                validity={"count":"VALID" if count_valid else "UNKNOWN","density":"VALID" if count_valid else "UNKNOWN","speed":"UNKNOWN","level":"NOT_COMPUTED","measurements":"NOT_COMPUTED_T11_PENDING"})
            db.add(record); db.flush()
            numeric=observation(serialize(rev),serialize(db.get(ROIVersion,rev.roi_version_id)),serialize(record),serialize(db.get(Media,frame.media_id)))
            record.validity={k:numeric[k]["status"] if approved else "UNKNOWN" for k in ("count","density","speed","level")}
            record.validity={**record.validity,"measurements":"COUNT_DENSITY_V1"}
            audit(db,value.reviewer,"REVIEW_FRAME",id,{"revision_id":rev.id,"review_id":record.id,"verdict":value.verdict})
            return output(db,ann)

    @router.get("/runs/{id}/queue")
    def queue(id:str,roi_version_id:str):
        with Session() as db:
            run=require(db,ProposalRun,id); roi=require(db,ROIVersion,roi_version_id)
            if roi.media_id!=run.media_id: raise DomainError("ROI_SCOPE_MISMATCH","ROI thuộc video khác.",422)
            config=rules(); items=[]; previous=None
            for row in db.scalars(select(ProposalFrame).where(ProposalFrame.run_id==id).order_by(ProposalFrame.ordinal)):
                frame=db.get(Frame,row.frame_id)
                ann=db.scalar(select(Annotation).where(Annotation.frame_id==frame.id,Annotation.roi_id==roi.roi_id))
                rev=current(db,ann) if ann else None
                objs=rev.objects if rev else seed_objects(row.payload)
                rec=last_review(db,rev) if rev else None
                is_stale=stale(db,rev,frame) if rev else False
                in_range=roi.valid_from_seconds<=frame.relative_seconds and (roi.valid_to_seconds is None or frame.relative_seconds<=roi.valid_to_seconds)
                items.append({"frame_id":frame.id,"ordinal":frame.ordinal,"pts":frame.pts,"relative_seconds":frame.relative_seconds,
                    "object_count":len(objs),"flags":flags(objs,roi,config,previous),"annotation_id":ann.id if ann else None,
                    "revision_id":rev.id if rev else None,"in_range":in_range,"stale":is_stale,
                    "state":"STALE" if is_stale else rec.verdict if rec else "DRAFT" if ann else "PROPOSAL"})
                previous=len(objs)
            return {"items":items,"flag_policy":POLICY}

    @router.post("/media/{id}/snapshots",status_code=201)
    def snapshot(id:str,value:Snapshot):
        with Session.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            media=require(db,Media,id); verify_source(media,full=True)
            entries=[]; seen=set()
            for rev_id in value.revision_ids:
                rev=require(db,AnnotationRevision,rev_id); ann=db.get(Annotation,rev.annotation_id); frame=db.get(Frame,ann.frame_id)
                if ann.id in seen or frame.media_id!=id: raise DomainError("SNAPSHOT_SCOPE_MISMATCH","Revision trùng scope hoặc thuộc video khác.",422)
                seen.add(ann.id)
                if ann.edit_version!=rev.version: raise DomainError("EDIT_CONFLICT","Revision đã có bản mới; cần review bản mới.",409)
                record=last_review(db,rev)
                if not record or record.verdict!="APPROVED": raise DomainError("NOT_REVIEWED","Snapshot chỉ chứa revision đã duyệt.",409)
                if stale(db,rev,frame): raise DomainError("STALE_DEPENDENCIES","Không phát hành revision dùng ROI/config cũ.",409)
                roi=db.get(ROIVersion,rev.roi_version_id)
                numeric=observation(serialize(rev),serialize(roi),serialize(record),serialize(media))
                if not db.get(GTObservation,numeric["dependencies_hash"]):
                    db.add(GTObservation(id=numeric["dependencies_hash"],revision_id=rev.id,review_id=record.id,payload=numeric))
                entries.append({"frame":serialize(frame),"revision":serialize(rev),"review":serialize(record),"observation":numeric,
                    "roi":serialize(db.get(ROIVersion,rev.roi_version_id)),"proposal_run":serialize(db.get(ProposalRun,rev.proposal_run_id))})
            manifest={"schema_version":"gt_snapshot_v2","snapshot_kind":"COUNT_DENSITY_GT","evidence_type":media.evidence_type,
                "media":serialize(media),"entries":sorted(entries,key=lambda e:(e["frame"]["ordinal"],e["revision"]["id"])),
                "numeric_gt":"COUNT_DENSITY_V1","speed":"UNKNOWN","training_eligibility":"NOT_EVALUATED","actor":value.actor,
                "coverage":{"included":len(entries),"count_valid":sum(e["observation"]["count"]["status"]=="VALID" for e in entries),
                    "count_uncertain":sum(e["observation"]["count"]["status"]=="UNCERTAIN" for e in entries),
                    "count_unknown":sum(e["observation"]["count"]["status"]=="UNKNOWN" for e in entries)}}
            row=GTSnapshot(id=uuid.uuid4().hex,media_id=id,manifest=manifest,manifest_hash=json_hash(manifest))
            db.add(row); db.flush()
            audit(db,value.actor,"CREATE_SNAPSHOT",row.id,{"manifest_hash":row.manifest_hash,"revision_ids":value.revision_ids})
            return serialize(row)

    @router.get("/media/{id}/snapshots")
    def snapshots(id:str):
        with Session() as db:
            require(db,Media,id)
            return [{"id":s.id,"created_at":s.created_at,"manifest_hash":s.manifest_hash,"frames":len(s.manifest["entries"])} for s in db.scalars(select(GTSnapshot).where(GTSnapshot.media_id==id).order_by(GTSnapshot.created_at.desc()))]

    @router.get("/snapshots/{id}")
    def export_snapshot(id:str):
        with Session() as db:
            row=require(db,GTSnapshot,id)
            return JSONResponse(serialize(row),headers={"Content-Disposition":f'attachment; filename="snapshot_{row.id}.json"'})

    app.include_router(router)
    from backend.app.gt.pilot import register as register_pilot
    register_pilot(app,Session,require,serialize,current,last_review,stale,output)
