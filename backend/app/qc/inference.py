"""Detect in a padded ROI crop; count only anchors in the original polygon."""
import math
import uuid
from backend.app.roi.geometry import membership, validate_polygon


class ROIInput:
    def __init__(self, width, height, polygon, base_size=640, stride=32, target='person', settings=None):
        validate_polygon(polygon, width, height)
        self.size = (width, height)
        self.polygon = polygon
        self.tracker = None
        self.target = target
        self.base_size = base_size
        settings = settings or {}
        self.scale_mode = settings.get('roi_scale_mode','source')
        self.padding = {'left':settings.get('roi_margin_side',.15),
                        'right':settings.get('roi_margin_side',.15),
                        'top':settings.get('roi_margin_top',.20),
                        'bottom':settings.get('roi_margin_bottom',.10)}
        self.tracking_metadata = {}
        self.recovered = 0
        left, top = math.floor(min(p[0] for p in polygon)), math.floor(min(p[1] for p in polygon))
        right, bottom = math.ceil(max(p[0] for p in polygon)), math.ceil(max(p[1] for p in polygon))
        self.roi_bounds = (left, top, right, bottom)
        # Fixed per run, so ByteTrack sees a stable coordinate system. Keep more
        # context above the floor ROI for heads/upper bodies; clip to source edges.
        side = math.ceil((right-left)*self.padding['left'])
        above, below = math.ceil((bottom-top)*self.padding['top']), math.ceil((bottom-top)*self.padding['bottom'])
        self.bounds = (max(0,left-side), max(0,top-above),
                       min(width,right+side), min(height,bottom+below))
        x, y, right, bottom = self.bounds
        self.crop_size = (right-x, bottom-y)
        # Preserve at least the old full-frame object scale, rounding up to stride.
        # Do not enlarge every small crop to base_size: that defeats the speed gain.
        scale = base_size / max(self.crop_size if self.scale_mode=='crop' else self.size)
        self.imgsz = tuple(max(stride, math.ceil(v*scale/stride)*stride)
                           for v in (bottom-y, right-x))

    def prepare(self, image):
        if image.size != self.size:
            raise ValueError('ROI_FRAME_SIZE_MISMATCH')
        # Preserve context at diagonal/concave edges too, not just bbox edges.
        return image.crop(self.bounds).convert('RGB')

    def objects(self, result, track_ids=None, min_conf=0):
        x, y, right, bottom = self.bounds
        boxes = result.boxes.xyxy.cpu().tolist()
        scores = result.boxes.conf.cpu().tolist()
        ids = track_ids if track_ids is not None else (result.boxes.id.cpu().tolist() if result.boxes.id is not None else [None]*len(boxes))
        objects = []
        for local, score, tid in zip(boxes, scores, ids):
            if score < min_conf:
                continue
            a,b,c,d = local
            box = [max(x,min(right,a+x)), max(y,min(bottom,b+y)),
                   max(x,min(right,c+x)), max(y,min(bottom,d+y))]
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            # Context detections support tracking but never enter ROI counts/GT.
            anchor=[(box[0]+box[2])/2,(box[1]+box[3])/2 if self.target=='head' else box[3]]
            if membership(anchor,self.polygon,True) != 'INSIDE':
                continue
            objects.append({'id':uuid.uuid4().hex,'box':box,'score':score,'label_type':self.target,
                            'track_id':str(int(tid)) if tid is not None else None})
        return objects

    def infer(self, model, image, settings):
        cropped = self.prepare(image)
        # Lower scores can maintain an existing track without becoming QC boxes.
        confidence = settings['confidence']
        tracker_conf = min(confidence,settings.get('track_low_thresh',.1)) if settings['tracking'] and settings.get('track_use_low_conf',False) else confidence
        kwargs = dict(device=0,imgsz=self.imgsz,rect=True,classes=[0],
                      conf=tracker_conf,iou=settings.get('iou',.7),
                      max_det=settings.get('max_det',300),verbose=False)
        result = model.predict(cropped,**kwargs)[0]
        ids = None
        if settings['tracking']:
            if self.tracker is None:
                from ultralytics.trackers.byte_tracker import BYTETracker
                from ultralytics.utils import YAML, IterableSimpleNamespace
                from ultralytics.utils.checks import check_yaml
                config = YAML.load(check_yaml('bytetrack.yaml'))
                for key in ('track_high_thresh','track_low_thresh','new_track_thresh','match_thresh'):
                    if key in settings:config[key] = settings[key]
                seconds = settings.get('track_buffer_seconds')
                if seconds is not None:
                    config['track_buffer'] = max(1,math.ceil(seconds*settings['sampling_fps']))
                self.tracker = BYTETracker(IterableSimpleNamespace(**config))
                self.tracking_metadata = {**config,'effective_buffer_seconds':config['track_buffer']/settings.get('sampling_fps',2)}
            lost_ids = {t.track_id for t in getattr(self.tracker,'lost_stracks',[])}
            tracks = self.tracker.update(result.boxes.cpu().numpy(),result.orig_img)
            # Ultralytics track() replaces detections with confirmed tracks.
            # QC must keep unconfirmed/low-confidence detections too; an unknown
            # track ID means speed is unavailable, not that the person vanished.
            ids = [None]*len(result.boxes)
            for track in tracks:
                index = int(track[-1])
                if 0 <= index < len(ids):ids[index] = int(track[4])
            self.recovered += sum(tid in lost_ids for tid in ids if tid is not None)
        return self.objects(result,ids,min_conf=confidence)

    def metadata(self):
        left,top,right,bottom = self.roi_bounds
        x,y,r,b = self.bounds
        return {'mode':'ROI_CROP_CONTEXT','crop_xyxy':list(self.bounds),
                'roi_bbox_xyxy':list(self.roi_bounds),
                'padding_ratios':self.padding,
                'effective_padding_px':{'left':left-x,'right':r-right,'top':top-y,'bottom':b-bottom},
                'crop_size':list(self.crop_size),'imgsz_hw':list(self.imgsz),
                'polygon_masked':False,'source_size':list(self.size),
                'detection_policy':'KEEP_ALL_DETECTIONS_ATTACH_CONFIRMED_TRACK_IDS',
                'base_size':self.base_size,
                'scale_policy':f'{"ROI_CROP" if self.scale_mode=="crop" else "FULL_FRAME"}_{self.base_size}_SCALE_STRIDE_ROUND_UP',
                'tracker':self.tracking_metadata,'recovered_track_observations':self.recovered,
                'boundary_policy':'CONTEXT_CROP_ORIGINAL_ROI_HEAD_CENTER' if self.target=='head' else 'CONTEXT_CROP_ORIGINAL_ROI_BOTTOM_CENTER'}
