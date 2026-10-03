"""Pool raw errors and confusion counts; never average per-camera metrics."""
from collections import defaultdict
from .benchmark import numeric

def combine_confusions(values):
    labels=values[0]['classes'];matrix=[[0]*len(labels) for _ in labels]
    for value in values:
        if value['classes']!=labels:raise ValueError('CLASS_SCHEMA_MISMATCH')
        for i,row in enumerate(value['matrix']):
            for j,n in enumerate(row):matrix[i][j]+=n
    per={}
    for i,label in enumerate(labels):
        tp=matrix[i][i];support=sum(matrix[i]);predicted=sum(r[i] for r in matrix)
        per[str(label)]={'precision':tp/predicted if predicted else None,'recall':tp/support if support else None,'f1':2*tp/(support+predicted) if support else None,'support':support,'predicted':predicted}
    f=[v['f1'] for v in per.values() if v['f1'] is not None]
    return {'classes':labels,'matrix':matrix,'per_class':per,'support':sum(map(sum,matrix)),'macro_f1_supported_classes':sum(f)/len(f) if f else None,'missing_gt_classes':[k for k in labels if not per[str(k)]['support']]}

def summarize(records):
    coverage={k:sum(r['payload']['coverage'][k] for r in records) for k in ('gt_rows','sut_accepted','sut_rejected','matched')}
    metrics={}
    for field in ('count','density','speed'):
        errors=[p['errors'][field] for r in records for p in r['payload']['pairs'] if field in p['errors']]
        metrics[field]={**numeric(errors),'candidate_samples':coverage['sut_accepted'],'excluded_samples':coverage['sut_accepted']-len(errors)}
    for field in ('density_confusion','speed_confusion','level_confusion'):
        metrics[field]=combine_confusions([r['payload']['metrics'][field] for r in records])
    return {'metrics':metrics,'coverage':coverage,'level_errors':{k:sum(r['payload']['level_errors'][k] for r in records) for k in ('under','over','one_three')}}

def aggregate(records):
    if not records or len({r['id'] for r in records})!=len(records):raise ValueError('SELECT_DISTINCT_REPORTS')
    # Compare dictionaries rather than serialization order.
    if any(r['payload']['aggregation_protocol']!=records[0]['payload']['aggregation_protocol'] for r in records):raise ValueError('INCOMPATIBLE_EVALUATION_PROTOCOL')
    evidence={r['payload']['evidence_type'] for r in records}
    if len(evidence)>1:raise ValueError('DEMO_AND_REAL_CANNOT_BE_POOLED')
    seen=set();groups=defaultdict(list)
    for r in records:
        p=r['payload'];scope=p['scope']
        for key in p['sample_keys']:
            key=tuple(key)
            if key in seen:raise ValueError('OVERLAPPING_SOURCE_SAMPLES')
            seen.add(key)
        groups[(scope['camera_id'],scope['roi_version_id'],scope['condition'])].append(r)
    slices=[{'scope':dict(zip(('camera_id','roi_version_id','condition'),key)),**summarize(values)} for key,values in sorted(groups.items())]
    return {**summarize(records),'slices':slices,'evidence_type':next(iter(evidence)),'schema_version':'pooled_evaluation_v1','aggregation_protocol':records[0]['payload']['aggregation_protocol'],
        'members':[{'id':r['id'],'hash':r['content_hash']} for r in records],
        'pairs':[{**pair,'evaluation_id':r['id'],'scope':r['payload']['scope']} for r in records for pair in r['payload']['pairs']],
        'unmatched':[{**row,'evaluation_id':r['id']} for r in records for row in r['payload']['unmatched']],
        'rejected_sut_rows':[{**row,'evaluation_id':r['id']} for r in records for row in r['payload']['rejected_sut_rows']],
        'method':'RAW_ERROR_SAMPLES_AND_SUMMED_CONFUSION_COUNTS','event_timing':'NOT_EVALUATED_SPARSE_GT'}
