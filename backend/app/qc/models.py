"""Fixed, versioned detector choices. Never substitute body boxes for heads."""
import json
from backend.app.jobs.profiles import ROOT
from backend.app.media.service import digest


def detector_spec(target='person',person_model_path=None):
    if target=='person':
        if person_model_path:
            return {'id':'custom_person','target':'person','weights':person_model_path,'class_id':0}
        return {'id':'yolo11n_coco','target':'person','weights':'weights/yolo11n.pt','class_id':0}
    if target=='head':return json.loads((ROOT/'configs/head_model.json').read_text(encoding='utf-8'))
    raise ValueError('INVALID_DETECTION_TARGET')


def detector_path(spec):
    path=ROOT/spec['weights']
    if not path.is_file():raise ValueError('MODEL_MISSING: '+spec['weights'])
    actual=digest(path)
    if spec.get('sha256') and spec['sha256']!=actual:raise ValueError('MODEL_HASH_MISMATCH')
    return path,actual


def detection_target(run):
    return run.get('detector',{}).get('target',run.get('settings',{}).get('detection_target','person'))
