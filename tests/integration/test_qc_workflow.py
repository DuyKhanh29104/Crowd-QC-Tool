import io
import json
import zipfile
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.main import create_app
from backend.app.media.demo import make_video
from backend.app.db.session import sessions
from backend.app.db.models import Frame,Job
from backend.app.workflows.store import artifact
from backend.app.qc.analysis import calibration,metrics,candidates
from backend.app.qc.api import Settings
from backend.app.qc.worker import export_dataset,run
from backend.app.qc.decoding import sampled_images
from test_media_roi import import_video

@pytest.fixture
def qc(tmp_path):
    root=tmp_path/'data';app=create_app(root)
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'QC workflow'}).json()['id']
        source=make_video(tmp_path/'source.mkv',list(range(0,3000,100)))
        media=import_video(client,project,source)['result']['media_id']
        body={'name':'Entrance','media_id':media,'rois':[{'id':'roi','name':'Lobby','polygon':[[0,0],[640,0],[640,360],[0,360]],'area_m2':20}]}
        response=client.post('/api/qc/profiles',json=body);assert response.status_code==201,response.text
        profile=response.json()
        with sessions(app.state.engine).begin() as db:
            frames=list(db.scalars(select(Frame).where(Frame.media_id==media).order_by(Frame.ordinal)))
            objects=[{'id':'person','track_id':'1','box':[20,20,60,100],'anchor':[40,100],'inside':True,'speed':None,'score':.9}]
            rows=[{'frame_id':f.id,'ordinal':f.ordinal,'pts':f.pts,'time':f.relative_seconds,'objects':objects,'count':1,'density':.05,'speed':None,'stationary':None,'label':'CROWDED','reasons':['Fixture only']} for f in frames]
            events=[{'id':'a','start':0,'end':1,'label':'CROWDED','peak_count':1,'avg_speed':None,'stationary':None,'reasons':['Fixture only']},{'id':'b','start':1,'end':2,'label':'CROWDED','peak_count':1,'avg_speed':None,'stationary':None,'reasons':['Fixture only']}]
            analysis=artifact(db,'QC_ANALYSIS',{'media_id':media,'profile_id':profile['id'],'roi':profile['payload']['rois'][0],'rows':rows,'candidates':events,'duration':3,'evidence_type':'DEMO','settings':Settings().model_dump()},project)
        yield SimpleNamespace(root=root,app=app,client=client,project=project,media=media,profile=profile,analysis=analysis,frames=frames,objects=objects)

def decision(qc,candidate='a',**extra):
    return qc.client.post(f'/api/qc/analysis/{qc.analysis.id}/candidates/{candidate}/decision',json={'decision':'APPROVE','label':'CROWDED','start':0 if candidate=='a' else 1,'end':1 if candidate=='a' else 2,'reviewer':'Tester','expected_version':0,**extra})

def test_profile_reuse_resolution_and_calibration(qc,tmp_path):
    data=qc.client.get(f'/api/qc/projects/{qc.project}/workspace').json()
    assert data['media'][0]['profile_id']==qc.profile['id']
    other=import_video(qc.client,qc.project,make_video(tmp_path/'real.mkv',[0,100]),evidence_type='REAL')['result']['media_id']
    response=qc.client.post(f'/api/qc/media/{other}/profile',json={'profile_id':qc.profile['id']})
    assert response.status_code==422 and response.json()['code']=='EVIDENCE_MISMATCH'
    assert qc.client.post(f'/api/qc/media/{qc.media}/profile',json={'profile_id':qc.profile['id']}).status_code==200
    c=calibration([[0,0],[640,0],[640,360],[0,360]],8,5,{'points':[[50,50],[150,50]],'expected_m':1.25})
    assert c['distance_verified'] and c['distance_m']==pytest.approx(1.25)
    saved=qc.client.post('/api/qc/profiles',json={'name':'Calibrated camera','media_id':qc.media,'rois':qc.profile['payload']['rois'],'calibration':{'points':[[0,0],[640,0],[640,360],[0,360]],'width_m':8,'height_m':5,'test':{'points':[[50,50],[150,50]],'expected_m':1.25}}})
    assert saved.status_code==201,saved.text
    preview=qc.client.get(f"/api/qc/media/{qc.media}/bird-eye/{saved.json()['id']}")
    assert preview.status_code==200 and preview.headers['content-type']=='image/png'
    from PIL import Image
    assert Image.open(io.BytesIO(preview.content)).size==(600,375)
    assert not calibration([[0,0],[640,0],[640,360],[0,360]],8,5)['distance_verified']
    assert qc.client.post('/api/qc/calibration-preview',json={'points':[[0,0],[1,0],[2,0],[3,0]],'width_m':8,'height_m':5}).status_code==422


def test_sequential_decode_matches_exact_indexed_frames(qc):
    from backend.app.db.models import Media
    from backend.app.media.service import frame_image
    from PIL import Image
    with sessions(qc.app.state.engine)() as db:
        media=db.get(Media,qc.media)
        selected=qc.frames[1::7]
        decoded=list(sampled_images(media,selected))
        assert [f.pts for f,_,_ in decoded]==[f.pts for f in selected]
        for frame,image,elapsed in decoded:
            expected=Image.open(io.BytesIO(frame_image(media,frame,qc.root/'test_cache',10000000))).convert('RGB')
            assert image.tobytes()==expected.tobytes() and elapsed>=0
        with pytest.raises(ValueError,match='SAMPLE_PTS_NOT_INCREASING'):
            list(sampled_images(media,list(reversed(selected))))

@pytest.mark.parametrize('when', ['before_read', 'after_read'])
def test_cache_eviction_during_read_never_recreates_empty_png(qc, monkeypatch, when):
    from pathlib import Path
    from backend.app.db.models import Media
    from backend.app.media.service import frame_image
    with sessions(qc.app.state.engine)() as db:
        media = db.get(Media, qc.media)
        cache_dir = qc.root / 'race_cache'
        expected = frame_image(media, qc.frames[0], cache_dir, 10000000)
        cache = next(cache_dir.glob('*.png'))
        original = Path.read_bytes

        def evicted_read(path):
            if path == cache:
                if when == 'before_read':
                    path.unlink(missing_ok=True)
                value = original(path)
                if when == 'after_read':
                    path.unlink(missing_ok=True)
                return value
            return original(path)

        monkeypatch.setattr(Path, 'read_bytes', evicted_read)
        assert frame_image(media, qc.frames[0], cache_dir, 10000000) == expected
        assert all(path.stat().st_size > 0 for path in cache_dir.glob('*.png'))


def test_failed_export_removes_partial_archive(qc, monkeypatch):
    from backend.app.qc import worker
    reviewed = decision(qc).json()

    def fail(*args):
        raise RuntimeError('simulated decode failure')

    monkeypatch.setattr(worker, 'frame_image', fail)
    with sessions(qc.app.state.engine)() as db:
        with pytest.raises(RuntimeError, match='simulated decode failure'):
            export_dataset(db, qc.root, dict(review_ids=[reviewed['id']], formats=['Frames'],
                                            scope='verified', name='Failure'), lambda *_: None)
    assert not list((qc.root / 'qc_exports').iterdir())


def test_export_can_be_cancelled_inside_a_single_review(qc):
    from backend.app.jobs.worker import Cancelled
    reviewed = decision(qc).json()
    calls = []

    def checkpoint(*args):
        calls.append(args)
        if len(calls) == 3:
            raise Cancelled()

    with sessions(qc.app.state.engine)() as db:
        with pytest.raises(Cancelled):
            export_dataset(db, qc.root, dict(review_ids=[reviewed['id']], formats=['Frames'],
                                            scope='verified', name='Cancelled'), checkpoint)
    assert not list((qc.root / 'qc_exports').iterdir())


def test_decisions_are_durable_versioned_and_not_auto_box_gt(qc):
    first=decision(qc);assert first.status_code==201,first.text
    assert first.json()['payload']['verified'] and not first.json()['payload']['boxes_verified']
    assert decision(qc).status_code==409
    edited=decision(qc,decision='CORRECT',label='CONGESTED',count=5,expected_version=1,tags=['Occlusion'])
    assert edited.status_code==201,edited.text
    assert edited.json()['payload']['previous_review_id']==first.json()['id']
    assert qc.client.get(f'/api/qc/analysis/{qc.analysis.id}').json()['candidates'][0]['review']['payload']['count']==5
    rejected=decision(qc,'b',decision='REJECT');assert rejected.status_code==201
    assert rejected.json()['payload']['hard_negative'] and rejected.json()['payload']['label']=='NORMAL'
    assert qc.client.post(f'/api/qc/projects/{qc.project}/exports',json={'formats':['YOLO']}).status_code==422
    with TestClient(create_app(qc.root)) as restarted:
        assert len(restarted.get(f'/api/qc/projects/{qc.project}/workspace').json()['reviews'])==2

def test_invalid_corrections_and_uncertain(qc):
    assert decision(qc,start=0,end=100).status_code==422
    assert decision(qc,count=4).status_code==422
    assert decision(qc,decision='CORRECT',frame_edits={'wrong_frame':qc.objects}).status_code==422
    assert decision(qc,decision='CORRECT',frame_edits={qc.frames[0].id:[{'id':'bad','box':[-1,0,5,5]}]}).status_code==422
    assert decision(qc,decision='CORRECT',boxes_verified=[qc.frames[-1].id]).status_code==422
    result=decision(qc,decision='UNCERTAIN');assert result.status_code==201
    assert not result.json()['payload']['verified'] and result.json()['payload']['label']=='UNCERTAIN'
    assert qc.client.post(f'/api/qc/projects/{qc.project}/exports',json={}).status_code==422

def test_export_formats_and_no_unverified_negative_images(qc):
    review=decision(qc,decision='CORRECT',count=2,frame_edits={qc.frames[0].id:[{'id':'manual','box':[10,20,40,90],'track_id':'corrected'}]},boxes_verified=[qc.frames[0].id]).json()
    assert review.get('id'),review
    payload={'review_ids':[review['id']],'formats':['JSON','CSV','QC Report','Frames','YOLO','COCO','Tracking Data'],'scope':'corrected','name':'QC dataset'}
    with sessions(qc.app.state.engine)() as db:
        id,manifest=export_dataset(db,qc.root,payload,lambda *args:None)
    with zipfile.ZipFile(qc.root/'qc_exports'/(id+'.zip')) as z:
        assert 'events.json' in z.namelist() and 'events.csv' in z.namelist() and 'qc-report.html' in z.namelist()
        assert len([x for x in z.namelist() if x.startswith('images/')])==1
        assert len([x for x in z.namelist() if x.startswith('frames/')])>0
        assert len([x for x in z.namelist() if x.startswith('labels/')])==1
        labels=z.read(f'labels/{qc.frames[0].id}.txt').decode().splitlines()
        assert len(labels)==1 and len(labels[0].split())==5
        coco=json.loads(z.read('annotations.coco.json'));assert coco['annotations'][0]['bbox']==[10,20,30,70]
        assert len(coco['images'])==1 and manifest['excluded']

def test_export_worker_and_manual_candidate(qc):
    manual=qc.client.post(f'/api/qc/analysis/{qc.analysis.id}/candidates',json={'start':.2,'end':.5})
    assert manual.status_code==201 and manual.json()['label']=='UNCERTAIN'
    assert len(qc.client.get(f'/api/qc/analysis/{qc.analysis.id}').json()['candidates'])==3
    reviewed=decision(qc).json()
    with sessions(qc.app.state.engine).begin() as db:
        job=Job(id='export-test',project_id=qc.project,kind='QC_EXPORT',state='QUEUED',progress=0,payload={'review_ids':[reviewed['id']],'formats':['JSON'],'scope':'verified','name':'Test'})
        db.add(job)
    run(qc.root,'export-test')
    j=qc.client.get('/api/jobs/export-test').json();assert j['state']=='SUCCEEDED',j
    assert qc.client.get('/api/qc/exports/'+j['result']['artifact_id']+'/download').status_code==200


def test_unsampled_frame_review_without_name_exports_and_versions(qc):
    source=qc.analysis.payload
    with sessions(qc.app.state.engine).begin() as db:
        run=artifact(db,'QC_ANALYSIS',{**source,'rows':source['rows'][::2],'candidates':[]},qc.project)
    frame=qc.frames[1]
    body={'frame_id':frame.id,'start':frame.relative_seconds,'end':frame.relative_seconds}
    route=f'/api/qc/analysis/{run.id}/candidates'
    candidate=qc.client.post(route,json=body).json()
    assert qc.client.post(route,json=body).json()['id']==candidate['id']
    payload={'decision':'CORRECT','label':'NORMAL','start':frame.relative_seconds,'end':frame.relative_seconds,'frame_edits':{frame.id:[{'id':'manual','box':[10,20,40,90],'track_id':None}]},'boxes_verified':[frame.id]}
    response=qc.client.post(route+'/'+candidate['id']+'/decision',json=payload)
    assert response.status_code==201,response.text
    review=response.json();assert review['payload']['reviewer']=='Local QC'
    assert review['payload']['frame_samples'][0]['frame_id']==frame.id
    assert qc.client.post(route+'/'+candidate['id']+'/decision',json=payload).status_code==409
    with sessions(qc.app.state.engine)() as db:
        id,manifest=export_dataset(db,qc.root,{'review_ids':[review['id']],'formats':['YOLO','COCO'],'scope':'verified','name':'Frame test'},lambda *_:None)
    with zipfile.ZipFile(qc.root/'qc_exports'/(id+'.zip')) as z:
        assert f'labels/{frame.id}.txt' in z.namelist()
        assert len(json.loads(z.read('annotations.coco.json'))['images'])==1


def test_review_refresh_omits_immutable_rows_and_uses_batched_queries(qc):
    from sqlalchemy import event
    for candidate in ('a','b'):assert decision(qc,candidate).status_code==201
    queries=[]
    def count(_conn,_cursor,statement,*_args):queries.append(statement)
    event.listen(qc.app.state.engine,'before_cursor_execute',count)
    try:
        complete=qc.client.get(f'/api/qc/analysis/{qc.analysis.id}').json()
        queries.clear()
        compact=qc.client.get(f'/api/qc/analysis/{qc.analysis.id}?include_rows=false').json()
        assert 'rows' not in compact and compact['candidates']==complete['candidates']
        assert len(queries)<=4
    finally:event.remove(qc.app.state.engine,'before_cursor_execute',count)


def test_export_hashes_each_source_before_and_after_not_per_review(qc,monkeypatch):
    from backend.app.qc import worker
    reviews=[decision(qc,expected_version=i).json()['id'] for i in range(4)]
    full=[];original=worker.verify_source
    def checked(media,full_check=False):
        if full_check:full.append(media.id)
        return original(media,full_check)
    monkeypatch.setattr(worker,'verify_source',checked)
    with sessions(qc.app.state.engine)() as db:
        worker.export_dataset(db,qc.root,{'review_ids':reviews,'formats':['JSON'],'scope':'verified','name':'Hash test'},lambda *_:None)
    assert full==[qc.media,qc.media]


def test_head_review_export_and_mixed_type_guard(qc):
    with sessions(qc.app.state.engine).begin() as db:
        head=artifact(db,'QC_ANALYSIS',{**qc.analysis.payload,'settings':{**Settings().model_dump(),'detection_target':'head'}},qc.project)
    route=f'/api/qc/analysis/{head.id}/candidates/a/decision'
    body={'decision':'CORRECT','label':'NORMAL','start':0,'end':1,'frame_edits':{qc.frames[0].id:[{'id':'head-1','box':[10,20,40,50],'label_type':'head'}]},'boxes_verified':[qc.frames[0].id]}
    response=qc.client.post(route,json=body);assert response.status_code==201,response.text
    review=response.json();assert review['payload']['detection_target']=='head'
    with sessions(qc.app.state.engine)() as db:
        id,_=export_dataset(db,qc.root,{'review_ids':[review['id']],'formats':['YOLO','COCO','JSON'],'scope':'verified','name':'Heads'},lambda *_:None)
    with zipfile.ZipFile(qc.root/'qc_exports'/(id+'.zip')) as z:
        assert '0: head' in z.read('data.yaml').decode()
        assert json.loads(z.read('annotations.coco.json'))['categories']==[{'id':0,'name':'head'}]
        assert json.loads(z.read('events.json'))[0]['detection_target']=='head'
    assert decision(qc).status_code==201
    response=qc.client.post(f'/api/qc/projects/{qc.project}/exports',json={'formats':['YOLO']})
    assert response.status_code==422 and response.json()['code']=='MIXED_LABEL_TYPES'
    body['expected_version']=1;body['frame_edits'][qc.frames[0].id][0]['label_type']='person'
    assert qc.client.post(route,json=body).status_code==422

def test_proposal_metrics_missing_speed_and_duration():
    settings=Settings().model_dump();roi={'area_m2':1,'polygon':[[0,0],[100,0],[100,100],[0,100]]}
    obj={'id':'x','track_id':'1','box':[10,10,20,30]}
    m=metrics([obj],roi,settings,{},0,None)
    assert m['label']=='CROWDED' and m['speed'] is None and m['stationary'] is None
    rows=[{**m,'time':i*.5,'label':'CONGESTED','speed':0,'stationary':1} for i in range(4)]
    assert candidates(rows,settings,2)[0]['label']=='CROWDED'
    settings['min_duration']=1
    assert candidates(rows,settings,2)[0]['label']=='CONGESTED'


def test_profile_persists_input_resolution_and_rejects_invalid_sizes(qc):
    data={'name':'Higher resolution','media_id':qc.media,'rois':qc.profile['payload']['rois'],'settings':{'imgsz':960}}
    saved=qc.client.post('/api/qc/profiles',json=data)
    assert saved.status_code==201,saved.text
    assert saved.json()['payload']['settings']['imgsz']==960
    assert qc.profile['payload']['settings']['imgsz']==640
    for size in (0,641,1952):
        data['settings']['imgsz']=size
        assert qc.client.post('/api/qc/profiles',json=data).status_code==422
