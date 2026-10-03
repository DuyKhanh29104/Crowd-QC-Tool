"""SUT import and explicit per-metric alignment. Never infer missing fields."""
import csv
import io
import json
import math
from datetime import datetime
from statistics import mean
from fractions import Fraction
from backend.app.gt.rules import classify, level_from_values

METRICS=('count','density','speed','level')

def parse_sut(raw,config,media):
    if config['format']=='JSON':
        rows=json.loads(raw)
        if not isinstance(rows,list):raise ValueError('JSON must be an array of records')
    else:rows=list(csv.DictReader(io.StringIO(raw),delimiter=config.get('delimiter',',')))
    if len(rows)>100000:raise ValueError('Maximum 100000 SUT rows')
    mapping=config['mapping'];units=config['units'];accepted=[];rejected=[];seen=set();previous={}
    factors={'s':1.,'ms':.001,'m/s':1.,'km/h':1/3.6,'people':1.,'people/m2':1.}
    if units.get('timestamp') not in ('s','ms') or units.get('speed','m/s') not in ('m/s','km/h') or units.get('density','people/m2')!='people/m2' or units.get('count','people')!='people':raise ValueError('Unsupported/missing explicit units')
    def number(value):
        if value in (None,''):return None
        if isinstance(value,bool):raise ValueError('Boolean is not numeric')
        result=float(str(value).replace(',','.') if config.get('decimal')==',' else value)
        if not math.isfinite(result):raise ValueError('Non-finite numeric value')
        return result
    def timestamp(value):
        if config['time_origin']=='WALL_CLOCK':
            stamp=datetime.fromisoformat(str(value).replace('Z','+00:00'));origin=datetime.fromisoformat(config['media_zero_iso'].replace('Z','+00:00'))
            if stamp.tzinfo is None or origin.tzinfo is None:raise ValueError('Timezone is required')
            result=(stamp-origin).total_seconds()
        else:
            result=number(value)
            if result is None:raise ValueError('Missing timestamp')
            result=Fraction(str(result))*Fraction(1,1000 if units['timestamp']=='ms' else 1)
            if config['time_origin']=='SOURCE_START':result-=Fraction(str(media.source_offset_seconds))
        return float(Fraction(str(result))+Fraction(str(config['offset_seconds'])))
    for index,row in enumerate(rows):
        try:
            if not isinstance(row,dict):raise ValueError('Row is not an object')
            camera=row.get(mapping.get('camera',''))
            roi=row.get(mapping.get('roi',''))
            if camera!=config['camera_value'] or roi!=config['roi_value']:raise ValueError('CAMERA_ROI_UNMAPPED')
            t=timestamp(row.get(mapping['timestamp']));key=(camera,roi,t)
            if key in seen:raise ValueError('DUPLICATE_TIMESTAMP')
            if t<previous.get((camera,roi),-math.inf):raise ValueError('NON_MONOTONIC_TIMESTAMP')
            values={};validity={}
            for field in METRICS:
                try:
                    n=number(row.get(mapping.get(field,'')))
                    if n is not None:
                        n*=factors[units.get(field,{'count':'people','density':'people/m2','speed':'m/s'}.get(field,'s'))]
                        if n<0 or field in ('count','level') and n!=int(n) or field=='level' and n not in (1,2,3):raise ValueError('OUT_OF_DOMAIN')
                    values[field]=n;validity[field]='VALID' if n is not None else 'SUT_FIELD_MISSING'
                except (ValueError,TypeError,KeyError):values[field]=None;validity[field]='SUT_FIELD_INVALID'
            start=timestamp(row[mapping['window_start']]) if mapping.get('window_start') and row.get(mapping['window_start']) not in (None,'') else None
            if start is not None and start>=t:raise ValueError('INVALID_WINDOW')
            seen.add(key);previous[(camera,roi)]=t
            accepted.append({'row_index':index,'time':t,'window_start':start,'values':values,'validity':validity,'raw':row})
        except (ValueError,KeyError,TypeError) as e:rejected.append({'row_index':index,'reason':str(e),'raw':row})
    return {'accepted':accepted,'rejected':rejected,'total_rows':len(rows),'config':config,'raw':raw,'status':'DEMO' if config['evidence_type']=='DEMO' else 'IMPORTED_CONTRACT_USER_DECLARED'}

def numeric(errors):
    if not errors:return {'mae':None,'rmse':None,'bias':None,'p95_absolute_error':None,'support':0,'status':'NO_DATA'}
    absolute=sorted(abs(x) for x in errors);index=.95*(len(absolute)-1);i=int(index)
    p95=absolute[i]+(absolute[min(i+1,len(absolute)-1)]-absolute[i])*(index-i)
    return {'mae':mean(absolute),'rmse':math.sqrt(mean(x*x for x in errors)),'bias':mean(errors),'p95_absolute_error':p95,'support':len(errors),'status':'EVALUATED'}

def confusion(pairs,classes):
    matrix=[[sum(a==x and b==y for a,b in pairs) for y in classes] for x in classes];per={}
    for i,c in enumerate(classes):
        tp=matrix[i][i];support=sum(matrix[i]);predicted=sum(row[i] for row in matrix)
        p=tp/predicted if predicted else None;r=tp/support if support else None
        f1=2*tp/(support+predicted) if support else None
        per[str(c)]={'precision':p,'recall':r,'f1':f1,'support':support,'predicted':predicted}
    f=[v['f1'] for v in per.values() if v['f1'] is not None]
    return {'classes':classes,'matrix':matrix,'per_class':per,'macro_f1_supported_classes':mean(f) if f else None,'support':len(pairs),'missing_gt_classes':[c for c in classes if per[str(c)]['support']==0]}

def evaluate(gt_entries,sut,config):
    unused=set(range(len(gt_entries)));pairs=[];exclusions=[]
    exact={}
    by_time={e['frame']['relative_seconds']:i for i,e in enumerate(gt_entries)}
    for row in sut['accepted']:
        index=by_time.get(row['time'])
        if index is not None and index in unused:exact[row['row_index']]=index;unused.remove(index)
    errors={k:[] for k in METRICS if k!='level'};classes={k:[] for k in ('density','speed','level')}
    for row in sut['accepted']:
        index=exact.get(row['row_index'])
        if index is None:index=min(unused,key=lambda i:(abs(gt_entries[i]['frame']['relative_seconds']-row['time']),i),default=None)
        if index is None or abs(gt_entries[index]['frame']['relative_seconds']-row['time'])>config['tolerance_seconds']:
            exclusions.append({'sut_row':row['row_index'],'reason':'TIME_UNMATCHED','metrics':list(METRICS)});continue
        unused.discard(index);gt=gt_entries[index];obs=gt['observation'];rules=gt['revision']['rules'];reasons={};deltas={}
        for field in METRICS:
            semantics=sut['config']['semantics'].get(field,'UNKNOWN')
            reason=None
            if row['validity'][field]!='VALID':reason=row['validity'][field]
            elif obs[field]['status']!='VALID':reason='GT_'+obs[field]['status']
            elif not obs.get('gt_eligible',False):reason='GT_NOT_REVIEWED'
            elif field=='speed':
                window=obs.get('speed_window')
                if semantics!='PAST_WINDOW' or not window or row['window_start'] is None or abs(row['window_start']-window[0])>1e-6 or abs(row['time']-window[1])>1e-6:reason='WINDOW_SEMANTICS_MISMATCH'
            elif semantics!='INSTANT':reason='WINDOW_SEMANTICS_MISMATCH'
            if reason:reasons[field]=reason;continue
            actual=obs[field]['value'];pred=row['values'][field]
            if field!='level':errors[field].append(pred-actual);deltas[field]=pred-actual
            if field in ('density','speed'):classes[field].append((classify(actual,field,rules)['class'],classify(pred,field,rules)['class']))
            if field=='level':classes[field].append((actual,pred))
        consistency='INCONCLUSIVE'
        if sut['config'].get('raw_precision_confirmed') and sut['config'].get('rules_hash')==config['rules_hash'] and all(row['validity'][k]=='VALID' for k in ('density','speed','level')) and all(sut['config']['semantics'].get(k)=='INSTANT' for k in ('density','speed','level')):
            consistency='CONSISTENT' if level_from_values(row['values']['density'],row['values']['speed'],rules)['value']==row['values']['level'] else 'INCONSISTENT'
        pairs.append({'gt_index':index,'gt_revision_id':gt['revision']['id'],'frame_id':gt['frame']['id'],'time':gt['frame']['relative_seconds'],
            'sut_row':row['row_index'],'delta_time':row['time']-gt['frame']['relative_seconds'],'gt':{k:obs[k]['value'] for k in METRICS},'sut':row['values'],'errors':deltas,'excluded_metrics':reasons,'sut_rule_consistency':consistency})
    for index in sorted(unused):exclusions.append({'gt_index':index,'frame_id':gt_entries[index]['frame']['id'],'reason':'GT_WITHOUT_SUT','metrics':list(METRICS)})
    metrics={k:{**numeric(v),'candidate_samples':len(sut['accepted']),'excluded_samples':len(sut['accepted'])-len(v)} for k,v in errors.items()}
    metrics.update({k+'_confusion':confusion(v,('LOW','MEDIUM','HIGH') if k=='density' else ('SLOW','NORMAL','FAST') if k=='speed' else (1,2,3)) for k,v in classes.items()})
    levels=classes['level']
    timeline=sorted(pairs+[{'time':gt_entries[i]['frame']['relative_seconds'],'frame_id':gt_entries[i]['frame']['id'],'gt':{k:gt_entries[i]['observation'][k]['value'] for k in METRICS},'sut':{k:None for k in METRICS}} for i in unused],key=lambda r:r['time'])
    return {'schema_version':'sut_evaluation_v1','evaluator_version':'explicit_alignment_v2_exact_first','metrics':metrics,'pairs':pairs,'timeline':timeline,'unmatched':exclusions,
        'rejected_sut_rows':sut['rejected'],'coverage':{'gt_rows':len(gt_entries),'sut_accepted':len(sut['accepted']),'sut_rejected':len(sut['rejected']),'matched':len(pairs)},
        'level_errors':{'under':sum(b<a for a,b in levels),'over':sum(b>a for a,b in levels),'one_three':sum({a,b}=={1,3} for a,b in levels)},
        'event_timing':'NOT_EVALUATED_SPARSE_GT','diagnosis':'Output comparison only; no detector/tracker causal attribution.'}
