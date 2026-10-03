import copy
from types import SimpleNamespace
import json
import pytest
from backend.app.workflows.aggregate import aggregate
from backend.app.workflows.benchmark import evaluate,parse_sut
from backend.app.workflows.effort import summarize_trials
from backend.app.workflows.motion import fit_calibration,draft_motion
from backend.app.workflows.helper import gate
from backend.app.workflows.helper import match_boxes,diagnostic_slices
from backend.app.workflows.helper import helper_count
from backend.app.workflows.api import Trial,SpeedSnapshot
from backend.app.annotations.api import rules
from backend.app.gt.observations import derive

def report(id,errors):
    config={'format':'JSON','mapping':{k:k for k in ('timestamp','camera','roi','count','density','speed','level')},'units':{'timestamp':'s','speed':'m/s'},'time_origin':'MEDIA_START','offset_seconds':0,'camera_value':'c','roi_value':'r','semantics':{k:'INSTANT' for k in ('count','density','speed','level')},'evidence_type':'DEMO'}
    obs={**derive([],[[0,0],[1,0],[1,1],[0,1]],1,rules(),True),'gt_eligible':True}
    entries=[{'frame':{'id':id+str(i),'relative_seconds':i},'observation':obs,'revision':{'id':id+str(i),'rules':rules()}} for i in range(len(errors))]
    sut=parse_sut(json.dumps([{'timestamp':i,'camera':'c','roi':'r','count':e,'density':0,'level':1} for i,e in enumerate(errors)]),config,SimpleNamespace(source_offset_seconds=0))
    p=evaluate(entries,sut,{'tolerance_seconds':0,'rules_hash':'r'})
    p.update(aggregation_protocol={'rules':'same'},evidence_type='DEMO',scope={'camera_id':id,'roi_version_id':id,'condition':'SIMULATED'},sample_keys=[[id,i] for i in range(len(errors))])
    return {'id':id,'content_hash':id,'payload':p}

def test_pool_raw_errors_and_confusion_counts_not_camera_means():
    a,b=report('a',[0,0]),report('b',[9]);p=aggregate([a,b])
    assert p['metrics']['count']['mae']==3 and p['metrics']['count']['p95_absolute_error']==pytest.approx(8.1)
    assert p['metrics']['count']['support']==3 and p['metrics']['level_confusion']['support']==3
    assert len(p['slices'])==2 and p['coverage']['matched']==3
    b['payload']['sample_keys'][0]=a['payload']['sample_keys'][0]
    with pytest.raises(ValueError,match='OVERLAPPING'):aggregate([a,b])
    b=report('b',[9]);b['payload']['aggregation_protocol']={'rules':'other'}
    with pytest.raises(ValueError,match='INCOMPATIBLE'):aggregate([a,b])
    b=report('b',[9]);b['payload']['evidence_type']='REFERENCE_COMPARISON'
    with pytest.raises(ValueError,match='DEMO_AND_REAL'):aggregate([a,b])

def test_exact_alignment_reserved_before_nearest_match():
    p=report('a',[0])['payload']
    config={'format':'JSON','mapping':{k:k for k in ('timestamp','camera','roi','count')},'units':{'timestamp':'s'},'time_origin':'MEDIA_START','offset_seconds':0,'camera_value':'c','roi_value':'r','semantics':{'count':'INSTANT'},'evidence_type':'DEMO'}
    obs={**derive([],[[0,0],[1,0],[1,1],[0,1]],1,rules(),True),'gt_eligible':True}
    entries=[{'frame':{'id':'f','relative_seconds':1},'observation':obs,'revision':{'id':'r','rules':rules()}}]
    sut=parse_sut(json.dumps([{'timestamp':.99,'camera':'c','roi':'r','count':9},{'timestamp':1,'camera':'c','roi':'r','count':0}]),config,SimpleNamespace(source_offset_seconds=0))
    result=evaluate(entries,sut,{'tolerance_seconds':.1,'rules_hash':'r'})
    assert result['pairs'][0]['sut_row']==1 and result['metrics']['count']['mae']==0

def test_millisecond_units_do_not_create_binary_time_mismatch_or_poison_valid_rows():
    config={'format':'JSON','mapping':{k:k for k in ('timestamp','camera','roi','count','window_start')},'units':{'timestamp':'ms'},'time_origin':'MEDIA_START','offset_seconds':0,'camera_value':'c','roi_value':'r','semantics':{},'evidence_type':'DEMO'}
    rows=[{'timestamp':700,'window_start':800,'camera':'c','roi':'r'},{'timestamp':700,'window_start':0,'camera':'c','roi':'r'}]
    result=parse_sut(json.dumps(rows),config,SimpleNamespace(source_offset_seconds=0))
    assert len(result['rejected'])==1 and result['accepted'][0]['time']==.7

def trial(id='a',order='MANUAL_FIRST'):
    return {'pair_id':id,'camera':'c','condition':'sim','split':'validation','order':order,'manual_active_seconds':100,'assisted_active_seconds':70,'manual_total_seconds':110,'assisted_total_seconds':100,'manual_quality':1,'assisted_quality':1,'independent_quality_checked':True,'edits':2,'unchanged_objects':8,'objects':10}

def test_effort_quality_order_denominator_and_demo_claim_gates():
    rows=[trial(),trial('b','AI_FIRST')];p=summarize_trials(rows,'DEMO',0)
    assert p['summary']['active_reduction_median']==.3 and p['human_active_reduction'] is None and p['target_25_percent_met'] is None
    assert summarize_trials(rows,'REAL',0)['human_active_reduction']==.3
    rows[1]['assisted_quality']=.8
    assert summarize_trials(rows,'REAL',0)['human_active_reduction'] is None
    rows[1]['independent_quality_checked']=False
    assert summarize_trials(rows,'REAL',0)['excluded'][0]['reason']=='INDEPENDENT_QUALITY_REQUIRED'
    with pytest.raises(ValueError):Trial(**{**trial(),'manual_total_seconds':20})
    with pytest.raises(ValueError):SpeedSnapshot(gt_snapshot_id='g',calibration_id='c',track_revision_id='t',actor='x',speed_uncertainty_mps=.02)

def test_draft_motion_never_promotes_estimated_observations():
    points=[{'pixel':p,'world':[p[0]/100,p[1]/100],'role':'FIT','region':'MID'} for p in [[0,0],[640,0],[640,360],[0,360]]]
    points += [{'pixel':p,'world':[p[0]/100,p[1]/100],'role':'HOLDOUT','region':r} for p,r in [([100,300],'NEAR'),([300,180],'MID'),([500,50],'FAR')]]
    polygon=[[0,0],[640,0],[640,360],[0,360]];cal={**fit_calibration(points,[{'a':[0,0],'b':[100,0],'meters':1,'region':'NEAR'}],polygon,0),'approved':True}
    tracks={'p':[{'frame_id':str(i),'time':i/10,'anchor':[100+6*i,100],'verified':False,'observed':False,'identity_verified':False} for i in range(11)]};before=copy.deepcopy(tracks)
    config={'max_gap_seconds':.25,'boundary_pixels':10,'max_speed_mps':.5,'jitter_displacement_m':.05,'jitter_path_m':.1}
    p=draft_motion(tracks,cal,polygon,0,1,config)
    assert p['tracks']['p']['rows'][-1]['draft_speed']==pytest.approx(.6)
    assert p['tracks']['p']['rows'][-1]['reviewed_speed'] is None and p['status']=='DRAFT_NOT_GT'
    assert 'IMPLAUSIBLE_SPEED_OR_ID_JUMP' in p['tracks']['p']['flags'] and tracks==before

def test_vram_gate_missing_or_exceeded_stays_baseline():
    f={'camera_id':'c','condition':'s','tp':1,'fn':0,'latency_seconds':.01}
    models=[{'map50_95':.5,'frames':[f],'peak_vram_bytes':100},{'map50_95':.6,'frames':[f],'peak_vram_bytes':200}]
    profile={'min_images':1,'min_images_per_slice':1,'min_map_gain':.01,'max_slice_recall_drop':0,'max_latency_seconds':1,'max_vram_bytes':150}
    assert 'VRAM_GATE_FAILED_OR_UNMEASURED' in gate(models,{'purpose':'REAL'},profile)['reasons']
    profile['max_vram_bytes']=300;assert gate(models,{'purpose':'REAL'},profile)['verdict']=='ELIGIBLE'
    profile['max_vram_bytes']=None;assert gate(models,{'purpose':'REAL'},profile)['verdict']=='KEEP_BASELINE'

def test_helper_diagnostics_size_crowding_and_unmeasured_occlusion():
    boxes=[{'box':[0,0,5,5]},{'box':[0,0,50,50]}];matched=match_boxes(boxes,[[0,0,5,5]],[.9])
    result=diagnostic_slices([{'gt_boxes':boxes,'width':100,'height':100,**matched}])['slices']
    small=next(s for s in result if s['slice']=='SMALL');large=next(s for s in result if s['slice']=='LARGE')
    assert small['recall']==1 and large['recall']==0
    assert next(s for s in result if s['dimension']=='occlusion')['recall'] is None

def test_helper_count_requires_scope_and_reviewed_membership():
    polygon=[[0,0],[100,0],[100,100],[0,100]]
    entry={'rect':[0,0,100,100],'review':{'verdict':'APPROVED','completeness':{'count':True}},'revision':{'objects':[{'id':'a','anchor':[25,50],'anchor_verified':True},{'id':'b','anchor':[75,50],'anchor_verified':True}]}}
    assert helper_count(entry,[[10,10,40,50]],polygon)['error']==-1
    entry['revision']['objects'][0]['anchor_verified']=False
    assert helper_count(entry,[],polygon)['error'] is None
    entry['rect']=[0,0,50,50]
    assert helper_count(entry,[],polygon)['reason']=='CROP_DOES_NOT_COVER_ROI'
