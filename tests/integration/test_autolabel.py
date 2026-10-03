import json
import threading
import time
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from backend.app.main import create_app
from backend.app.db.models import Job, ProposalRun, ProposalFrame
from backend.app.db.session import sessions
from backend.app.media.demo import make_video
from backend.app.jobs import api as job_api, profiles, worker
from backend.app.models.detector import proposal_objects


class FakeDetector:
    calls=0
    actual_device="cpu"
    def __init__(self,*args): pass
    def predict(self,metadata,image):
        FakeDetector.calls+=1
        assert image.size==(metadata["width"],metadata["height"])
        return [] if metadata["pts"]==1900 else proposal_objects([[20,30,100,150,.83,0]],*image.size)


@pytest.fixture
def context(tmp_path,monkeypatch):
    root=tmp_path/"db"
    app=create_app(root)
    client=TestClient(app)
    project=client.post('/api/projects',json={"name":"T08"}).json()['id']
    source=make_video(tmp_path/'vfr.mkv',[1000,1100,1370,1900,2300])
    j=client.post(f'/api/projects/{project}/media',json={"source_path":str(source),"camera_id":"demo"}).json()['job_id']
    for _ in range(400):
        result=client.get(f'/api/jobs/{j}').json()
        if result['state'] not in ('QUEUED','RUNNING'): break
        time.sleep(.025)
    assert result['state']=='SUCCEEDED'
    media=result['result']['media_id']
    weights=tmp_path/'test.pt'
    weights.write_bytes(b'FAKE_TEST_ONLY')
    original=profiles.profile
    monkeypatch.setattr(job_api,'profile',lambda id:{**original(id),'weights_path':str(weights)})
    monkeypatch.setattr(job_api.subprocess,'Popen',lambda *a,**kw:SimpleNamespace(poll=lambda:None))
    monkeypatch.setattr(profiles,'runtime_versions',lambda:{"test":"fake_detector"})
    monkeypatch.setattr(worker,'runtime_versions',lambda:{"test":"fake_detector"})
    monkeypatch.setenv('CROWD_QC_GPU_LOCK_ROOT',str(tmp_path/'locks'))
    FakeDetector.calls=0
    yield SimpleNamespace(root=root,app=app,client=client,media=media,weights=weights,source=source,project=project)
    client.close()
    app.state.engine.dispose()


def submit(c,**values):
    response=c.client.post(f'/api/media/{c.media}/auto-label',json={"profile_id":"person_cpu_v1","every_seconds":.3,**values})
    assert response.status_code==202,response.text
    return response.json()['job_id']


def results(c,id):
    job=c.client.get(f'/api/jobs/{id}').json()
    assert job['state']=='SUCCEEDED',job
    run=job['result']['proposal_run_id']
    return run,c.client.get(f'/api/proposal-runs/{run}/frames').json()['items']


def test_sampling_cache_roi_immutable_runs_and_empty_frames(context):
    c=context
    first=submit(c)
    assert c.client.get(f'/api/media/{c.media}/proposal-runs').json()==[]
    worker.run(c.root,first,FakeDetector)
    run,items=results(c,first)
    assert [i['pts'] for i in items]==[1000,1370,1900,2300]
    assert items[2]['objects']==[]
    assert all(i['annotation_state']=='PROPOSAL' and not i['reviewed'] and not i['ground_truth'] for i in items)
    assert FakeDetector.calls==4
    roi=c.client.post(f'/api/media/{c.media}/rois',json={"name":"ROI","polygon_source_px":[[0,0],[200,0],[200,200],[0,200]],"area_m2":20,"area_evidence":"DEMO"}).json()
    derived=c.client.get(f'/api/proposal-runs/{run}/frames?roi_version_id={roi["id"]}').json()['items'][0]
    assert derived['objects'][0]['roi_hint']=='INSIDE'
    assert derived['objects'][0]['membership']=='UNCERTAIN'
    second=submit(c)
    worker.run(c.root,second,FakeDetector)
    run2,items2=results(c,second)
    assert run2!=run and FakeDetector.calls==4
    assert items2[0]['objects'][0]['source_proposal_id']!=items[0]['objects'][0]['source_proposal_id']
    assert c.client.get(f'/api/proposal-runs/{run}/frames').json()['items']==items
    assert c.client.get(f'/api/jobs/{second}').json()['result']['cache_hits']==4
    worker.run(c.root,second,FakeDetector)
    assert len(c.client.get(f'/api/media/{c.media}/proposal-runs').json())==2
    # Corrupt a cache: recompute precisely one frame, never consume corrupted objects.
    next((c.root/'detector_cache').glob('*.json')).write_text('{}')
    third=submit(c)
    worker.run(c.root,third,FakeDetector)
    results(c,third)
    assert FakeDetector.calls==5
    assert not list((c.root/'proposal_staging').glob('*.partial'))


def test_cancel_queued_while_gpu_locked_retry_and_cancel_during_inference(context):
    c=context
    lock=profiles.gpu_lease()
    assert lock.acquire()
    id=submit(c)
    thread=threading.Thread(target=worker.run,args=(c.root,id,FakeDetector))
    thread.start()
    try:
        time.sleep(.1)
        assert c.client.get(f'/api/jobs/{id}').json()['state']=='QUEUED'
        assert c.client.post(f'/api/jobs/{id}/cancel',json={}).status_code==200
        thread.join(5)
        assert not thread.is_alive()
        assert c.client.get(f'/api/jobs/{id}').json()['state']=='CANCELLED'
        assert FakeDetector.calls==0
    finally: lock.release()
    retry=c.client.post(f'/api/jobs/{id}/retry',json={}).json()['job_id']
    assert retry!=id
    class CancelDetector(FakeDetector):
        def predict(self,*args):
            c.client.post(f'/api/jobs/{retry}/cancel',json={}).raise_for_status()
            return super().predict(*args)
    worker.run(c.root,retry,CancelDetector)
    assert c.client.get(f'/api/jobs/{retry}').json()['state']=='CANCELLED'
    assert c.client.get(f'/api/media/{c.media}/proposal-runs').json()==[]
    final=c.client.post(f'/api/jobs/{retry}/retry',json={}).json()['job_id']
    worker.run(c.root,final,FakeDetector)
    results(c,final)
    assert c.client.post(f'/api/jobs/{final}/cancel',json={}).status_code==409


def test_failure_and_source_or_weights_change_never_publish_partial(context):
    c=context
    id=submit(c)
    class BrokenDetector(FakeDetector):
        def predict(self,*args): raise RuntimeError('CUDA out of memory')
    worker.run(c.root,id,BrokenDetector)
    job=c.client.get(f'/api/jobs/{id}').json()
    assert job['state']=='FAILED' and job['error']['code']=='CUDA_OOM'
    next_id=submit(c)
    c.weights.write_bytes(b'changed weights')
    worker.run(c.root,next_id,FakeDetector)
    assert c.client.get(f'/api/jobs/{next_id}').json()['error']['code']=='WEIGHTS_HASH_MISMATCH'
    next_id=submit(c)
    c.source.write_bytes(b'changed source')
    worker.run(c.root,next_id,FakeDetector)
    assert c.client.get(f'/api/jobs/{next_id}').json()['error']['code']=='SOURCE_HASH_MISMATCH'
    assert c.client.get(f'/api/media/{c.media}/proposal-runs').json()==[]
    with sessions(c.app.state.engine)() as db:
        assert list(db.scalars(select(ProposalFrame)))==[]


def test_crash_recovery_and_invalid_scopes(context):
    c=context
    for bad in ({'end_seconds':99},{'every_seconds':0},{'start_seconds':1,'end_seconds':0},{'max_frames':5001},{'profile_id':'arbitrary'}):
        assert c.client.post(f'/api/media/{c.media}/auto-label',json=bad).status_code==422
    id=submit(c)
    with sessions(c.app.state.engine).begin() as db:
        db.execute(update(Job).where(Job.id==id).values(state='RUNNING'))
    # No worker lease: crashed run is interrupted and can never become a visible proposal.
    assert c.client.get(f'/api/jobs/{id}').json()['state']=='INTERRUPTED'
    retry=c.client.post(f'/api/jobs/{id}/retry',json={}).json()['job_id']
    worker.run(c.root,retry,FakeDetector)
    results(c,retry)


def test_adapter_rejects_invalid_geometry_and_keeps_raw_scores():
    objects=proposal_objects([[1,2,30,40,.123456,0]],640,360)
    assert objects[0]['score']==.123456
    assert objects[0]['ground_anchor']['xy_source_px']==[15.5,40]
    for box in ([1,2,3,4,.5,1],[-1,2,3,4,.5,0],[1,2,3,999,.5,0],[1,2,3,4,float('nan'),0]):
        with pytest.raises(ValueError): proposal_objects([box],640,360)


def test_two_workers_share_resource_and_never_overlap(context):
    c=context
    ids=[submit(c,max_frames=1),submit(c,max_frames=1,profile_id='person_gpu_v1')]
    active=0
    peak=0
    class SlowDetector(FakeDetector):
        def predict(self,*args):
            nonlocal active,peak
            resource=profiles.gpu_lease()
            assert not resource.acquire(), 'Inference must own the global resource lease'
            active+=1
            peak=max(peak,active)
            time.sleep(.15)
            output=super().predict(*args)
            active-=1
            return output
    threads=[threading.Thread(target=worker.run,args=(c.root,id,SlowDetector)) for id in ids]
    for t in threads: t.start()
    for t in threads: t.join(10)
    assert not any(t.is_alive() for t in threads)
    assert peak==1 and FakeDetector.calls==2
    for id in ids: results(c,id)
