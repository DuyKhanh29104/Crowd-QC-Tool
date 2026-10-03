import io
import math

from PIL import Image
import pytest

from backend.app.db.session import sessions
from backend.app.workflows.store import artifact
from test_qc_workflow import qc


def quad_input(plane):
    return {'shape':'quadrilateral','plane':plane,'reference_height_m':1.65 if plane=='head' else None,
        'points':[[50,50],[300,50],[350,200],[100,250]],
        'lengths_m':{'ab':5,'bc':math.sqrt(10),'cd':math.sqrt(26),'da':math.sqrt(17),'ac':math.sqrt(45),'bd':math.sqrt(32)},
        'test':{'points':[[150,100],[200,100]],'expected_m':1}}


@pytest.mark.parametrize('target,plane',[('person','ground'),('head','head')])
def test_quad_profile_bird_eye_and_speed_through_api(qc,target,plane):
    cal=quad_input(plane)
    preview=qc.client.post('/api/qc/calibration-preview',json=cal)
    assert preview.status_code==200,preview.text
    assert preview.json()['distance_verified']
    roi={'id':'quad-roi','name':'Inside skew quad','area_m2':5,
         'polygon':[[120,90],[280,90],[280,180],[120,180]]}
    field='head_calibration' if target=='head' else 'calibration'
    saved=qc.client.post('/api/qc/profiles',json={'name':'Skew quad','media_id':qc.media,'rois':[roi],field:cal})
    assert saved.status_code==201,saved.text
    profile=saved.json()
    assert profile['payload'][field]['shape']=='quadrilateral'
    image_response=qc.client.get(f"/api/qc/media/{qc.media}/bird-eye/{profile['id']}?plane={plane}")
    assert image_response.status_code==200,image_response.text
    image=Image.open(io.BytesIO(image_response.content)).convert('RGB')
    assert image.size==(600,400) and image.getpixel((599,0))==(0,0,0)
    with sessions(qc.app.state.engine).begin() as db:
        run=artifact(db,'QC_ANALYSIS',{**qc.analysis.payload,'camera':profile['payload'],'roi':roi,
                     'settings':{**qc.analysis.payload['settings'],'detection_target':target}},qc.project)
    y1,y2=(80,100) if target=='person' else (90,110)
    body={'frames':[{'frame_id':qc.frames[i].id,'objects':[{'id':'box','box':[x,y1,x+20,y2],
                    'track_id':'one','label_type':target}]} for i,x in [(0,140),(5,165)]]}
    measured=qc.client.post(f'/api/qc/analysis/{run.id}/motion-preview',json=body)
    assert measured.status_code==200,measured.text
    assert measured.json()['rows'][1]['speed']==pytest.approx(1)


def test_quad_input_requires_all_lengths_and_rejects_inconsistent_bd(qc):
    data=quad_input('ground');data['lengths_m'].pop('bd')
    assert qc.client.post('/api/qc/calibration-preview',json=data).status_code==422
    data=quad_input('ground');data['lengths_m']['bd']=12
    result=qc.client.post('/api/qc/calibration-preview',json=data)
    assert result.status_code==422 and 'BD' in result.json()['message']


@pytest.mark.parametrize('target,plane',[('person','ground'),('head','head')])
def test_right_angle_profile_without_diagonals_preserves_metric_speed(qc,target,plane):
    cal={**quad_input(plane),'shape':'right_angle','points':[[50,50],[300,50],[350,200],[50,250]],
         'lengths_m':{'ab':5,'bc':math.sqrt(10),'cd':math.sqrt(37),'da':4}}
    preview=qc.client.post('/api/qc/calibration-preview',json=cal)
    assert preview.status_code==200,preview.text
    assert preview.json()['distance_verified']
    roi={'id':'right-roi','name':'One right angle','area_m2':5,'polygon':[[120,90],[280,90],[280,180],[120,180]]}
    field='head_calibration' if target=='head' else 'calibration'
    saved=qc.client.post('/api/qc/profiles',json={'name':'Four sides','media_id':qc.media,'rois':[roi],field:cal})
    assert saved.status_code==201,saved.text
    profile=saved.json();stored=profile['payload'][field]
    assert stored['construction']['right_angle_at']=='A' and set(stored['lengths_m'])=={'ab','bc','cd','da'}
    image=qc.client.get(f"/api/qc/media/{qc.media}/bird-eye/{profile['id']}?plane={plane}")
    assert image.status_code==200
    assert Image.open(io.BytesIO(image.content)).getpixel((599,0))==(0,0,0)
    with sessions(qc.app.state.engine).begin() as db:
        run=artifact(db,'QC_ANALYSIS',{**qc.analysis.payload,'camera':profile['payload'],'roi':roi,
            'settings':{**qc.analysis.payload['settings'],'detection_target':target}},qc.project)
    y1,y2=(80,100) if target=='person' else (90,110)
    body={'frames':[{'frame_id':qc.frames[i].id,'objects':[{'id':'box','box':[x,y1,x+20,y2],
        'track_id':'one','label_type':target}]} for i,x in [(0,140),(5,165)]]}
    result=qc.client.post(f'/api/qc/analysis/{run.id}/motion-preview',json=body)
    assert result.status_code==200,result.text
    assert result.json()['rows'][1]['speed']==pytest.approx(1)


def test_right_angle_api_rejects_missing_side_and_stale_diagonals(qc):
    cal={**quad_input('ground'),'shape':'right_angle'}
    assert qc.client.post('/api/qc/calibration-preview',json=cal).status_code==422
    cal['lengths_m']={'ab':5,'bc':math.sqrt(10),'da':4}
    assert qc.client.post('/api/qc/calibration-preview',json=cal).status_code==422
