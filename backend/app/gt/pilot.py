"""Pilot coverage and review effort. DEMO and simulated work never become human evidence."""
import math
import uuid
from typing import Literal
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from backend.app.db.models import Annotation, AnnotationRevision, ProposalRun, ProposalFrame, Frame, Media, ROIVersion, GTSnapshot, ReviewActivity, IndependentCheck, Adjudication, PilotReport, AuditEvent
from backend.app.jobs.profiles import json_hash
from backend.app.media.service import verify_source
from backend.app.errors import DomainError


class Input(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False,str_strip_whitespace=True)


class Activity(Input):
    id: str=Field(min_length=1,max_length=80)
    session_id: str=Field(min_length=1,max_length=80)
    reviewer: str=Field(min_length=1,max_length=120)
    seconds: float=Field(ge=0,le=5)
    interactions: int=Field(ge=1,le=10000)
    source: Literal["UI_INTERACTION","SIMULATED"]="UI_INTERACTION"


class Check(Input):
    expected_edit_version: int=Field(ge=1)
    reviewer: str=Field(min_length=1,max_length=120)
    labels_hidden: Literal[True]
    count_inside: int|None=Field(default=None,ge=0,strict=True)
    checked_boxes: bool=False
    notes: str=Field(min_length=1,max_length=4000)


class Resolve(Input):
    expected_edit_version: int=Field(ge=1)
    reviewer: str=Field(min_length=1,max_length=120)
    resolution: Literal["PRIMARY_CONFIRMED","CORRECTED","UNRESOLVED"]
    notes: str=Field(min_length=1,max_length=4000)


class Report(Input):
    roi_version_id: str
    actor: str=Field(min_length=1,max_length=120)


def deltas(before,after):
    a={o["id"]:o for o in before};b={o["id"]:o for o in after}
    return {"added":len(b.keys()-a.keys()),"removed":len(a.keys()-b.keys()),
        "geometry_changed":sum(any(a[id].get(k)!=b[id].get(k) for k in ("box","marker","anchor")) for id in a.keys()&b.keys()),
        "anchor_verifications":sum(not a[id].get("anchor_verified") and b[id].get("anchor_verified") for id in a.keys()&b.keys())}


def register(app,Session,require,serialize,current,last_review,stale,output):
    router=APIRouter(prefix="/api/pilot")

    def eligible(db,ann,expected):
        if ann.edit_version!=expected:raise DomainError("EDIT_CONFLICT","Revision đã thay đổi.",409)
        doc=output(db,ann)
        if not doc["observation"]["gt_eligible"]:raise DomainError("NOT_REVIEWED_OR_STALE","Cần review revision hiện tại, không stale.",409)
        verify_source(db.get(Media,doc["frame"]["media_id"]),full=True)
        return doc

    @router.post("/annotations/{id}/activity",status_code=201)
    def activity(id:str,value:Activity):
        with Session.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            ann=require(db,Annotation,id);frame=db.get(Frame,ann.frame_id);media=db.get(Media,frame.media_id)
            if value.source=="SIMULATED" and media.evidence_type!="DEMO":raise DomainError("SIMULATION_REQUIRES_DEMO","Không ghi thời gian mô phỏng vào video thực.",422)
            previous=db.get(ReviewActivity,value.id)
            if previous:
                expected={**value.model_dump(),"annotation_id":id}
                if any(getattr(previous,k)!=v for k,v in expected.items()):raise DomainError("ACTIVITY_CONFLICT","ID activity đã dùng cho dữ liệu khác.",409)
                return serialize(previous)
            row=ReviewActivity(annotation_id=id,**value.model_dump())
            db.add(row);db.flush();return serialize(row)

    @router.post("/annotations/{id}/independent-check",status_code=201)
    def independent(id:str,value:Check):
        with Session.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            ann=require(db,Annotation,id);doc=eligible(db,ann,value.expected_edit_version)
            primary=doc["observation"]["count"]["value"]
            verdict="UNRESOLVED" if primary is None or value.count_inside is None else "AGREES" if primary==value.count_inside else "DISAGREES"
            row=IndependentCheck(id=uuid.uuid4().hex,revision_id=doc["revision"]["id"],reviewer=value.reviewer,
                payload={"method":"HIDDEN_LABEL_SECOND_PASS" if value.reviewer==doc["review"]["reviewer"] else "SECOND_REVIEWER_HIDDEN_LABELS",
                    "labels_hidden":True,"count_inside":value.count_inside,"primary_count":primary,"verdict":verdict,
                    "checked_boxes":value.checked_boxes,"notes":value.notes,"evidence_type":doc["observation"]["evidence_type"]})
            db.add(row);db.flush()
            db.add(AuditEvent(id=uuid.uuid4().hex,actor=value.reviewer,action="INDEPENDENT_CHECK",entity_id=id,payload={"check_id":row.id,"verdict":verdict}))
            return serialize(row)

    @router.get("/annotations/{id}/checks")
    def checks(id:str):
        with Session() as db:
            require(db,Annotation,id)
            query=select(IndependentCheck).join(AnnotationRevision,IndependentCheck.revision_id==AnnotationRevision.id).where(AnnotationRevision.annotation_id==id).order_by(IndependentCheck.created_at.desc())
            return [{**serialize(c),"adjudications":[serialize(a) for a in db.scalars(select(Adjudication).where(Adjudication.check_id==c.id).order_by(Adjudication.created_at.desc()))]} for c in db.scalars(query)]

    @router.post("/checks/{id}/adjudications",status_code=201)
    def resolve(id:str,value:Resolve):
        with Session.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            check=require(db,IndependentCheck,id);rev=db.get(AnnotationRevision,check.revision_id);ann=db.get(Annotation,rev.annotation_id)
            doc=eligible(db,ann,value.expected_edit_version)
            if value.resolution!="UNRESOLVED" and doc["observation"]["count"]["status"]!="VALID":raise DomainError("COUNT_NOT_EXACT","Cần count chính xác để phân xử xong.",409)
            if value.resolution=="PRIMARY_CONFIRMED" and doc["revision"]["id"]!=check.revision_id:raise DomainError("REVISION_CHANGED","Nhãn đã sửa; chọn CORRECTED và ghi căn cứ.",409)
            if value.resolution=="CORRECTED" and doc["revision"]["id"]==check.revision_id:raise DomainError("CORRECTION_REQUIRED","Sửa và review revision mới trước khi ghi CORRECTED.",409)
            row=Adjudication(id=uuid.uuid4().hex,check_id=id,revision_id=doc["revision"]["id"],reviewer=value.reviewer,resolution=value.resolution,notes=value.notes)
            db.add(row);db.flush()
            db.add(AuditEvent(id=uuid.uuid4().hex,actor=value.reviewer,action="ADJUDICATION",entity_id=ann.id,payload={"check_id":id,"adjudication_id":row.id}))
            return serialize(row)

    @router.post("/runs/{id}/reports",status_code=201)
    def report(id:str,value:Report):
        from backend.app.annotations.api import seed_objects
        with Session.begin() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            run=require(db,ProposalRun,id);media=db.get(Media,run.media_id);roi=require(db,ROIVersion,value.roi_version_id)
            if roi.media_id!=media.id:raise DomainError("ROI_SCOPE_MISMATCH","ROI khác video.",422)
            verify_source(media,full=True)
            all_snapshots=list(db.scalars(select(GTSnapshot).where(GTSnapshot.media_id==media.id)))
            items=[];ui_seconds=simulated_seconds=0.;ui_chunks=0;independent_frames=unresolved=0
            for proposal in db.scalars(select(ProposalFrame).where(ProposalFrame.run_id==id).order_by(ProposalFrame.ordinal)):
                frame=db.get(Frame,proposal.frame_id)
                ann=db.scalar(select(Annotation).where(Annotation.frame_id==frame.id,Annotation.roi_id==roi.roi_id))
                in_range=roi.valid_from_seconds<=frame.relative_seconds and (roi.valid_to_seconds is None or frame.relative_seconds<=roi.valid_to_seconds)
                item={"frame_id":frame.id,"ordinal":frame.ordinal,"pts":frame.pts,"time_base":[frame.time_base_num,frame.time_base_den],
                    "relative_seconds":frame.relative_seconds,"proposal_count":len(proposal.payload["objects"]),"in_range":in_range,
                    "state":"PROPOSAL","exclusion_reason":"NOT_REVIEWED" if in_range else "OUTSIDE_ROI_RANGE","observation":None,
                    "revision_id":None,"review_id":None,"checks":[],"released_snapshot_ids":[],"final_delta":None,"saved_edit_events":None,"box_complete":False,"independent_current":False}
                if ann:
                    doc=output(db,ann);rev=doc["revision"];review=doc["review"];obs=doc["observation"]
                    # The requested report ROI must be exactly the dependency used by the revision.
                    wrong_roi=rev["roi_version_id"]!=roi.id
                    item.update(revision_id=rev["id"],review_id=review["id"] if review else None,state="STALE" if doc["stale"] or wrong_roi else review["verdict"] if review else "DRAFT",observation=obs)
                    item["exclusion_reason"]="OUTSIDE_ROI_RANGE" if not in_range else "STALE_DEPENDENCIES" if doc["stale"] or wrong_roi else "NOT_REVIEWED" if not obs["gt_eligible"] else obs["count"]["reason"]
                    item["box_complete"]=bool(review and review["completeness"].get("person_boxes") and review["scope"]["kind"]=="FULL_FRAME" and obs["gt_eligible"] and not wrong_roi and in_range)
                    baseline=db.scalar(select(ProposalFrame).where(ProposalFrame.run_id==rev["proposal_run_id"],ProposalFrame.frame_id==frame.id))
                    item["annotation_source_run_id"]=rev["proposal_run_id"]
                    item["final_delta"]=deltas(seed_objects(baseline.payload),rev["objects"])
                    history=list(db.scalars(select(AnnotationRevision).where(AnnotationRevision.annotation_id==ann.id).order_by(AnnotationRevision.version)))
                    event_counts={k:0 for k in ("added","removed","geometry_changed","anchor_verifications")}
                    for a,b in zip(history,history[1:]):
                        for k,n in deltas(a.objects,b.objects).items():event_counts[k]+=n
                    item["saved_edit_events"]=event_counts
                    item["checks"]=checks(ann.id)
                    good=False;pending=0
                    for c in item["checks"]:
                        last=c["adjudications"][0] if c["adjudications"] else None
                        resolved=bool(last and last["revision_id"]==rev["id"] and last["resolution"]!="UNRESOLVED")
                        agrees=c["revision_id"]==rev["id"] and c["payload"]["verdict"]=="AGREES"
                        good=good or resolved or agrees
                        if c["payload"]["verdict"]!="AGREES" and not resolved:pending+=1
                    good=good and obs["gt_eligible"] and not wrong_roi and in_range
                    item["independent_current"]=good;independent_frames+=int(good);unresolved+=pending
                    for activity in db.scalars(select(ReviewActivity).where(ReviewActivity.annotation_id==ann.id)):
                        if activity.source=="SIMULATED":simulated_seconds+=activity.seconds
                        else:ui_seconds+=activity.seconds;ui_chunks+=1
                    for snap in all_snapshots:
                        if any(e["revision"]["id"]==rev["id"] and review and e["review"]["id"]==review["id"] and e.get("observation") for e in snap.manifest["entries"]):item["released_snapshot_ids"].append(snap.id)
                items.append(item)
            approved=[f for f in items if f["state"]=="APPROVED" and f["in_range"]]
            exact=[f for f in items if f["exclusion_reason"] is None and f["observation"] and f["observation"]["count"]["status"]=="VALID"]
            untouched=sum(all(f["final_delta"][k]==0 for k in ("added","removed","geometry_changed")) for f in approved)
            target=max(1,math.ceil(len(items)*.1)) if items else 0
            payload={"schema_version":"pilot_report_v1","evidence_type":media.evidence_type,"run_id":id,"roi_version":serialize(roi),"source_sha256":media.sha256,"actor":value.actor,
                "summary":{"sampled_frames":len(items),"approved_frames":len(approved),"count_valid_frames":len(exact),
                    "count_uncertain_frames":sum(f["observation"]["count"]["status"]=="UNCERTAIN" for f in approved),
                    "excluded_or_incomplete_frames":len(items)-len(exact),"count_valid_coverage":len(exact)/len(items) if items else None,
                    "no_geometry_edits_approved_frames":untouched,"no_geometry_edits_rate":untouched/len(approved) if approved else None,
                    "full_frame_box_complete_frames":sum(f["box_complete"] for f in items),"independently_checked_current_frames":independent_frames,
                    "independent_target_frames":target,"unresolved_disagreements":unresolved,"independent_gate_passed":bool(items) and independent_frames>=target and unresolved==0},
                "effort":{"recorded_ui_chunks":ui_chunks,"human_active_seconds":ui_seconds if media.evidence_type=="REAL" and ui_chunks else None,"demo_ui_interaction_seconds":ui_seconds if media.evidence_type=="DEMO" and ui_chunks else None,
                    "simulated_seconds":simulated_seconds,"policy":"Sum of gaps <=5s between trusted foreground UI inputs; reading-only and idle gaps excluded. Client-reported estimate, not total wall time."},
                "training_eligibility":"NOT_EVALUATED_NO_DATASET_RELEASE","real_pilot_status":"AWAITING_REAL_MEDIA_AND_HUMAN_REVIEW" if media.evidence_type=="DEMO" else "REVIEW_EVIDENCE_ONLY_NOT_MODEL_ACCURACY",
                "frames":items,"snapshot_ids":sorted({s for f in items for s in f["released_snapshot_ids"]})}
            row=PilotReport(id=uuid.uuid4().hex,run_id=id,payload=payload,report_hash=json_hash(payload))
            db.add(row);db.flush();return serialize(row)

    @router.get("/runs/{id}/reports")
    def reports(id:str):
        with Session() as db:
            require(db,ProposalRun,id)
            return [{"id":r.id,"created_at":r.created_at,"report_hash":r.report_hash,"summary":r.payload["summary"]} for r in db.scalars(select(PilotReport).where(PilotReport.run_id==id).order_by(PilotReport.created_at.desc()))]

    @router.get("/reports/{id}")
    def download(id:str):
        with Session() as db:return JSONResponse(serialize(require(db,PilotReport,id)),headers={"Content-Disposition":f'attachment; filename="pilot_{id}.json"'})

    app.include_router(router)
