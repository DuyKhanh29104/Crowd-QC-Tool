"""Review hints only. Never discard overlapping boxes or emit GT measurements."""
import math
from backend.app.roi.geometry import membership

POLICY = {"version":"review_flags_v1","low_confidence":0.5,"boundary_px":8,"overlap_iou":0.5,"count_jump":3,"threshold_margin_people":1}


def distance(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
    return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)


def flags(objects,roi,rules,previous_count=None):
    found=set()
    polygon=roi.polygon_source_px
    for obj in objects:
        if obj.get("score") is not None and obj["score"]<POLICY["low_confidence"]: found.add("LOW_CONFIDENCE")
        p=obj.get("anchor")
        if p and min(distance(p,a,b) for a,b in zip(polygon,polygon[1:]+polygon[:1]))<=POLICY["boundary_px"]: found.add("NEAR_ROI_BOUNDARY")
        if not obj.get("anchor_verified"): found.add("ANCHOR_UNVERIFIED")
    boxes=[o["box"] for o in objects if o.get("box")]
    for i,a in enumerate(boxes):
        for b in boxes[i+1:]:
            inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
            union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
            if inter/union>=POLICY["overlap_iou"]: found.add("POSSIBLE_DUPLICATE")
    if previous_count is not None and abs(len(objects)-previous_count)>=POLICY["count_jump"]: found.add("COUNT_JUMP")
    inside=sum(membership(o.get("anchor"),polygon,True)=="INSIDE" for o in objects)
    if any(abs(inside-roi.area_m2*t)<=POLICY["threshold_margin_people"] for t in rules["density"]["thresholds"]): found.add("NEAR_DENSITY_THRESHOLD")
    if not objects: found.add("EMPTY_PREDICTION")
    return sorted(found)
