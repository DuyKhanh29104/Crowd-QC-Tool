"""Paired CPU/CUDA engineering smoke on 40 DEMO frames, never an accuracy benchmark."""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=ROOT/'docs/evidence/gpu_benchmark.json')
    args=parser.parse_args()
    import av
    import cv2
    import torch
    import ultralytics
    import numpy as np
    if not torch.cuda.is_available():
        raise SystemExit('CUDA required; benchmark will not silently use CPU.')
    base=ROOT/'runs'/f'gpu_validation_{uuid.uuid4().hex[:12]}'
    base.mkdir(parents=True)
    source=base/'demo40.mkv'
    assets=Path(ultralytics.__file__).parent/'assets'
    images=[cv2.imread(str(assets/name)) for name in ['bus.jpg','zidane.jpg']]
    if any(image is None for image in images):
        raise RuntimeError('Bundled DEMO images missing')
    expected=[]
    pts=1000
    with av.open(str(source),'w') as container:
        stream=container.add_stream('ffv1',rate=10)
        stream.width,stream.height,stream.pix_fmt=640,480,'bgr0'
        stream.time_base=stream.codec_context.time_base=Fraction(1,1000)
        for index in range(40):
            pixels=np.zeros((480,640,3),dtype=np.uint8)
            if index%10!=9:
                original=images[index%2]
                scale=min(640/original.shape[1],480/original.shape[0])
                resized=cv2.resize(original,(round(original.shape[1]*scale),round(original.shape[0]*scale)))
                top=(480-resized.shape[0])//2
                left=(640-resized.shape[1])//2
                pixels[top:top+resized.shape[0],left:left+resized.shape[1]]=resized
            frame=av.VideoFrame.from_ndarray(pixels,format='bgr24')
            frame.pts,frame.time_base=pts,Fraction(1,1000)
            expected.append(pts)
            pts += [100,170,230][index%3]
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    results={}
    for device,label in [('cpu','cpu'),('cuda:0','gpu')]:
        destination=base/label
        command=[sys.executable,str(ROOT/'scripts/spike_autolabel.py'),'--source',str(source),'--weights',str(ROOT/'weights/yolo11n.pt'),'--output',str(destination),'--device',device,'--evidence-type','DEMO','--every-seconds','0.05','--max-frames','40']
        subprocess.run(command,check=True)
        manifest=json.loads((destination/'manifest.json').read_text(encoding='utf-8'))
        rows=[json.loads(row) for row in (destination/'proposals.jsonl').read_text().splitlines()]
        assert manifest['state']=='SUCCEEDED' and manifest['frames_emitted']==40
        assert [r['pts'] for r in rows]==expected
        assert all(r['reviewed'] is False and r['annotation_state']=='PROPOSAL' for r in rows)
        assert all(not r['objects'] for r in rows[9::10])
        assert any(r['objects'] for r in rows)
        if label=='gpu':
            assert manifest['actual_model_device'].startswith('cuda')
            assert manifest['peak_cuda_allocated_bytes']>0
        results[label]={'manifest':manifest,'object_counts':[len(r['objects']) for r in rows]}
    assert results['cpu']['manifest']['source_sha256']==results['gpu']['manifest']['source_sha256']
    assert results['cpu']['manifest']['config_sha256']==results['gpu']['manifest']['config_sha256']
    ratio=results['cpu']['manifest']['median_inference_seconds']/results['gpu']['manifest']['median_inference_seconds']
    report={'status':'PASSED','recorded_at':datetime.now(timezone.utc).isoformat(),
            'evidence_type':'DEMO_ENGINEERING_NOT_ACCURACY','run_directory':str(base.relative_to(ROOT)),
            'source_description':'40 frames: repeated padded bus/zidane demo images plus 4 blank frames; VFR, nonzero start',
            'limits':'Repeated public demo images, not 40 independent CCTV samples; no training, accuracy or quality claim.',
            'median_cpu_over_gpu_ratio':ratio,'results':results}
    args.report.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASSED','report':str(args.report),'median_cpu_over_gpu_ratio':ratio}))


if __name__=='__main__':
    main()
