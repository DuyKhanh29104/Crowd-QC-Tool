import json
import pytest
from backend.app.gt.rules import CONFIG_PATH
from backend.app.gt.observations import derive, possible_classes

CONFIG=json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
POLYGON=[[0,0],[10,0],[10,10],[0,10]]


def objects(inside=0,uncertain=0,outside=0):
    return [{"id":str(i),"anchor":[5,5] if i<inside+uncertain else [20,20],"anchor_verified":not(inside<=i<inside+uncertain)} for i in range(inside+uncertain+outside)]


@pytest.mark.parametrize('count,speed,level',[(1,0,1),(1,.1,1),(1,1.5,1),(2,0,3),(2,.1,2),(2,1.5,1),(4,0,3),(4,.1,3),(4,1.5,2)])
def test_nine_cells_and_speed_boundaries(count,speed,level):
    result=derive(objects(count),POLYGON,4,CONFIG,True,speed)
    assert result['level']['value']==level
    assert result['count']['value']==count


def test_membership_interval_empty_and_unbounded_population():
    result=derive(objects(10,2,3),POLYGON,20,CONFIG,True)
    assert result['count']['value'] is None and result['count']['status']=='UNCERTAIN'
    assert (result['count']['lower'],result['count']['upper'])==(10,12)
    assert (result['density']['lower'],result['density']['upper'])==(.5,.6)
    assert result['level']['possible_levels']==[1,2,3]
    assert result['speed']['value'] is None
    incomplete=derive(objects(10,2),POLYGON,20,CONFIG,False)
    assert incomplete['count']['upper'] is None and incomplete['count']['status']=='UNKNOWN'
    empty_proposal=derive([],POLYGON,20,CONFIG,False)
    assert empty_proposal['count']['value'] is None and empty_proposal['level']['value'] is None
    empty_reviewed=derive([],POLYGON,20,CONFIG,True)
    assert empty_reviewed['count']['value']==0 and empty_reviewed['density']['value']==0
    assert empty_reviewed['speed']['value'] is None and empty_reviewed['level']['value']==1


def test_sparse_uncertainty_still_implies_level_one_and_no_rounding():
    result=derive(objects(1,1),POLYGON,10,CONFIG,True)
    assert result['density']['status']=='UNCERTAIN' and result['density']['possible_classes']==['LOW']
    assert result['level']['value']==1 and result['speed']['status']=='UNKNOWN'
    below=derive(objects(1),POLYGON,2.000000000000001,CONFIG,True)
    exact=derive(objects(1),POLYGON,2,CONFIG,True)
    assert below['density']['class']=='LOW' and exact['density']['class']=='MEDIUM'
    above=derive(objects(1),POLYGON,.9999999999999999,CONFIG,True)
    assert above['density']['class']=='HIGH'


@pytest.mark.parametrize('area,reason',[(0,'AREA_NOT_POSITIVE'),(-1,'AREA_NOT_POSITIVE'),(None,'MISSING_VALUE'),(float('nan'),'NON_FINITE_VALUE'),(1e-320,'NUMERIC_OVERFLOW')])
def test_invalid_area_never_produces_density(area,reason):
    result=derive(objects(1),POLYGON,area,CONFIG,True)
    assert result['density']['value'] is None and result['density']['reason']==reason


def test_exact_boundary_and_invalid_speed():
    result=derive([{'id':'edge','anchor':[0,5],'anchor_verified':True}],POLYGON,10,CONFIG,True)
    assert result['count']['value']==1
    result=derive(objects(1),POLYGON,10,CONFIG,True,-.1)
    assert result['speed']['status']=='INVALID' and result['level']['status']=='INVALID'
