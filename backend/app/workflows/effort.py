"""Explicit paired trial records; synthetic timings never claim human savings."""
from statistics import median

def summarize_trials(rows,evidence_type,max_quality_drop):
    if len({r['pair_id'] for r in rows})!=len(rows):raise ValueError('DUPLICATE_PAIR')
    accepted=[];excluded=[]
    for row in rows:
        reason=None
        if not row['independent_quality_checked']:reason='INDEPENDENT_QUALITY_REQUIRED'
        elif row['split']=='locked_test':reason='LOCKED_TEST_NOT_FOR_WORKFLOW_TUNING'
        elif row['manual_active_seconds']<=0 or row['manual_total_seconds']<=0:reason='MANUAL_DENOMINATOR_ZERO'
        if reason:excluded.append({'pair_id':row['pair_id'],'reason':reason})
        else:accepted.append(row)
    def stats(items):
        if not items:return {'pairs':0,'active_reduction_median':None,'total_reduction_median':None,'quality_gate':False}
        good=all(r['assisted_quality']>=r['manual_quality']-max_quality_drop for r in items)
        return {'pairs':len(items),'manual_active_median':median(r['manual_active_seconds'] for r in items),'assisted_active_median':median(r['assisted_active_seconds'] for r in items),
            'active_reduction_median':median((r['manual_active_seconds']-r['assisted_active_seconds'])/r['manual_active_seconds'] for r in items),
            'total_reduction_median':median((r['manual_total_seconds']-r['assisted_total_seconds'])/r['manual_total_seconds'] for r in items),'quality_gate':good,
            'edits':sum(r['edits'] for r in items),'unchanged_objects':sum(r['unchanged_objects'] for r in items),'objects':sum(r['objects'] for r in items),'empty_pairs':sum(r['objects']==0 for r in items),
            'unchanged_object_rate':sum(r['unchanged_objects'] for r in items)/sum(r['objects'] for r in items) if sum(r['objects'] for r in items) else None,
            'orders':{order:sum(r['order']==order for r in items) for order in ('MANUAL_FIRST','AI_FIRST')}}
    summary=stats(accepted);balanced=all(summary.get('orders',{}).get(k,0)>0 for k in ('MANUAL_FIRST','AI_FIRST'))
    return {'schema_version':'paired_effort_v1','evidence_type':evidence_type,'summary':summary,'excluded':excluded,'rows':rows,
        'slices':[{'camera':camera,'condition':condition,**stats([r for r in accepted if (r['camera'],r['condition'])==(camera,condition)])} for camera,condition in sorted({(r['camera'],r['condition']) for r in accepted})],
        'order_balanced':balanced,'quality_drop_tolerance':max_quality_drop,'human_active_reduction':summary['active_reduction_median'] if evidence_type=='REAL' and summary['quality_gate'] and balanced else None,
        'claim_status':'SIMULATION_ONLY' if evidence_type=='DEMO' else 'DESCRIPTIVE_PAIRED_MEASUREMENT' if summary['quality_gate'] and balanced else 'INSUFFICIENT_QUALITY_OR_ORDER_EVIDENCE',
        'target_25_percent_met':None if evidence_type=='DEMO' or not summary['quality_gate'] or not balanced else summary['active_reduction_median']>=.25}
