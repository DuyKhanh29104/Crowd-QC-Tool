"""Paired engineering benchmark on an indexed video; never infer accuracy from counts."""
import argparse
from collections import Counter
import importlib.util
import io
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(values):
    return {'mean_ms': statistics.mean(values)*1000,
            'median_ms': statistics.median(values)*1000,
            'p95_ms': sorted(values)[min(len(values)-1, int(len(values)*.95))]*1000}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, default=ROOT/'7.mp4')
    parser.add_argument('--data-root', type=Path, default=ROOT/'workspace_data')
    parser.add_argument('--baseline', type=Path, default=ROOT/'.runtime/improvement-baseline')
    parser.add_argument('--output', type=Path, default=ROOT/'.runtime/benchmark-7.json')
    parser.add_argument('--skip-inference', action='store_true')
    args = parser.parse_args()
    from PIL import Image
    from sqlalchemy import select
    from backend.app.db.models import Media, Frame, WorkArtifact
    from backend.app.db.session import make_engine, sessions
    from backend.app.media import service
    from backend.app.qc.inference import ROIInput
    engine = make_engine(args.data_root)
    with sessions(engine)() as db:
        checksum = service.digest(args.video)
        media = db.scalar(select(Media).where(Media.sha256 == checksum))
        if media is None:
            raise SystemExit('Import this exact source into the app first (hash must match).')
        frames = list(db.scalars(select(Frame).where(Frame.media_id == media.id).order_by(Frame.ordinal)))
        runs = list(db.scalars(select(WorkArtifact).where(WorkArtifact.kind == 'QC_ANALYSIS').order_by(WorkArtifact.created_at.desc())))
        run = next(r.payload for r in runs if r.payload['media_id'] == media.id)
        reviewed = [r.payload for r in db.scalars(select(WorkArtifact).where(WorkArtifact.kind == 'QC_DECISION'))
                    if r.payload['media_id'] == media.id and r.payload.get('boxes_verified')]
    # Resolve the explicit input without modifying its persisted provenance.
    media.source_path = str(args.video.resolve())
    media.size_bytes = args.video.stat().st_size
    media.mtime_ns = str(args.video.stat().st_mtime_ns)
    report = {'video': str(args.video), 'source_sha256': checksum, 'frame_count': len(frames),
              'roi': run['roi'], 'human_box_review_records': len(reviewed),
              'limits': 'Counts and track lifetimes are diagnostics, not recall/precision or ID-switch metrics. No human GT for this video.',
              'accuracy': {'recall': None, 'precision': None, 'FN': None, 'FP': None, 'id_switches': None}}
    old_service = load_module(args.baseline/'service.py', 'baseline_service')
    # Compare decoded pixels, nearby steps, cold long jumps, and hot cache.
    patterns = {'nearby': list(range(30, 45)), 'long_jump': [0, len(frames)-1, 100, 500, 200, 600, 50, 400]}
    report['review'] = {}
    for name, module in [('before', old_service), ('after', service)]:
        report['review'][name] = {}
        for pattern, ordinals in patterns.items():
            values = []
            with tempfile.TemporaryDirectory() as folder:
                for ordinal in ordinals:
                    started = time.perf_counter()
                    data = module.frame_image(media, frames[ordinal], Path(folder), 0)
                    values.append(time.perf_counter()-started)
                    reference = old_service.frame_image(media, frames[ordinal], Path(folder), 0)
                    assert Image.open(io.BytesIO(data)).tobytes() == Image.open(io.BytesIO(reference)).tobytes()
                report['review'][name][pattern] = summary(values)
        with tempfile.TemporaryDirectory() as folder:
            module.frame_image(media, frames[100], Path(folder), 64*1024*1024)
            values = []
            for _ in range(15):
                started = time.perf_counter()
                module.frame_image(media, frames[100], Path(folder), 64*1024*1024)
                values.append(time.perf_counter()-started)
            report['review'][name]['cache_hit'] = summary(values)
    if not args.skip_inference:
        import torch
        from ultralytics import YOLO
        from backend.app.qc.decoding import sampled_images
        assert torch.cuda.is_available(), 'CUDA required'
        old_roi = load_module(args.baseline/'inference.py', 'baseline_roi').ROIInput
        # 5 Hz, sampled by source timestamps across the whole clip.
        selected = []; next_time = 0.
        for frame in frames:
            if frame.relative_seconds >= next_time-1e-8:
                selected.append(frame)
                next_time += .2
        model = YOLO(str(ROOT/'weights/yolo11n.pt'))
        image = next(sampled_images(media, selected[:1]))[1]
        model.predict(image, device=0, imgsz=640, verbose=False)
        report['gpu'] = torch.cuda.get_device_name(0)
        report['inference'] = {}
        variants = [('before', old_roi, {}), ('after_compatible', ROIInput, {}),
                    ('crop_640', ROIInput, {'roi_scale_mode': 'crop'}),
                    ('crop_960', ROIInput, {'roi_scale_mode': 'crop', 'imgsz': 960}),
                    ('crop_1280', ROIInput, {'roi_scale_mode': 'crop', 'imgsz': 1280}),
                    ('low_conf_tracking', ROIInput, {'track_use_low_conf': True}),
                    ('two_second_buffer', ROIInput, {'track_buffer_seconds': 2})]
        baseline_rows = None
        for name, cls, extra in variants:
            settings = {**run['settings'], 'imgsz': 640, 'confidence': .15,
                        'tracking': True, 'sampling_fps': 5, **extra}
            options = {'base_size': settings['imgsz']}
            if cls is ROIInput:
                options['settings'] = settings
            plan = cls(media.width, media.height, run['roi']['polygon'], **options)
            # Warm each tensor shape; use fresh tracker for the measured sequence.
            plan.infer(model, image, {**settings, 'tracking': False})
            elapsed = []; counts = []; tracks = Counter(); untracked = 0; rows = []
            torch.cuda.reset_peak_memory_stats()
            for frame, image, _ in sampled_images(media, selected):
                torch.cuda.synchronize(); started = time.perf_counter()
                objects = plan.infer(model, image, settings)
                torch.cuda.synchronize(); elapsed.append(time.perf_counter()-started)
                counts.append(len(objects))
                for obj in objects:
                    if obj['track_id'] is None: untracked += 1
                    else: tracks[obj['track_id']] += 1
                rows.append({'ordinal': frame.ordinal, 'pts': frame.pts, 'objects': objects})
            report['inference'][name] = {**summary(elapsed), 'fps': len(elapsed)/sum(elapsed),
                'samples': len(counts), 'detections': sum(counts), 'mean_count': statistics.mean(counts),
                'unique_ids': len(tracks), 'mean_observations_per_id': statistics.mean(tracks.values()) if tracks else None,
                'untracked_detections': untracked, 'peak_gpu_bytes': torch.cuda.max_memory_allocated(),
                'metadata': plan.metadata(), 'settings': settings}
            signature = [[(o['box'],o['score'],o['track_id']) for o in row['objects']] for row in rows]
            if name == 'before': baseline_rows = signature
            if name == 'after_compatible':
                assert signature == baseline_rows, 'Default inference geometry, confidence or identities changed'
                report['inference'][name]['identical_to_baseline'] = True
            args.output.with_name(args.output.stem+'-'+name+'-predictions.json').write_text(json.dumps(rows), encoding='utf-8')
            print(name, json.dumps(report['inference'][name]), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print('Report:', args.output)
    engine.dispose()


if __name__ == '__main__':
    main()
