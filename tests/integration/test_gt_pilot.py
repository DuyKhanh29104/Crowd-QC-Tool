import copy
import uuid
from sqlalchemy import select
from test_review import c,seed,save,approve,snap
from backend.app.db.models import GTObservation
from backend.app.db.session import sessions


def verified(c,index=0,complete=True):
    doc=seed(c,index)
    objects=copy.deepcopy(doc['revision']['objects'])
    for obj in objects:obj['anchor_verified']=True
    doc=save(c,doc,objects)
    response=approve(c,doc,count_complete=complete,anchors_complete=True,boxes_complete=True,reviewed_scope='FULL_FRAME')
    assert response.status_code==201,response.text
    return response.json()


def check(c,doc,count):
    return c.client.post(f'/api/pilot/annotations/{doc["annotation"]["id"]}/independent-check',json={'expected_edit_version':doc['annotation']['edit_version'],'reviewer':'DEMO_TEST_AUTOMATION','labels_hidden':True,'count_inside':count,'notes':'DEMO independent count fixture'})


def report(c):
    r=c.client.post(f'/api/pilot/runs/{c.run.id}/reports',json={'roi_version_id':c.roi['id'],'actor':'DEMO_TEST_AUTOMATION'})
    assert r.status_code==201,r.text
    return r.json()


def test_numeric_snapshot_coverage_and_stale_invalidation(c):
    doc=verified(c)
    assert doc['observation']['gt_eligible'] and doc['observation']['count']['value']==2
    assert doc['observation']['density']['value']==.5 and doc['observation']['level']['value'] is None
    snapshot=snap(c,doc).json()
    assert snapshot['manifest']['numeric_gt']=='COUNT_DENSITY_V1'
    assert snapshot['manifest']['entries'][0]['observation']['dependencies_hash']==doc['observation']['dependencies_hash']
    with sessions(c.app.state.engine)() as db:assert len(list(db.scalars(select(GTObservation))))==1
    snap(c,doc)
    with sessions(c.app.state.engine)() as db:assert len(list(db.scalars(select(GTObservation))))==1
    r=report(c)
    assert r['payload']['summary']['sampled_frames']==3 and r['payload']['summary']['count_valid_frames']==1
    assert r['payload']['summary']['excluded_or_incomplete_frames']==2
    assert r['payload']['summary']['full_frame_box_complete_frames']==1
    assert r['payload']['effort']['human_active_seconds'] is None
    changed=c.client.post(f'/api/rois/{c.roi["roi_id"]}/versions',json={k:c.roi[k] for k in ('name','polygon_source_px','area_evidence','evidence_type')}|{'area_m2':8,'base_version_id':c.roi['id']}).json()
    assert c.client.get(f'/api/review/annotations/{doc["annotation"]["id"]}').json()['observation']['gt_eligible'] is False
    assert snap(c,doc).status_code==409
    refreshed=save(c,doc,roi=changed['id']);refreshed=approve(c,refreshed,count_complete=True,anchors_complete=True).json()
    assert refreshed['observation']['density']['value']==.25 and refreshed['observation']['level']['value']==1
    assert c.client.get(f'/api/review/snapshots/{snapshot["id"]}').json()==snapshot
    assert c.client.get(f'/api/pilot/reports/{r["id"]}').json()==r


def test_draft_preview_uncertainty_and_no_false_gt(c):
    doc=seed(c);objects=copy.deepcopy(doc['revision']['objects']);objects[0]['anchor_verified']=True
    from test_review import body
    payload=body(doc,objects);payload.pop('roi_version_id');payload['count_complete']=True
    r=c.client.post(f'/api/review/annotations/{doc["annotation"]["id"]}/preview',json=payload).json()
    assert r['release_state']=='DRAFT' and not r['gt_eligible']
    assert r['count']['lower']==1 and r['count']['upper']==2 and r['count']['status']=='UNCERTAIN'
    doc=save(c,doc,objects);reviewed=approve(c,doc,count_complete=True).json()
    assert reviewed['observation']['count']['status']=='UNCERTAIN'
    frozen=snap(c,doc).json()
    assert frozen['manifest']['coverage']['count_uncertain']==1
    assert report(c)['payload']['summary']['count_uncertain_frames']==1


def test_independent_disagreement_adjudication_and_activity_idempotency(c):
    doc=verified(c);ann=doc['annotation']['id']
    activity={'id':uuid.uuid4().hex,'session_id':'demo','reviewer':'DEMO_TEST_AUTOMATION','seconds':2,'interactions':3,'source':'SIMULATED'}
    for _ in range(2):assert c.client.post(f'/api/pilot/annotations/{ann}/activity',json=activity).status_code==201
    assert c.client.post(f'/api/pilot/annotations/{ann}/activity',json={**activity,'seconds':3}).status_code==409
    assert c.client.post(f'/api/pilot/annotations/{ann}/activity',json={**activity,'id':'too-long','seconds':6}).status_code==422
    secondary=check(c,doc,3).json()
    assert secondary['payload']['verdict']=='DISAGREES'
    before=report(c)['payload']
    assert before['summary']['unresolved_disagreements']==1 and not before['summary']['independent_gate_passed']
    assert before['effort']['simulated_seconds']==2 and before['effort']['human_active_seconds'] is None
    resolution={'expected_edit_version':doc['annotation']['edit_version'],'reviewer':'DEMO_TEST_AUTOMATION','resolution':'PRIMARY_CONFIRMED','notes':'DEMO adjudication confirms two people'}
    assert c.client.post(f'/api/pilot/checks/{secondary["id"]}/adjudications',json={**resolution,'resolution':'CORRECTED'}).status_code==409
    assert c.client.post(f'/api/pilot/checks/{secondary["id"]}/adjudications',json=resolution).status_code==201
    after=report(c)['payload']
    assert after['summary']['unresolved_disagreements']==0 and after['summary']['independent_gate_passed']
    edited=save(c,doc,[]);edited=approve(c,edited,count_complete=True,anchors_complete=True).json()
    assert report(c)['payload']['summary']['unresolved_disagreements']==1
    resolution.update(expected_edit_version=edited['annotation']['edit_version'],resolution='CORRECTED',notes='DEMO correction to empty after reviewing source')
    assert c.client.post(f'/api/pilot/checks/{secondary["id"]}/adjudications',json=resolution).status_code==201
    assert report(c)['payload']['summary']['unresolved_disagreements']==0
    assert check(c,edited,0).json()['payload']['verdict']=='AGREES'
