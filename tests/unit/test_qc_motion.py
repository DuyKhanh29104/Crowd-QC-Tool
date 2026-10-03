import copy

import pytest

from backend.app.qc.analysis import calibration, metrics
from backend.app.qc.api import Settings
from backend.app.qc.motion import motion_context, track_history


POLYGON = [[0,0],[200,0],[200,200],[0,200]]
ROI = {'polygon':POLYGON, 'area_m2':4}


def camera():
    data = dict(points=POLYGON, width_m=2, height_m=2,
                test={'points':[[20,20],[120,20]], 'expected_m':1})
    return {'calibration':calibration(**data),
            'head_calibration':calibration(**data,plane='head',reference_height_m=1.65)}


def box(x, track='one', y=20):
    return {'id':str(x), 'box':[x,y,x+20,y+40], 'track_id':track}


@pytest.mark.parametrize('target',['person','head'])
def test_both_planes_measure_vector_speed_with_source_time(target):
    settings={**Settings().model_dump(),'detection_target':target}
    context=motion_context(camera(),ROI,settings)
    first=[box(10)];metrics(first,ROI,settings,{},.1,motion=context)
    objects=[box(40,y=60)]
    row=metrics(objects,ROI,settings,track_history(first,.1),.6,motion=context)
    assert objects[0]['velocity_mps']==pytest.approx([.6,.8])
    assert objects[0]['speed']==pytest.approx(1)
    assert objects[0]['speed_px_s']==pytest.approx(100)
    assert row['speed']==pytest.approx(1) and row['speed_coverage']==1
    assert objects[0]['anchor']==[50,80 if target=='head' else 100]


def test_head_never_reuses_floor_calibration_and_pixels_do_not_classify_stationary():
    settings={**Settings().model_dump(),'detection_target':'head','density_threshold':.1}
    context=motion_context({'calibration':camera()['calibration']},ROI,settings)
    assert not context['metric_available'] and context['reason']=='HEAD_CALIBRATION_REQUIRED'
    objects=[box(10)]
    row=metrics(objects,ROI,settings,{'one':{'time':0,'anchor':[20,40]}},.5,motion=context)
    assert objects[0]['speed_px_s']==0
    assert row['speed'] is None and row['stationary'] is None and row['label']=='CROWDED'


@pytest.mark.parametrize('mutation,reason',[
    ('tracking','SPEED_OR_TRACKING_DISABLED'),('speed','SPEED_OR_TRACKING_DISABLED'),
    ('wrong_plane','CALIBRATION_PLANE_MISMATCH'),('unverified','CALIBRATION_DISTANCE_UNVERIFIED'),
    ('outside','ROI_OUTSIDE_CALIBRATION'),
])
def test_context_explains_unavailable_metric_speed(mutation,reason):
    cam=camera();settings={**Settings().model_dump(),'detection_target':'head'};roi=copy.deepcopy(ROI)
    if mutation in ('tracking','speed'):settings[mutation]=False
    elif mutation=='wrong_plane':cam['head_calibration']=cam['calibration']
    elif mutation=='unverified':cam['head_calibration']['distance_verified']=False
    else:roi['polygon'][1]=[201,0]
    result=motion_context(cam,roi,settings)
    assert result['reason']==reason and not result['metric_available']


@pytest.mark.parametrize('old,tid,reason',[
    (None,None,'TRACK_ID_REQUIRED'),(None,'one','TRACK_HISTORY_REQUIRED'),
    ({'time':0,'anchor':[20,40]},'one','TRACK_TIME_GAP'),
    ({'time':2,'anchor':[20,40]},'one','TRACK_TIME_GAP'),
    ({'time':1.5,'anchor':[-1,40]},'one','PREVIOUS_POINT_OUTSIDE_ROI'),
])
def test_missing_identity_gaps_and_roi_entry_do_not_fabricate_zero(old,tid,reason):
    settings=Settings().model_dump();objects=[box(30,tid)]
    row=metrics(objects,ROI,settings,{tid:old} if old else {},2,motion=motion_context(camera(),ROI,settings))
    assert objects[0]['speed'] is None and objects[0]['speed_px_s'] is None
    assert objects[0]['speed_reason']==reason and row['speed'] is None


def test_partial_population_and_duplicate_tracks_are_not_complete_speed():
    settings=Settings().model_dump();context=motion_context(camera(),ROI,settings)
    objects=[box(20),box(40,'new')]
    row=metrics(objects,ROI,settings,{'one':{'time':0,'anchor':[20,60]}},.5,motion=context)
    assert objects[0]['speed']==pytest.approx(.2)
    assert row['speed'] is None and row['stationary'] is None and row['speed_coverage']==.5
    objects=[box(20),box(40)]
    metrics(objects,ROI,settings,{'one':{'time':0,'anchor':[20,60]}},.5,motion=context)
    assert all(o['speed_reason']=='DUPLICATE_TRACK_ID' for o in objects)
    assert track_history(objects,.5)=={}


def test_calibration_requires_convex_support_and_independent_valid_distance():
    with pytest.raises(ValueError):calibration([[0,0],[200,0],[50,50],[0,200]],2,2)
    with pytest.raises(ValueError):calibration(POLYGON,2,2,plane='head')
    for points in ([[20,20],[20,20]],[[0,0],[20,20]],[[20,20],[201,20]]):
        with pytest.raises(ValueError):calibration(POLYGON,2,2,{'points':points,'expected_m':1})
