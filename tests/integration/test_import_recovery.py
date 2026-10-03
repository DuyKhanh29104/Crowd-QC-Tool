import subprocess
import sys
import time
import uuid
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.db.models import Job
from backend.app.db.session import sessions
from backend.app.media.demo import make_video
from backend.app.media.importer import run
from backend.app.media.service import inspect_video


def test_worker_lease_survives_api_restart_then_detects_crash_and_retries(tmp_path):
    root=tmp_path/'data'
    source=make_video(tmp_path/'source.mkv',[1000,1100,1370])
    app=create_app(root)
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'Recovery'}).json()['id']
    id=uuid.uuid4().hex
    payload={'source_path':str(source),'camera_id':'demo','evidence_type':'DEMO','video_stream':0,'parent_media_id':None,'source_offset_seconds':0}
    with sessions(app.state.engine).begin() as db:
        db.add(Job(id=id,project_id=project,kind='MEDIA_IMPORT',state='RUNNING',payload=payload,progress=0))
    # Separate fixture process holds the same OS lease as the production importer.
    ready=tmp_path/'ready'
    code="from backend.app.media.lease import WorkerLease; from pathlib import Path; import sys,time; lease=WorkerLease(sys.argv[1],sys.argv[2]); assert lease.acquire(); Path(sys.argv[3]).write_text('ready'); time.sleep(30)"
    process=subprocess.Popen([sys.executable,'-c',code,str(root),id,str(ready)])
    try:
        deadline=time.monotonic()+10
        while not ready.exists() and time.monotonic()<deadline: time.sleep(.02)
        assert ready.exists()
        with TestClient(create_app(root)) as restarted:
            assert restarted.get(f'/api/jobs/{id}').json()['state']=='RUNNING'
            assert restarted.post(f'/api/jobs/{id}/retry',json={}).status_code==409
        process.terminate()
        process.wait(timeout=10)
        with TestClient(create_app(root)) as restarted:
            state=restarted.get(f'/api/projects/{project}/jobs').json()[0]
            assert state['state']=='INTERRUPTED' and state['error']['retryable']
            assert restarted.get(f'/api/projects/{project}/media').json()==[]
            retry=restarted.post(f'/api/jobs/{id}/retry',json={})
            assert retry.status_code==202
            retry_id=retry.json()['job_id']
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                result=restarted.get(f'/api/jobs/{retry_id}').json()
                if result['state'] not in ('QUEUED','RUNNING'):break
                time.sleep(.05)
            assert result['state']=='SUCCEEDED',result
            run(root,retry_id)  # A second invocation cannot duplicate completed media.
            assert len(restarted.get(f'/api/projects/{project}/media').json())==1
            assert restarted.get(f'/api/jobs/{id}').json()['state']=='INTERRUPTED'
            assert not list((root/'import_staging').glob('*.partial'))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_streamed_index_and_bulk_import(tmp_path):
    source=tmp_path/'1200_frames.mkv'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc=size=64x64:rate=100','-frames:v','1200','-c:v','ffv1',str(source)],check=True)
    count=0
    def consume(frame):
        nonlocal count
        assert frame['ordinal']==count
        count+=1
    metadata,rows=inspect_video(source,0,on_frame=consume)
    assert count==1200 and metadata['frame_count']==1200 and rows==[]
    app=create_app(tmp_path/'db')
    with TestClient(app) as client:
        project=client.post('/api/projects',json={'name':'Bulk index'}).json()['id']
        response=client.post(f'/api/projects/{project}/media',json={'source_path':str(source),'camera_id':'demo'})
        id=response.json()['job_id']
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            job=client.get(f'/api/jobs/{id}').json()
            if job['state'] not in ('QUEUED','RUNNING'):break
            time.sleep(.05)
        assert job['state']=='SUCCEEDED',job
        media=job['result']['media_id']
        last=client.get(f'/api/media/{media}/frames?offset=1199&limit=1').json()
        assert last['total']==1200 and last['items'][0]['ordinal']==1199
