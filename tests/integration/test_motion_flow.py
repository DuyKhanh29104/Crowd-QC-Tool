import copy
import json
import sys
import zipfile
from types import SimpleNamespace

import pytest

from backend.app.db.models import Job
from backend.app.db.session import sessions
from backend.app.qc import worker
from backend.app.workflows.store import artifact, get
from test_qc_workflow import qc


def calibrated_profile(qc):
    ground={'points':[[0,0],[640,0],[640,360],[0,360]],'width_m':6.4,'height_m':3.6,
            'test':{'points':[[50,50],[150,50]],'expected_m':1}}
    head={**ground,'plane':'head','reference_height_m':1.65,'width_m':12.8,'height_m':7.2,
          'test':{'points':[[50,50],[150,50]],'expected_m':2}}
    response=qc.client.post('/api/qc/profiles',json={'name':'Two planes','media_id':qc.media,
        'rois':qc.profile['payload']['rois'],
        'calibration':ground,'head_calibration':head})
    assert response.status_code==201,response.text
    return response.json()


def analysis_for(qc,target,profile):
    with sessions(qc.app.state.engine).begin() as db:
        return artifact(db,'QC_ANALYSIS',{**qc.analysis.payload,'camera':profile['payload'],
                        'settings':{**qc.analysis.payload['settings'],'detection_target':target}},qc.project)


def pair(qc,target):
    return {'frames':[{'frame_id':qc.frames[i].id,'objects':[{'id':'box','box':[x,20,x+20,60],
                     'track_id':'same-person','label_type':target}]} for i,x in [(0,10),(5,30)]]}


@pytest.mark.parametrize('target,expected',[('person',.4),('head',.8)])
def test_profile_to_manual_boxes_speed_and_plane_preview(qc,target,expected):
    profile=calibrated_profile(qc);run=analysis_for(qc,target,profile)
    response=qc.client.post(f'/api/qc/analysis/{run.id}/motion-preview',json=pair(qc,target))
    assert response.status_code==200,response.text
    result=response.json();obj=result['rows'][1]['objects'][0]
    assert obj['speed']==pytest.approx(expected) and obj['speed_px_s']==40
    assert obj['velocity_mps']==pytest.approx([expected,0])
    assert result['status']=='MANUAL_PAIR_ESTIMATE_NOT_GT' and not result['identity_verified']
    assert result['motion']['plane']==('head' if target=='head' else 'ground')
    assert qc.client.get(f"/api/qc/media/{qc.media}/bird-eye/{profile['id']}?plane=head").status_code==200
    data=qc.client.get(f'/api/qc/analysis/{run.id}').json()
    assert data['rows']==qc.analysis.payload['rows']  # Preview never overwrites AI rows.


def test_preview_rejects_bad_geometry_labels_and_time(qc):
    run=analysis_for(qc,'head',calibrated_profile(qc));url=f'/api/qc/analysis/{run.id}/motion-preview'
    good=pair(qc,'head')
    for mutation in ('bounds','label','order','duplicate_frame','missing_frame'):
        body=copy.deepcopy(good)
        if mutation=='bounds':body['frames'][1]['objects'][0]['box'][2]=1000
        elif mutation=='label':body['frames'][1]['objects'][0]['label_type']='person'
        elif mutation=='order':body['frames'].reverse()
        elif mutation=='duplicate_frame':body['frames'][1]['frame_id']=body['frames'][0]['frame_id']
        else:body['frames'][1]['frame_id']='not-a-frame'
        assert qc.client.post(url,json=body).status_code in (404,422)
    body=copy.deepcopy(good);body['frames'][1]['objects'][0]['track_id']='another-person'
    row=qc.client.post(url,json=body).json()['rows'][1]
    assert row['speed'] is None and row['objects'][0]['speed_reason']=='TRACK_HISTORY_REQUIRED'


def test_corrected_box_never_keeps_old_ai_speed(qc):
    from test_qc_workflow import decision
    obj={**qc.objects[0],'speed':99,'speed_px_s':999,'velocity_mps':[99,0],
         'velocity_px_s':[999,0],'speed_reason':None}
    result=decision(qc,decision='CORRECT',frame_edits={qc.frames[0].id:[obj]})
    assert result.status_code==201,result.text
    corrected=result.json()['payload']['frame_edits'][qc.frames[0].id][0]
    assert corrected['box']==obj['box'] and corrected['track_id']==obj['track_id']
    assert not set(corrected)&{'speed','speed_px_s','velocity_mps','velocity_px_s','anchor','inside','speed_reason'}


def test_head_profile_cannot_be_saved_as_floor_and_needs_height(qc):
    profile=calibrated_profile(qc)
    data={k:profile['payload'][k] for k in ('name','media_id','rois','settings')}
    cal={k:v for k,v in profile['payload']['head_calibration'].items() if k in ('points','width_m','height_m','test','plane','reference_height_m')}
    assert qc.client.post('/api/qc/profiles',json={**data,'calibration':cal}).status_code==422
    cal.pop('reference_height_m')
    assert qc.client.post('/api/qc/profiles',json={**data,'head_calibration':cal}).status_code==422


@pytest.mark.parametrize('target,expected',[('person',.2),('head',.4)])
def test_analysis_worker_records_speed_and_tracking_export(qc,monkeypatch,target,expected):
    profile=calibrated_profile(qc)
    payload={**qc.analysis.payload,'camera':profile['payload'],
             'settings':{**qc.analysis.payload['settings'],'detection_target':target,'imgsz':960},
             'detector':{'target':target,'id':'DETERMINISTIC_TEST'}}
    # Exercise real decoding, metric selection, persistence and export with a deterministic detector.
    class Model:
        names={0:target};task='detect'
        model=SimpleNamespace(parameters=lambda:iter([SimpleNamespace(device='test-fixture')]))
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:True)))
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=lambda _:Model()))
    monkeypatch.setattr(worker,'detector_path',lambda _:('fixture.pt','test-checksum'))
    monkeypatch.setattr(worker,'runtime_versions',lambda:{'detector':'DETERMINISTIC_TEST'})
    calls=[]
    def infer(_self,_model,_image,_settings):
        assert _self.base_size==960 and _self.imgsz==(544,960)
        x=20+10*len(calls);calls.append(x)
        return [{'id':'box','box':[x,20,x+20,60],'track_id':'1','label_type':target}]
    monkeypatch.setattr(worker.ROIInput,'infer',infer)
    monkeypatch.setenv('CROWD_QC_GPU_LOCK_ROOT',str(qc.root/'test-locks'))
    with sessions(qc.app.state.engine).begin() as db:
        db.add(Job(id='motion-test',project_id=qc.project,kind='QC_ANALYSIS',state='QUEUED',payload=payload))
    worker.run(qc.root,'motion-test')
    with sessions(qc.app.state.engine)() as db:
        job=db.get(Job,'motion-test');assert job.state=='SUCCEEDED',job.error
        run=get(db,job.result['artifact_id'],'QC_ANALYSIS')
        assert run.payload['inference']['base_size']==960
        assert run.payload['speed_available']
        assert run.payload['rows'][0]['speed'] is None
        assert run.payload['rows'][1]['speed']==pytest.approx(expected)
    route=f'/api/qc/analysis/{run.id}/candidates'
    candidate=qc.client.post(route,json={'start':0,'end':1}).json()
    response=qc.client.post(route+'/'+candidate['id']+'/decision',json={
        'decision':'CORRECT','label':'NORMAL','start':0,'end':1,'expected_version':0})
    assert response.status_code==201,response.text
    with sessions(qc.app.state.engine)() as db:
        export_id,_=worker.export_dataset(db,qc.root,{'review_ids':[response.json()['id']],
                            'formats':['Tracking Data'],'scope':'verified','name':'Motion'},lambda *_:None)
    with zipfile.ZipFile(qc.root/'qc_exports'/(export_id+'.zip')) as archive:
        tracking=json.loads(archive.read('tracking/'+response.json()['id']+'.json'))
    assert tracking['motion']['plane']==('head' if target=='head' else 'ground')
    assert tracking['speed_units']['speed']=='m/s'
    assert tracking['ai_samples'][1]['objects'][0]['speed']==pytest.approx(expected)
