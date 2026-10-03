import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from backend.app.models.detector import TRANSFORM
from backend.app.media.lease import WorkerLease

ROOT = Path(__file__).resolve().parents[3]


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",",":"), allow_nan=False).encode()).hexdigest()


def profile(profile_id):
    devices = {"person_gpu_v1": "cuda:0", "person_cpu_v1": "cpu"}
    if profile_id not in devices:
        raise ValueError("UNKNOWN_MODEL_PROFILE")
    return {"id": profile_id, "device": devices[profile_id],
            "weights_path": str(ROOT/"weights/yolo11n.pt"),
            "config": json.loads((ROOT/"configs/model_baseline.json").read_text(encoding="utf-8")),
            "transform": TRANSFORM}


def gpu_lease():
    # Repository-wide, shared across data roots, API workers and the legacy CLI.
    return WorkerLease(Path(os.environ.get("CROWD_QC_GPU_LOCK_ROOT", ROOT/".runtime")), "gpu_resource")


def runtime_versions():
    return {n: importlib.metadata.version(n) for n in ("torch","torchvision","ultralytics","av","numpy","opencv-python")}


def cache_key(media, frame, payload):
    return json_hash({"media_hash": media.sha256, "stream": media.stream_index,
        "pts": frame.pts, "time_base": [frame.time_base_num, frame.time_base_den],
        "width": media.width, "height": media.height, "weights": payload["weights_sha256"],
        "config": payload["config_sha256"], "transform": payload["profile"]["transform"],
        "device": payload["profile"]["device"], "runtime": runtime_versions()})
