from types import SimpleNamespace
import pytest
from PIL import Image
from backend.app.qc.inference import ROIInput
from backend.app.qc.analysis import metrics
from backend.app.qc.api import Settings


class Values:
    def __init__(self, value): self.value=value
    def cpu(self): return self
    def tolist(self): return self.value


def result(boxes, ids=None):
    class Boxes:
        xyxy=Values(boxes)
        conf=Values([.9]*len(boxes))
        id=Values(ids) if ids is not None else None
        def __len__(self):return len(boxes)
        def cpu(self):return self
        def numpy(self):return self
    return SimpleNamespace(boxes=Boxes(),orig_img=None)


@pytest.mark.parametrize('tracking',[False,True])
def test_model_receives_padded_crop_and_keeps_edge_person_only(tracking):
    image=Image.new('RGB',(1920,1080),(255,0,0))
    roi=ROIInput(1920,1080,[[600,200],[1200,200],[600,800]])
    roi.tracker=SimpleNamespace(update=lambda *_:[[100,0,180,230,7,.9,0,0]])
    class Spy:
        def call(self, source, **kwargs):
            assert source.size==(780,780)  # Padded crop, never full source.
            assert source.getpixel((10,10))==(255,0,0)
            assert source.getpixel((590,590))==(255,0,0)  # Context kept outside triangle.
            assert kwargs['imgsz']==(288,288) and kwargs['rect']
            assert 'persist' not in kwargs  # Always predict; tracking cannot prune boxes.
            return [result([[100,0,180,230],[10,10,40,40]],[7,8] if tracking else None)]
        predict=call
        track=call
    objects=roi.infer(Spy(),image,{'confidence':.15,'tracking':tracking})
    assert len(objects)==1 and objects[0]['box']==[610,80,690,310]
    assert objects[0]['track_id']==('7' if tracking else None)


def test_unconfirmed_detections_survive_tracking():
    roi=ROIInput(100,100,[[0,0],[100,0],[100,100],[0,100]])
    roi.tracker=SimpleNamespace(update=lambda *_:[[0,0,20,30,12,.9,0,0]])
    model=SimpleNamespace(predict=lambda *_args,**_kw:[result([[0,0,20,30],[40,40,60,90]])])
    objects=roi.infer(model,Image.new('RGB',(100,100)),{'confidence':.15,'tracking':True})
    assert len(objects)==2
    assert [o['track_id'] for o in objects]==['12',None]


def test_crop_restores_global_coordinates_for_speed_and_export():
    polygon=[[600,200],[1200,200],[1200,800],[600,800]]
    roi=ROIInput(1920,1080,polygon)
    objects=roi.objects(result([[90,120,190,220]],[3]))
    settings=Settings().model_dump()
    measured=metrics(objects,{'polygon':polygon,'area_m2':20},settings,
                     {'3':{'anchor':[640,300],'time':0}},.5,[.01,0,0,0,.01,0,0,0,1])
    assert objects[0]['anchor']==[650,300]
    assert measured['count']==1 and measured['speed']==pytest.approx(.2)
    assert objects[0]['box']==[600,200,700,300]


def test_full_frame_roi_and_tiny_roi_are_valid():
    image=Image.new('RGB',(1920,1080),(1,2,3))
    full=ROIInput(1920,1080,[[0,0],[1920,0],[1920,1080],[0,1080]])
    assert full.prepare(image).tobytes()==image.tobytes()
    assert full.imgsz==(384,640) and full.metadata()['effective_padding_px']==dict(left=0,right=0,top=0,bottom=0)
    tiny=ROIInput(1920,1080,[[10.2,20.2],[11.2,20.2],[11.2,21.2],[10.2,21.2]])
    assert tiny.crop_size==(4,4) and tiny.imgsz==(32,32)
    with pytest.raises(ValueError,match='ROI_FRAME_SIZE_MISMATCH'):
        tiny.prepare(Image.new('RGB',(10,10)))


def test_context_clips_at_video_edge_and_boundary_anchor_is_included():
    roi=ROIInput(1920,1080,[[0,0],[960,0],[960,540],[0,540]])
    assert roi.bounds==(0,0,1104,594)
    assert roi.metadata()['effective_padding_px']==dict(left=0,right=144,top=0,bottom=54)
    objects=roi.objects(result([[900,100,1020,540],[980,100,1080,540]],[1,2]))
    assert len(objects)==1 and objects[0]['box']==[900,100,1020,540]


def test_head_anchor_and_no_false_floor_speed():
    polygon=[[0,0],[100,0],[100,100],[0,100]]
    plan=ROIInput(200,200,polygon,target='head')
    # Head center is inside, bbox bottom outside; it is still a valid head ROI hit.
    objects=plan.objects(result([[20,80,40,110]],[1]))
    assert len(objects)==1 and objects[0]['label_type']=='head'
    settings={**Settings().model_dump(),'detection_target':'head'}
    row=metrics(objects,{'polygon':polygon,'area_m2':20},settings,{'1':{'anchor':[20,90],'time':0}},.5,[1,0,0,0,1,0,0,0,1])
    assert row['count']==1 and row['speed'] is None and row['stationary'] is None
    assert objects[0]['anchor']==[30,95]


@pytest.mark.parametrize('target',['person','head'])
@pytest.mark.parametrize('size,expected',[(640,(288,288)),(960,(416,416)),(1280,(544,544))])
def test_configured_input_size_reaches_model_without_changing_coordinates(target,size,expected):
    roi=ROIInput(1920,1080,[[600,200],[1200,200],[1200,800],[600,800]],base_size=size,target=target)
    def predict(source,**kwargs):
        assert kwargs['imgsz']==expected
        return [result([[90,120,190,220]])]
    objects=roi.infer(SimpleNamespace(predict=predict),Image.new('RGB',(1920,1080)),{'confidence':.15,'tracking':False})
    assert objects[0]['box']==[600,200,700,300]
    assert roi.metadata()['base_size']==size


@pytest.mark.parametrize('size',[0,319,641,1952])
def test_invalid_input_resolution_is_rejected(size):
    with pytest.raises(ValueError):Settings(imgsz=size)


def test_crop_scale_and_configurable_margin_preserve_source_coordinates():
    settings=Settings(roi_scale_mode='crop',imgsz=960,roi_margin_side=.1,
                      roi_margin_top=.1,roi_margin_bottom=.1,iou=.8,max_det=500).model_dump()
    roi=ROIInput(1920,1080,[[600,200],[1200,200],[1200,800],[600,800]],base_size=960,settings=settings)
    assert roi.bounds==(540,140,1260,860)
    assert roi.imgsz==(960,960)
    def predict(source,**kwargs):
        assert kwargs['iou']==.8 and kwargs['max_det']==500
        return [result([[60,60,160,160]])]
    objects=roi.infer(SimpleNamespace(predict=predict),Image.new('RGB',(1920,1080)),{**settings,'tracking':False})
    assert objects[0]['box']==[600,200,700,300]


def test_low_confidence_association_does_not_inflate_qc_count():
    settings=Settings(confidence=.35,track_use_low_conf=True).model_dump()
    roi=ROIInput(100,100,[[0,0],[100,0],[100,100],[0,100]])
    detections=result([[0,0,20,30],[40,40,60,90]])
    detections.boxes.conf=Values([.9,.15])
    def predict(source,**kwargs):
        assert kwargs['conf']==.1
        return [detections]
    def update(boxes,image):
        assert len(boxes)==2  # Low confidence still reaches the tracker.
        return [[0,0,20,30,12,.9,0,0],[40,40,60,90,13,.15,0,1]]
    roi.tracker=SimpleNamespace(update=update)
    objects=roi.infer(SimpleNamespace(predict=predict),Image.new('RGB',(100,100)),settings)
    assert len(objects)==1 and objects[0]['track_id']=='12'


@pytest.mark.parametrize('options',[{'track_low_thresh':.5},{'new_track_thresh':.1},{'roi_margin_side':-.1},{'max_det':0}])
def test_invalid_experimental_settings_are_rejected(options):
    with pytest.raises(ValueError):Settings(**options)
