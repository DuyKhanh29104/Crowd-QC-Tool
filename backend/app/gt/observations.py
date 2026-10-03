"""Pure count/density/level derivation. Exact rational comparisons; no speed fabrication."""
from fractions import Fraction
import hashlib
import math
from pathlib import Path
from backend.app.roi.geometry import membership
from backend.app.jobs.profiles import json_hash
from backend.app.gt.rules import measurement

ENGINE_VERSION="count_density_v1"
ENGINE_HASH=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def metric(value,status,reason=None,lower=None,upper=None):
    return {"value":value,"status":status,"reason":reason,"lower":lower,"upper":upper}


def count_membership(objects,polygon,population_complete):
    states={o["id"]:membership(o.get("anchor"),polygon,o.get("anchor_verified",False)) for o in objects}
    inside=sum(s=="INSIDE" for s in states.values())
    uncertain=sum(s=="UNCERTAIN" for s in states.values())
    upper=inside+uncertain if population_complete else None
    status="UNKNOWN" if upper is None else "UNCERTAIN" if uncertain else "VALID"
    result=metric(inside if status=="VALID" else None,status,
        "POPULATION_INCOMPLETE" if upper is None else "MEMBERSHIP_UNCERTAIN" if uncertain else None,inside,upper)
    return {**result,"confirmed_inside":inside,"confirmed_outside":sum(s=="OUTSIDE" for s in states.values()),
        "uncertain":uncertain,"object_membership":states}


def possible_classes(lower,upper,profile):
    limits=[Fraction(str(v)) for v in profile["thresholds"]]
    starts=[Fraction(0),*limits]
    ends=[*limits,None]
    return [name for name,start,end in zip(profile["classes"],starts,ends)
        if (upper is None or upper>=start) and (end is None or lower<end)]


def density_interval(count,area,config):
    check=measurement(area,positive=True)
    if check["status"]!="VALID": return {**metric(None,check["status"],check["reason"]),"class":None,"possible_classes":[]}
    area=Fraction(str(area))
    lower=Fraction(count["lower"])/area
    upper=None if count["upper"] is None else Fraction(count["upper"])/area
    classes=possible_classes(lower,upper,config["density"])
    try:
        lo,hi=float(lower),float(upper) if upper is not None else None
        if not math.isfinite(lo) or hi is not None and not math.isfinite(hi):raise OverflowError()
    except OverflowError:
        return {**metric(None,"INVALID","NUMERIC_OVERFLOW"),"class":None,"possible_classes":[]}
    result=metric(lo if count["status"]=="VALID" else None,count["status"],count["reason"],lo,hi)
    return {**result,"class":classes[0] if len(classes)==1 else None,"possible_classes":classes,
        "exact_lower":f"{lower.numerator}/{lower.denominator}","exact_upper":None if upper is None else f"{upper.numerator}/{upper.denominator}","unit":"people_per_m2"}


def classify_speed(value,config):
    check=measurement(value)
    if check["status"]!="VALID": return {**metric(None,check["status"],check["reason"]),"class":None}
    number=Fraction(str(value))
    return {**metric(float(number),"VALID"),"class":possible_classes(number,number,config["speed"])[0]}


def possible_levels(density,speed,config):
    if density["status"]=="INVALID" or speed["status"]=="INVALID": return []
    speeds=[speed["class"]] if speed["class"] else config["speed"]["classes"]
    return sorted({config["level_matrix"][d][s] for d in density["possible_classes"] for s in speeds})


def derive(objects,polygon,area,config,population_complete=False,speed=None):
    count=count_membership(objects,polygon,population_complete)
    density=density_interval(count,area,config)
    velocity=classify_speed(speed,config)
    if count["value"]==0 and velocity["status"]!="INVALID":
        velocity={**metric(None,"UNKNOWN","EMPTY_POPULATION"),"class":None}
    options=possible_levels(density,velocity,config)
    invalid=next((m for m in (density,velocity) if m["status"]=="INVALID"),None)
    level=metric(options[0] if len(options)==1 else None,"INVALID" if invalid else "VALID" if len(options)==1 else "UNKNOWN",
        invalid["reason"] if invalid else None if len(options)==1 else "SPEED_MISSING_OR_DENSITY_UNCERTAIN")
    return {"count":count,"density":density,"speed":velocity,"level":{**level,"possible_levels":options},"engine_version":ENGINE_VERSION}


def observation(revision,roi,review,media,stale=False):
    complete=bool(review and review["completeness"].get("count"))
    values=derive(revision["objects"],roi["polygon_source_px"],roi["area_m2"],revision["rules"],complete)
    approved=bool(review and review["verdict"]=="APPROVED")
    dependencies={"revision_id":revision["id"],"review_id":review["id"] if review else None,
        "revision_dependencies_hash":revision["dependencies_hash"],"objects":revision["objects"],
        "completeness":review["completeness"] if review else None,"roi":roi,"rules":revision["rules"],
        "source_sha256":media["sha256"],"engine_version":ENGINE_VERSION,"engine_hash":ENGINE_HASH}
    return {**values,"revision_id":revision["id"],"review_id":review["id"] if review else None,
        "dependencies_hash":json_hash(dependencies),"engine_hash":ENGINE_HASH,"evidence_type":media["evidence_type"],
        "release_state":"STALE" if stale else "REVIEWED" if approved else "DRAFT",
        "gt_eligible":approved and not stale,"stale":stale,
        "exclusion_reason":"STALE_DEPENDENCIES" if stale else "NOT_REVIEWED" if not approved else None}
