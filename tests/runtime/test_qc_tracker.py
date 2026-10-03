"""Run with the GPU/model environment: python -m unittest discover -s tests/runtime."""
from types import SimpleNamespace
import unittest

from PIL import Image
try:
    import numpy as np
    import torch
    from ultralytics.engine.results import Results
except ImportError as exc:
    raise unittest.SkipTest('Run tracker runtime checks in .venv-gpu') from exc
from backend.app.qc.inference import ROIInput


class TrackerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.image=Image.new('RGB',(100,100))
        self.settings={'confidence':.35,'tracking':True,'sampling_fps':5,'track_buffer_seconds':2}
        self.roi=ROIInput(100,100,[[0,0],[100,0],[100,100],[0,100]])

    def infer(self, score=None):
        data=np.array([[30,10,60,80,score,0]] if score is not None else [],dtype=np.float32).reshape(-1,6)
        result=Results(np.zeros((100,100,3),dtype=np.uint8),'test', {0:'person'},boxes=torch.from_numpy(data))
        return self.roi.infer(SimpleNamespace(predict=lambda *_args,**_kwargs:[result]),self.image,self.settings)

    def test_missing_detection_recovers_existing_id(self):
        first=self.infer(.9)[0]['track_id']
        self.assertIsNotNone(first)
        self.infer(.9)
        self.assertEqual(self.infer(),[])
        self.assertEqual(self.infer(.9)[0]['track_id'],first)
        self.assertEqual(self.roi.recovered,1)
        self.assertEqual(self.roi.tracker.max_frames_lost,10)

    def test_low_confidence_keeps_track_without_becoming_label(self):
        self.settings['track_use_low_conf']=True
        first=self.infer(.9)[0]['track_id']
        self.assertEqual(self.infer(.15),[])
        self.assertEqual(len(self.roi.tracker.lost_stracks),0)
        self.assertEqual(self.infer(.9)[0]['track_id'],first)

    def test_sampling_rate_controls_lifetime(self):
        self.settings['sampling_fps']=10
        self.infer(.9)
        self.assertEqual(self.roi.tracker.max_frames_lost,20)


if __name__=='__main__':unittest.main()
