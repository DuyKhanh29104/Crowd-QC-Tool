import copy
import io
import json
import zipfile
import pytest
from test_review import c,seed,save,approve,snap
from backend.app.db.session import sessions
from backend.app.workflows.store import artifact
from backend.app.workflows.backup import backup,restore
from backend.app.main import create_app
from fastapi.testclient import TestClient

def post(c,path,body,status=201):
    r=c.client.post('/api/workflows'+path,json=body)
    assert r.status_code==status,r.text
    return r.json()

def test_import_evaluate_export_restore_preserves_evidence(c,tmp_path):
    doc=seed(c);objects=copy.deepcopy(doc['revision']['objects'])
    for o in objects:o.update(anchor=[50,100],anchor_verified=True)
    doc=save(c,doc,objects);doc=approve(c,doc,count_complete=True).json();gt=snap(c,doc).json()
    config={'mapping':{k:k for k in ['timestamp','camera','roi','count','density','speed','level']},'units':{'timestamp':'s','speed':'m/s'},'semantics':{k:'INSTANT' for k in ['count','density','speed','level']},'camera_value':'c','roi_value':'r','offset_evidence':'DEMO exact PTS'}
    sut=post(c,'/sut',{'media_id':c.media.id,'roi_version_id':c.roi['id'],'raw_text':json.dumps([{'timestamp':0,'camera':'c','roi':'r','count':3,'density':.75,'level':2},{'timestamp':0,'camera':'c','roi':'r'}]),'config':config,'actor':'DEMO'})
    assert len(sut['payload']['rejected'])==1
    ev=post(c,'/evaluate',{'gt_snapshot_id':gt['id'],'sut_id':sut['id'],'actor':'DEMO'})
    assert ev['payload']['metrics']['count']['mae']==1
    assert ev['payload']['metrics']['speed']['support']==0
    data=c.client.get('/api/workflows/artifacts/'+ev['id']+'/report.zip').content
    with zipfile.ZipFile(io.BytesIO(data)) as z:assert {'metrics.csv','report.html','run_manifest.json'}<=set(z.namelist())
    archive=tmp_path/'backup.zip';backup(c.app.state.data_root,archive,True)
    target=tmp_path/'restored';restore(archive,target)
    app=create_app(target)
    with TestClient(app) as client:
        assert client.get('/api/workflows/artifacts/'+ev['id']).json()==ev
        assert client.get('/api/review/snapshots/'+gt['id']).json()==gt
        assert client.get('/api/frames/'+c.frames[0].id+'/image').status_code==200
    app.state.engine.dispose()
    with pytest.raises(ValueError):restore(archive,target)

def test_track_review_cas_and_invalid_time(c):
    with sessions(c.app.state.engine).begin() as db:
        row=artifact(db,'TRACKS',{'media_id':c.media.id,'source_sha256':c.media.sha256,'tracks':{},'evidence_type':'DEMO'},c.media.project_id)
        id=row.id
    body={'expected_revision_id':id,'tracks':{'p':[{'frame_id':c.frames[0].id,'pts':c.frames[0].pts,'time':0,'anchor':[50,100],'verified':True,'observed':True,'identity_verified':True}]},'reviewed':True,'viewed_complete_window':False,'start_seconds':0,'end_seconds':.2,'notes':'DEMO','actor':'DEMO'}
    post(c,f'/tracks/{id}/revisions',body,422)
    body['viewed_complete_window']=True
    row=post(c,f'/tracks/{id}/revisions',body)
    post(c,f'/tracks/{id}/revisions',body,409)
    body['expected_revision_id']=row['id'];body['tracks']['p'][0]['pts']+=1
    post(c,f'/tracks/{id}/revisions',body,422)

def test_model_disposition_preserves_checkpoint_and_protects_active(c):
    from backend.app.workflows.store import baseline
    with sessions(c.app.state.engine).begin() as db:
        original=baseline(db)
        model=artifact(db,'MODEL',{**original.payload,'baseline':False,'evidence_type':'DEMO'},c.media.project_id)
        id=model.id;baseline_id=original.id;checksum=model.content_hash
    body={'state':'ARCHIVED','reason':'SIMULATED lifecycle test','actor':'DEMO'}
    post(c,'/models/'+baseline_id+'/decision',body,409)
    post(c,'/models/'+id+'/decision',body)
    listing=c.client.get('/api/workflows/learning').json()
    assert next(m for m in listing['models'] if m['id']==id)['registry_state']=='ARCHIVED'
    assert c.client.get('/api/workflows/artifacts/'+id).json()['content_hash']==checksum
    post(c,'/activate',{'model_id':id,'namespace':'DEMO','expected_version':0,'reason':'test','actor':'DEMO'},409)
    post(c,'/models/'+id+'/decision',{**body,'state':'CANDIDATE'})

def test_pooled_api_rejects_overlap_and_exports_slices(c):
    ids=[]
    for i in (0,1):
        doc=seed(c,i);objects=copy.deepcopy(doc['revision']['objects'])
        for o in objects:o.update(anchor=[50,100],anchor_verified=True)
        doc=save(c,doc,objects);doc=approve(c,doc,count_complete=True).json();gt=snap(c,doc).json()
        config={'mapping':{k:k for k in ['timestamp','camera','roi','count','density','speed','level']},'units':{'timestamp':'s','speed':'m/s'},'semantics':{k:'INSTANT' for k in ['count','density','speed','level']},'camera_value':'c','roi_value':'r','offset_evidence':'DEMO PTS'}
        sut=post(c,'/sut',{'media_id':c.media.id,'roi_version_id':c.roi['id'],'raw_text':json.dumps([{'timestamp':c.frames[i].relative_seconds,'camera':'c','roi':'r','count':3}]),'config':config,'actor':'DEMO'})
        ev=post(c,'/evaluate',{'gt_snapshot_id':gt['id'],'sut_id':sut['id'],'actor':'DEMO'});ids.append(ev['id'])
    pooled=post(c,'/projects/'+c.media.project_id+'/aggregate',{'evaluation_ids':ids,'actor':'DEMO'})
    assert pooled['payload']['metrics']['count']['support']==2 and pooled['payload']['metrics']['count']['mae']==1
    again=post(c,'/evaluate',{'gt_snapshot_id':gt['id'],'sut_id':sut['id'],'actor':'DEMO'})
    post(c,'/projects/'+c.media.project_id+'/aggregate',{'evaluation_ids':[ids[-1],again['id']],'actor':'DEMO'},409)
    with zipfile.ZipFile(io.BytesIO(c.client.get('/api/workflows/artifacts/'+pooled['id']+'/report.zip').content)) as z:assert 'slices.csv' in z.namelist()

def test_effort_api_does_not_turn_demo_into_human_measurements(c):
    row={'pair_id':'a','camera':'c','condition':'s','order':'MANUAL_FIRST','manual_active_seconds':10,'assisted_active_seconds':7,'manual_total_seconds':15,'assisted_total_seconds':12,'manual_quality':1,'assisted_quality':1,'independent_quality_checked':True,'edits':0,'unchanged_objects':0,'objects':0}
    body={'rows':[row,{**row,'pair_id':'b','order':'AI_FIRST'}],'actor':'DEMO','protocol_evidence':'SIMULATED'}
    result=post(c,'/projects/'+c.media.project_id+'/effort-studies',body)
    assert result['payload']['human_active_reduction'] is None
    post(c,'/projects/'+c.media.project_id+'/effort-studies',{**body,'evidence_type':'REAL'},409)
    post(c,'/projects/'+c.media.project_id+'/effort-studies',{**body,'rows':[row,row]},422)

def test_tampered_dataset_blocks_train_before_launch(c,tmp_path):
    import uuid
    from backend.app.db.models import DatasetSnapshot,Job
    from backend.app.jobs.profiles import json_hash
    from backend.app.media.service import digest
    from backend.app.workflows.store import baseline
    from sqlalchemy import select
    id=uuid.uuid4().hex;folder=c.app.state.data_root/'datasets'/id;folder.mkdir(parents=True)
    path=folder/'data.yaml';path.write_text('frozen')
    manifest={'files':{'data.yaml':digest(path)},'summary':{'splits':{'train':1,'validation':1}},'purpose':'DEMO_SMOKE'}
    (folder/'manifest.json').write_text(json.dumps(manifest))
    with sessions(c.app.state.engine).begin() as db:
        model_id=baseline(db).id;db.add(DatasetSnapshot(id=id,project_id=c.media.project_id,manifest=manifest,manifest_hash=json_hash(manifest)))
    path.write_text('tampered')
    result=post(c,'/train',{'dataset_id':id,'parent_model_id':model_id,'actor':'DEMO'},409)
    assert result['code']=='DATASET_CORRUPTED'
    with sessions(c.app.state.engine)() as db:assert not db.scalar(select(Job.id).where(Job.kind=='TRAIN'))

def test_restore_rejects_remap_escape(tmp_path):
    import hashlib
    path=tmp_path/'bad-map.zip';data=b'fixture'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('crowd_qc.sqlite',data)
        z.writestr('backup_manifest.json',json.dumps({'files':{'crowd_qc.sqlite':hashlib.sha256(data).hexdigest()},'path_map':{'checkpoint':'../../outside'},'sources':[]}))
    with pytest.raises(ValueError,match='remap'):restore(path,tmp_path/'restore')
    assert not (tmp_path/'restore').exists()

def test_calibration_holdout_does_not_approve_bad_geometry(c):
    points=[{'pixel':p,'world':[p[0]/100,p[1]/100],'role':'FIT','region':'MID'} for p in [[0,0],[640,0],[640,360],[0,360]]]
    points += [{'pixel':p,'world':[p[0]/100,p[1]/100],'role':'HOLDOUT','region':r} for p,r in [([100,300],'NEAR'),([300,180],'MID'),([500,50],'FAR')]]
    body={'media_id':c.media.id,'roi_version_id':c.roi['id'],'points':points,'lengths':[{'a':[100,100],'b':[200,100],'meters':1,'region':'MID'}],'repeatability_mps':0,'approve':True,'evidence':'SIMULATED','actor':'DEMO'}
    good=post(c,'/calibrations',body);assert good['payload']['approved']
    body['points'][-1]['world']=[99,99]
    bad=post(c,'/calibrations',body);assert not bad['payload']['approved']
    assert bad['payload']['homography']==good['payload']['homography']

def test_restore_rejects_path_traversal_before_creating_directory(tmp_path):
    import hashlib
    path=tmp_path/'evil.zip'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('../escape',b'x');z.writestr('backup_manifest.json',json.dumps({'files':{'../escape':hashlib.sha256(b'x').hexdigest()}}))
    with pytest.raises(ValueError):restore(path,tmp_path/'target')
    assert not (tmp_path/'target').exists()

def test_training_simulated_oom_publishes_no_candidate_and_releases_lease(c,monkeypatch,tmp_path):
    import sys
    import uuid
    from types import SimpleNamespace
    from backend.app.workflows import worker
    from backend.app.db.models import Job,WorkArtifact
    from backend.app.media.service import digest
    from backend.app.jobs.profiles import gpu_lease
    from sqlalchemy import select
    monkeypatch.setenv('CROWD_QC_GPU_LOCK_ROOT',str(tmp_path/'lease'))
    monkeypatch.setattr(worker,'runtime_versions',lambda:{'test':'SIMULATED_OOM'})
    cuda=SimpleNamespace(is_available=lambda:True,reset_peak_memory_stats=lambda:None,get_device_name=lambda _: 'SIMULATED',empty_cache=lambda:None)
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(cuda=cuda,version=SimpleNamespace(cuda='SIMULATED')))
    def fail(*a,**kw):raise RuntimeError('CUDA out of memory (SIMULATED fault injection)')
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=fail))
    monkeypatch.setattr(worker,'dataset',lambda *args:(SimpleNamespace(manifest_hash='hash'),tmp_path))
    with sessions(c.app.state.engine).begin() as db:
        model=artifact(db,'MODEL',{'weights_path':c.media.source_path,'weights_sha256':digest(c.media.source_path)},c.media.project_id)
        id=uuid.uuid4().hex
        db.add(Job(id=id,project_id=c.media.project_id,kind='TRAIN',state='QUEUED',progress=0,payload={'parent_model_id':model.id,'dataset_id':'simulated','dataset_hash':'hash','profile':{'epochs':1}}))
    worker.run(c.app.state.data_root,id)
    with sessions(c.app.state.engine)() as db:
        job=db.get(Job,id);assert job.state=='FAILED' and job.error['code']=='CUDA_OOM'
        assert len(list(db.scalars(select(WorkArtifact).where(WorkArtifact.kind=='MODEL'))))==1
    lease=gpu_lease();assert lease.acquire();lease.release()
