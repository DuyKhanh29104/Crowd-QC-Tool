import math

import pytest

from backend.app.qc.analysis import calibration
from backend.app.qc.calibration_geometry import bird_eye_layout,quadrilateral_world,right_angle_world
from backend.app.workflows.motion import transform


WORLD=[[0,0],[5,0],[6,3],[1,4]]
PIXELS=[[50+50*x,50+50*y] for x,y in WORLD]


def lengths(world=WORLD):
    return {name:math.dist(world[a],world[b]) for name,a,b in
            [('ab',0,1),('bc',1,2),('cd',2,3),('da',3,0),('ac',0,2),('bd',1,3)]}


@pytest.mark.parametrize('world',[WORLD,[[0,0],[3,0],[2,4],[-2,2]],[[0,0],[3,0],[3,4],[0,4]]])
def test_reconstructs_skew_and_negative_x_without_right_angle_assumption(world):
    actual,check=quadrilateral_world(lengths(world))
    for a,b in zip(actual,world):assert a==pytest.approx(b)
    assert check['error_m']==pytest.approx(0,abs=1e-12)
    for key,value in lengths(actual).items():assert value==pytest.approx(lengths(world)[key])


@pytest.mark.parametrize('plane',['ground','head'])
def test_quad_homography_and_independent_check_for_both_planes(plane):
    result=calibration(PIXELS,shape='quadrilateral',lengths_m=lengths(),plane=plane,
        reference_height_m=1.65 if plane=='head' else None,
        test={'points':[[150,100],[200,100]],'expected_m':1})
    assert result['distance_verified'] and result['measurement_check']['verified']
    for pixel,world in zip(PIXELS,WORLD):assert transform(result['homography'],pixel)==pytest.approx(world,abs=1e-10)
    assert transform(result['homography'],[175,125])==pytest.approx([2.5,1.5])
    size,points=bird_eye_layout(result)
    assert size==(600,400)
    for a,b in zip(points,[[0,0],[500,0],[600,300],[100,400]]):assert a==pytest.approx(b)


@pytest.mark.parametrize('patch',[{'ab':0},{'ac':100},{'bd':9},{'da':.01},{'cd':math.nan}])
def test_impossible_or_inconsistent_measurements_are_rejected(patch):
    with pytest.raises(ValueError):quadrilateral_world({**lengths(),**patch})


def test_concave_world_and_missing_diagonal_are_rejected():
    with pytest.raises(ValueError):quadrilateral_world(lengths([[0,0],[5,0],[1,1],[0,4]]))
    data=lengths();data.pop('bd')
    with pytest.raises(ValueError):quadrilateral_world(data)


def test_consistency_does_not_replace_independent_distance_validation():
    result=calibration(PIXELS,shape='quadrilateral',lengths_m=lengths())
    assert result['measurement_check']['verified'] and not result['distance_verified']


def test_bird_eye_uniform_scale_for_tall_quad_and_legacy_rectangle():
    size,points=bird_eye_layout({'world_points':[[0,0],[1,0],[1,10],[0,10]]})
    assert size==(120,1200) and points[1]==[120,0]
    assert bird_eye_layout({'width_m':8,'height_m':5})==((600,375),[[0,0],[600,0],[600,375],[0,375]])


@pytest.mark.parametrize('world',[
    [[0,0],[5,0],[6,3],[0,4]],
    [[0,0],[5,0],[3,6],[0,4]],
    [[0,0],[3,0],[3,4],[0,4]],
])
def test_one_right_angle_reconstructs_from_only_four_sides(world):
    sides={k:v for k,v in lengths(world).items() if k in ('ab','bc','cd','da')}
    actual,construction=right_angle_world(sides)
    for a,b in zip(actual,world):assert a==pytest.approx(b)
    assert construction['right_angle_at']=='A'
    assert construction['angle_source']=='USER_DECLARED'
    assert construction['derived_diagonals_m']['bd']==pytest.approx(math.hypot(sides['ab'],sides['da']))
    assert construction['derived_diagonals_m']['ac']==pytest.approx(math.dist(world[0],world[2]))


@pytest.mark.parametrize('patch',[{'ab':0},{'bc':1,'cd':1},{'bc':10,'cd':3.7},{'da':math.inf}])
def test_right_angle_rejects_invalid_or_nonconvex_geometry(patch):
    with pytest.raises(ValueError):right_angle_world({'ab':5,'bc':math.sqrt(10),'cd':math.sqrt(37),'da':4,**patch})


@pytest.mark.parametrize('plane',['ground','head'])
def test_right_angle_calibration_does_not_force_the_other_corners_square(plane):
    world=[[0,0],[5,0],[6,3],[0,4]]
    # Perspective view: the physical right angle at A is visibly skewed in pixels.
    camera=[50,10,70,5,40,60,.02,.01,1]
    pixels=[transform(camera,p) for p in world]
    sides={k:v for k,v in lengths(world).items() if k in ('ab','bc','cd','da')}
    data=calibration(pixels,shape='right_angle',lengths_m=sides,plane=plane,
        reference_height_m=1.65 if plane=='head' else None,
        test={'points':[transform(camera,[2,1]),transform(camera,[3,1])],'expected_m':1})
    assert data['distance_verified'] and data['measurement_check'] is None
    for p,w in zip(pixels,world):assert transform(data['homography'],p)==pytest.approx(w,abs=1e-10)
    assert data['world_points'][2]==pytest.approx([6,3])
