"""Person-only eligibility and deterministic source-pixel transforms."""
import json
from pathlib import Path
from backend.app.jobs.profiles import json_hash

ROOT=Path(__file__).resolve().parents[3]
GUIDE=json.loads((ROOT/'configs/person_annotation_guide_v1.json').read_text('utf-8'))
GUIDE_HASH=json_hash(GUIDE)


def scope_rect(scope,crop,width,height):
    if scope=='FULL_FRAME':
        if crop is not None:raise ValueError('FULL_FRAME must not have crop coordinates')
        return [0,0,width,height]
    if crop is None or len(crop)!=4:raise ValueError('CROP requires integer x1,y1,x2,y2')
    x1,y1,x2,y2=crop
    if not(0<=x1<x2<=width and 0<=y1<y2<=height):raise ValueError('Crop must be inside the source image')
    if x2-x1<10 or y2-y1<10:raise ValueError('YOLO images require width and height of at least 10 pixels')
    return list(crop)


def targets(objects,rect):
    x,y,right,bottom=rect
    result=[];reasons=[]
    for obj in objects:
        box=obj.get('box')
        if box is None:
            # A point cannot prove that the person's extent is outside a crop.
            reasons.append('MARKER_ONLY_EXTENT_UNKNOWN');continue
        a,b,c,d=box
        clipped=[max(a,x)-x,max(b,y)-y,min(c,right)-x,min(d,bottom)-y]
        if clipped[0]>=clipped[2] or clipped[1]>=clipped[3]:continue
        if clipped[2]-clipped[0]<GUIDE['minimum_box_px'] or clipped[3]-clipped[1]<GUIDE['minimum_box_px']:
            reasons.append('BOX_TOO_SMALL');continue
        result.append({'object_id':obj['id'],'class_id':GUIDE['class_mapping']['person'],'box':clipped,'truncated':a<x or b<y or c>right or d>bottom,'origin':obj.get('origin'),'source_proposal_id':obj.get('source_proposal_id')})
    if len({tuple(o['box']) for o in result})!=len(result):reasons.append('DUPLICATE_TARGET_BOXES')
    return result,sorted(set(reasons))


def eligibility(payload,objects,rect,review_scope,box_complete):
    boxes,reasons=targets(objects,rect)
    if rect[2]-rect[0]<10 or rect[3]-rect[1]<10:reasons.append('IMAGE_TOO_SMALL_FOR_YOLO')
    if payload['decision']=='EXCLUDE':reasons.append('USER_EXCLUDED')
    if payload['guide_hash']!=GUIDE_HASH:reasons.append('GUIDE_CHANGED')
    for field in ('viewed','all_people_checked','geometry_checked','class_checked','convention_checked','no_unresolved'):
        if not payload.get(field):reasons.append(field.upper()+'_REQUIRED')
    if payload['scope']=='FULL_FRAME' and (review_scope!='FULL_FRAME' or not box_complete):reasons.append('FULL_FRAME_BOX_REVIEW_REQUIRED')
    if payload['scope']=='CROP' and not payload.get('context_and_truncation_checked'):reasons.append('CROP_CONTEXT_REVIEW_REQUIRED')
    if not boxes and not payload.get('negative_confirmed'):reasons.append('EMPTY_NOT_CONFIRMED')
    if boxes and payload.get('negative_confirmed'):reasons.append('NEGATIVE_HAS_BOXES')
    return boxes,sorted(set(reasons))


def yolo_labels(boxes,width,height):
    lines=[]
    for obj in boxes:
        a,b,c,d=obj['box']
        if obj['class_id']!=0 or not(0<=a<c<=width and 0<=b<d<=height):raise ValueError('Invalid person target geometry/class')
        values=((a+c)/2/width,(b+d)/2/height,(c-a)/width,(d-b)/height)
        lines.append('0 '+' '.join(format(v,'.12g') for v in values))
    return ('\n'.join(lines)+'\n' if lines else '').encode('ascii')
