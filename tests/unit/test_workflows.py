import math
from types import SimpleNamespace
import pytest
from backend.app.workflows.benchmark import numeric,parse_sut,evaluate,confusion
from backend.app.workflows.motion import fit_calibration,individual_speed,population_speed
from backend.app.workflows.helper import match_boxes,gate

def config():return {'format':'JSON','mapping':{k:k for k in ('timestamp','camera','roi','count','density','speed','level','window_start')},'units':{'timestamp':'s','speed':'m/s'},'time_origin':'MEDIA_START','offset_seconds':0,'camera_value':'c','roi_value':'r','semantics':{k:'INSTANT' for k in ('count','density','speed','level')},'evidence_type':'DEMO'}

def calibration():
    points=[{'pixel':p,'world':[p[0]/100,p[1]/100],'role':'FIT','region':'MID'} for p in ([0,0],[640,0],[640,360],[0,360])]
    points += [{'pixel':p,'world':[p[0]/100,p[1]/100],'role':'HOLDOUT','region':r} for p,r in (([100,300],'NEAR'),([300,180],'MID'),([500,50],'FAR'))]
    return points,[{'a':[100,100],'b':[200,100],'meters':1,'region':'NEAR'}]

def test_metrics_hand_calculated_and_missing_classes():
    m=numeric([1,-2,3]);assert m['mae']==2 and m['bias']==pytest.approx(2/3) and m['rmse']==pytest.approx(math.sqrt(14/3))
    assert numeric([])['mae'] is None
    c=confusion([(1,3),(1,1)],(1,2,3));assert c['missing_gt_classes']==[2,3] and c['per_class']['2']['f1'] is None

def test_sut_units_missing_invalid_duplicates_and_time_origins():
    import json
    c=config();c['units']={'timestamp':'ms','speed':'km/h'}
    rows=[{'timestamp':1000,'camera':'c','roi':'r','speed':3.6,'level':2},{'timestamp':1000,'camera':'c','roi':'r'},{'timestamp':500,'camera':'c','roi':'r'},{'timestamp':2000,'camera':'c','roi':'r','count':-1}]
    parsed=parse_sut(json.dumps(rows),c,SimpleNamespace(source_offset_seconds='0'))
    assert len(parsed['accepted'])==2 and len(parsed['rejected'])==2
    assert parsed['accepted'][0]['values']['speed']==1 and parsed['accepted'][0]['values']['count'] is None
    assert parsed['accepted'][1]['validity']['count']=='SUT_FIELD_INVALID'
    c['time_origin']='SOURCE_START';assert parse_sut(json.dumps(rows[:1]),c,SimpleNamespace(source_offset_seconds='.5'))['accepted'][0]['time']==.5
    c['time_origin']='WALL_CLOCK';c['media_zero_iso']='2026-01-01T00:00:00+07:00';rows[0]['timestamp']='2025-12-31T17:00:01Z'
    assert parse_sut(json.dumps(rows[:1]),c,SimpleNamespace(source_offset_seconds='0'))['accepted'][0]['time']==1

def test_calibration_separate_holdout_and_invalid_coverage():
    p,l=calibration();roi=[[0,0],[640,0],[640,360],[0,360]]
    result=fit_calibration(p,l,roi,.001);assert result['valid'] and not result['fit_uses_holdout']
    p[-1]['world']=[99,99];bad=fit_calibration(p,l,roi,.001);assert not bad['valid']
    assert bad['homography']==result['homography']
    with pytest.raises(ValueError):fit_calibration([dict(x,pixel=[i,0]) for i,x in enumerate(p)],l,roi,.001)

def path(xs):return [{'time':i/10,'anchor':[x,100],'verified':True,'observed':True,'identity_verified':True} for i,x in enumerate(xs)]

def test_speed_stationary_opposite_turnback_gaps_and_population():
    p,l=calibration();c={**fit_calibration(p,l,[[0,0],[640,0],[640,360],[0,360]],.001),'approved':True}
    a=path([100+i*10 for i in range(11)]);b=path([300-i*10 for i in range(11)])
    result=population_speed({'a':a,'b':b},1,2,[[0,0],[640,0],[640,360],[0,360]],c,{'window_seconds':1,'max_gap_seconds':.25})
    assert result['value']==pytest.approx(1)
    assert individual_speed(path([100]*11),1,1,.25,c)['value']==0
    assert individual_speed(path([100,120,140,160,180,200,180,160,140,120,100]),1,1,.25,c)['value']==pytest.approx(2)
    assert individual_speed(a[:3]+a[8:],1,1,.25,c)['value'] is None
    assert individual_speed(a[5:],1,1,.25,c)['value'] is None
    assert population_speed({'a':a},1,2,[[0,0],[640,0],[640,360],[0,360]],c,{'window_seconds':1,'max_gap_seconds':.25})['value'] is None
    assert population_speed({'a':a},1,1,[[0,0],[640,0],[640,360],[0,360]],{}, {'window_seconds':1,'max_gap_seconds':.25})['reason']=='CALIBRATION_INVALID'

def test_one_to_one_alignment_excludes_window_mismatch():
    import json
    from backend.app.gt.observations import derive
    from backend.app.annotations.api import rules
    r=rules();obs={**derive([],[[0,0],[1,0],[1,1],[0,1]],1,r,True),'gt_eligible':True}
    entries=[{'frame':{'id':'f','relative_seconds':1},'observation':obs,'revision':{'id':'r','rules':r}}]
    c=config();c['semantics']['density']='PAST_WINDOW'
    parsed=parse_sut(json.dumps([{'timestamp':1,'camera':'c','roi':'r','count':0,'density':0,'level':1},{'timestamp':1.01,'camera':'c','roi':'r','count':0}]),c,SimpleNamespace(source_offset_seconds='0'))
    result=evaluate(entries,parsed,{'tolerance_seconds':.1,'rules_hash':'unknown'})
    assert result['coverage']['matched']==1 and result['metrics']['count']['support']==1
    assert result['metrics']['density']['support']==0 and result['pairs'][0]['excluded_metrics']['density']=='WINDOW_SEMANTICS_MISMATCH'

def test_helper_box_matching_no_duplicate_credit():
    r=match_boxes([{'box':[0,0,10,10]}],[[0,0,10,10],[0,0,10,10]],[.9,.8])
    assert r['tp']==1 and r['fp']==1 and r['fn']==0
