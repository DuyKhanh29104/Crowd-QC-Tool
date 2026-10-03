from statistics import mean
from backend.app.gt.observations import count_membership
from backend.app.roi.geometry import membership

def iou(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union else 0

def match_boxes(gt,boxes,scores):
    unused=set(range(len(gt)));tp=0;fp=[];matched=[]
    for i in sorted(range(len(boxes)),key=lambda i:-scores[i]):
        match=max(unused,key=lambda j:iou(gt[j]['box'],boxes[i]),default=None)
        if match is not None and iou(gt[match]['box'],boxes[i])>=.5:
            unused.remove(match);tp+=1;matched.append({'gt_index':match,'prediction_index':i,'iou':iou(gt[match]['box'],boxes[i])})
        else:fp.append(i)
    return {'tp':tp,'fp':len(fp),'fn':len(unused),'false_positive_indices':fp,'missed_gt_indices':sorted(unused),'matched_boxes':matched,'expected_box_edits':len(fp)+len(unused)}

def diagnostic_slices(frames):
    result=[]
    for label,lo,hi in [('EMPTY',0,1),('1_TO_5',1,6),('6_TO_15',6,16),('16_PLUS',16,float('inf'))]:
        rows=[f for f in frames if lo<=len(f['gt_boxes'])<hi];support=sum(len(f['gt_boxes']) for f in rows);tp=sum(f['tp'] for f in rows)
        result.append({'dimension':'people_per_image','slice':label,'images':len(rows),'gt_objects':support,'tp':tp,'fn':support-tp,'fp':sum(f['fp'] for f in rows),'recall':tp/support if support else None})
    for label,lo,hi in [('SMALL',0,.01),('MEDIUM',.01,.1),('LARGE',.1,float('inf'))]:
        support=tp=0
        for f in frames:
            for i,g in enumerate(f['gt_boxes']):
                b=g['box'];fraction=(b[2]-b[0])*(b[3]-b[1])/(f['width']*f['height'])
                if lo<=fraction<hi:support+=1;tp+=i not in f['missed_gt_indices']
        result.append({'dimension':'box_area_fraction','slice':label,'gt_objects':support,'tp':tp,'fn':support-tp,'recall':tp/support if support else None})
    result.append({'dimension':'occlusion','slice':'UNKNOWN_NOT_ANNOTATED','gt_objects':sum(len(f['gt_boxes']) for f in frames),'recall':None,'reason':'No independent occlusion label; never infer occlusion from detector error.'})
    return {'version':'helper_diagnostics_v1','size_cutoffs_area_fraction':[.01,.1],'slices':result}

def helper_count(entry,boxes,polygon):
    rect=entry['rect']
    if not polygon or not all(rect[0]<=p[0]<=rect[2] and rect[1]<=p[1]<=rect[3] for p in polygon):return {'status':'NOT_EVALUATED','reason':'CROP_DOES_NOT_COVER_ROI','error':None}
    if entry['review']['verdict']!='APPROVED' or not entry['review']['completeness'].get('count'):return {'status':'NOT_EVALUATED','reason':'COUNT_REVIEW_INCOMPLETE','error':None}
    gt=count_membership(entry['revision']['objects'],polygon,True)
    if gt['status']!='VALID':return {'status':'NOT_EVALUATED','reason':'GT_MEMBERSHIP_UNCERTAIN','error':None}
    predicted=sum(membership([(b[0]+b[2])/2+rect[0],b[3]+rect[1]],polygon,True)=='INSIDE' for b in boxes)
    return {'status':'HELPER_PROXY_EVALUATED','gt':gt['value'],'predicted':predicted,'error':predicted-gt['value'],'anchor_policy':'ESTIMATED_BOX_BOTTOM_NOT_VERIFIED_GROUND_ANCHOR','scope':'HELPER_ONLY_NOT_SUT_OR_SPEED'}

def gate(results,manifest,profile):
    a,b=results;reasons=[]
    if manifest['purpose']=='DEMO_SMOKE':reasons.append('DEMO_NOT_QUALITY_EVIDENCE')
    if len(b['frames'])<profile['min_images']:reasons.append('INSUFFICIENT_VALIDATION_SUPPORT')
    if profile['min_map_gain'] is None or profile['max_slice_recall_drop'] is None or profile['max_latency_seconds'] is None or profile.get('max_vram_bytes') is None:reasons.append('GATES_NOT_ESTABLISHED_FROM_BASELINE')
    delta=b['map50_95']-a['map50_95'];slices=[]
    keys={(f['camera_id'],f['condition']) for f in a['frames']}
    for camera,condition in sorted(keys):
        rows=[[f for f in r['frames'] if (f['camera_id'],f['condition'])==(camera,condition)] for r in results]
        recalls=[sum(f['tp'] for f in fs)/sum(f['tp']+f['fn'] for f in fs) if sum(f['tp']+f['fn'] for f in fs) else None for fs in rows]
        support=len(rows[0]);drop=None if None in recalls else recalls[0]-recalls[1]
        slices.append({'camera':camera,'condition':condition,'images':support,'recall':recalls,'drop':drop})
        if support<profile['min_images_per_slice'] or drop is None:reasons.append('INSUFFICIENT_SLICE_SUPPORT')
        elif profile['max_slice_recall_drop'] is not None and drop>profile['max_slice_recall_drop']:reasons.append('SLICE_REGRESSION')
    latency=mean(f['latency_seconds'] for f in b['frames']) if b['frames'] else None
    if profile['min_map_gain'] is not None and delta<profile['min_map_gain']:reasons.append('MAP_GAIN_GATE_FAILED')
    if profile['max_latency_seconds'] is not None and (latency is None or latency>profile['max_latency_seconds']):reasons.append('LATENCY_GATE_FAILED')
    peak=b.get('peak_vram_bytes')
    if profile.get('max_vram_bytes') is not None and (peak is None or peak>profile['max_vram_bytes']):reasons.append('VRAM_GATE_FAILED_OR_UNMEASURED')
    return {'verdict':'ELIGIBLE' if not reasons else 'KEEP_BASELINE','reasons':sorted(set(reasons)),'map50_95_gain':delta,'candidate_mean_latency':latency,'candidate_peak_vram_bytes':peak,'slices':slices,'human_effort_gain':None}
