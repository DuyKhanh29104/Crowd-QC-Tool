"""T05 bounded local video spike. Output is an unreviewed proposal, never GT."""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.metadata
import json
from pathlib import Path
import statistics
import time

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frame_identity(frame, stream_index, source_start):
    if frame.pts is None or frame.time_base is None:
        raise ValueError("MISSING_SOURCE_PTS: no frame-index/fps fallback allowed")
    timestamp = frame.pts * frame.time_base
    return {"stream_index": stream_index, "pts": frame.pts,
            "time_base": {"numerator": frame.time_base.numerator, "denominator": frame.time_base.denominator},
            "source_timestamp_seconds": float(timestamp),
            "seconds_from_stream_start": float(timestamp - source_start),
            "width": frame.width, "height": frame.height,
            "orientation": "coded_pixels_no_display_rotation"}


from backend.app.models.detector import proposal_objects


def run(args):
    import av
    import cv2
    import torch
    from ultralytics import YOLO

    if not args.source.is_file() or not args.weights.is_file():
        raise ValueError("SOURCE_OR_WEIGHTS_MISSING: explicit local files required")
    if args.device != "cpu" and not torch.cuda.is_available():
        raise ValueError("CUDA_UNAVAILABLE: choose --device cpu explicitly or install a compatible CUDA environment")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config["predict"]["batch"] != 1 or config["predict"]["classes"] != [0]:
        raise ValueError("This spike requires batch=1 and explicit person class 0")
    source_hash, weights_hash = sha256(args.source), sha256(args.weights)
    # Refuse reuse so reruns cannot overwrite evidence from a previous attempt.
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": "proposal_spike_v1", "state": "RUNNING", "annotation_state": "PROPOSAL",
                "evidence_type": args.evidence_type, "created_at": datetime.now(timezone.utc).isoformat(),
                "source_path": str(args.source.resolve()), "source_sha256": source_hash,
                "weights_path": str(args.weights.resolve()), "weights_sha256": weights_hash,
                "config_sha256": sha256(args.config), "config": config, "device": args.device,
                "sampling": {"start_seconds_from_stream_start": args.start_seconds, "every_seconds": args.every_seconds, "max_frames": args.max_frames},
                "packages": {name: importlib.metadata.version(name) for name in ("torch", "torchvision", "ultralytics", "av", "numpy", "opencv-python")},
                "ground_truth": False}
    partial = args.output / "proposals.jsonl.partial"
    try:
        model = YOLO(str(args.weights), task="detect")
        if model.names.get(0) != "person":
            raise ValueError("CLASS_MAPPING_MISMATCH: model class 0 must be person")
        manifest["model_class_mapping"] = model.names
        durations, decoded, emitted = [], 0, 0
        decode_seconds = overlay_seconds = warmup_seconds = 0.0
        next_time = Fraction(str(args.start_seconds))
        step = Fraction(str(args.every_seconds))
        use_cuda = args.device != "cpu"

        def synchronize():
            if use_cuda:
                torch.cuda.synchronize(args.device)

        def predict(image):
            return model.predict(source=image, device=args.device, **config["predict"])[0]

        if use_cuda:
            # is_available() does not initialize the CUDA allocator/context.
            torch.cuda.set_device(args.device)
            torch.cuda.init()
            torch.cuda.reset_peak_memory_stats(args.device)
        loop_start = time.perf_counter()
        with av.open(str(args.source)) as container, partial.open("w", encoding="utf-8") as output:
            stream = container.streams.video[args.video_stream]
            source_start = None if stream.start_time is None else stream.start_time * stream.time_base
            frames = iter(container.decode(stream))
            previous_pts_time = None
            while emitted < args.max_frames:
                decode_start = time.perf_counter()
                try:
                    frame = next(frames)
                except StopIteration:
                    break
                if frame.pts is None or frame.time_base is None:
                    raise ValueError("MISSING_SOURCE_PTS")
                stamp = frame.pts * frame.time_base
                if previous_pts_time is not None and stamp <= previous_pts_time:
                    raise ValueError("NON_MONOTONIC_SOURCE_PTS")
                previous_pts_time = stamp
                if source_start is None:
                    source_start = stamp
                identity = frame_identity(frame, stream.index, source_start)
                decoded += 1
                if stamp - source_start < next_time:
                    decode_seconds += time.perf_counter() - decode_start
                    continue
                image = frame.to_ndarray(format="bgr24")
                decode_seconds += time.perf_counter() - decode_start
                if emitted == 0:
                    warm_start = time.perf_counter()
                    for _ in range(config["warmup_iterations"]):
                        predict(image)
                    synchronize()
                    warmup_seconds = time.perf_counter() - warm_start
                    actual_device = str(next(model.model.parameters()).device)
                    if use_cuda and not actual_device.startswith("cuda"):
                        raise RuntimeError("GPU_DEVICE_MISMATCH: requested CUDA but model is not on CUDA")
                    manifest["actual_model_device"] = actual_device
                    manifest["gpu_name"] = torch.cuda.get_device_name(args.device) if use_cuda else None
                    manifest["cuda_runtime"] = torch.version.cuda
                synchronize()
                inference_start = time.perf_counter()
                result = predict(image)
                synchronize()
                durations.append(time.perf_counter() - inference_start)
                if tuple(result.orig_shape) != (frame.height, frame.width):
                    raise ValueError("SOURCE_GEOMETRY_MISMATCH")
                boxes = result.boxes.data.cpu().tolist()
                objects = proposal_objects(boxes, frame.width, frame.height)
                frame_id = f"{source_hash}:{stream.index}:{frame.pts}"
                for obj in objects:
                    obj["source_proposal_id"] = f"{frame_id}:{obj['object_id']}"
                record = {"frame_id": frame_id, **identity, "annotation_state": "PROPOSAL", "reviewed": False,
                          "objects": objects, "inference_seconds": durations[-1],
                          "coordinate_mapping": "Ultralytics Results.xyxy restored to orig_shape; no extra resize/crop"}
                output.write(json.dumps(record, allow_nan=False) + "\n")
                overlay_start = time.perf_counter()
                overlay = result.plot()
                cv2.putText(overlay, "PROPOSAL - NOT GT", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                overlay_name = f"frame_{emitted:04d}_pts_{frame.pts}.jpg"
                if not cv2.imwrite(str(args.output / overlay_name), overlay):
                    raise OSError("OVERLAY_WRITE_FAILED")
                overlay_seconds += time.perf_counter() - overlay_start
                emitted += 1
                while next_time <= stamp - source_start:
                    next_time += step
        if emitted == 0:
            raise ValueError("NO_SAMPLED_FRAMES")
        manifest.update({"state": "SUCCEEDED", "frames_decoded": decoded, "frames_emitted": emitted,
                         "warmup_seconds": warmup_seconds, "decode_seconds": decode_seconds,
                         "steady_inference_seconds": sum(durations), "median_inference_seconds": statistics.median(durations),
                         "decode_plus_steady_inference_seconds": decode_seconds + sum(durations),
                         "overlay_seconds": overlay_seconds, "loop_total_seconds": time.perf_counter() - loop_start,
                         "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(args.device) if use_cuda else None,
                         "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(args.device) if use_cuda else None,
                         "proposals_sha256": sha256(partial)})
        partial.rename(args.output / "proposals.jsonl")
    except BaseException as exc:
        manifest.update({"state": "FAILED", "error_type": type(exc).__name__, "error": str(exc)})
        raise
    finally:
        temporary = args.output / "manifest.json.partial"
        temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(args.output / "manifest.json")
    print(json.dumps({"output": str(args.output), "frames": emitted, "state": manifest["state"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New run directory; existing directory is rejected")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/model_baseline.json")
    parser.add_argument("--device", choices=["cpu", "cuda:0"], required=True)
    parser.add_argument("--evidence-type", choices=["DEMO", "PILOT"], required=True)
    parser.add_argument("--video-stream", type=int, default=0)
    parser.add_argument("--start-seconds", type=float, default=0)
    parser.add_argument("--every-seconds", type=float, default=1)
    parser.add_argument("--max-frames", type=int, default=40)
    args = parser.parse_args()
    import math
    if (not math.isfinite(args.every_seconds) or args.every_seconds <= 0 or
            not math.isfinite(args.start_seconds) or args.start_seconds < 0 or
            args.max_frames <= 0 or args.video_stream < 0):
        parser.error("sampling values must be finite; every-seconds/max-frames > 0; start-seconds/video-stream >= 0")
    from backend.app.jobs.profiles import gpu_lease
    lease = gpu_lease()
    if not lease.acquire():
        parser.error("GPU_RESOURCE_BUSY: another model job is running; retry after it finishes")
    try:
        run(args)
    finally:
        lease.release()


if __name__ == "__main__":
    main()
