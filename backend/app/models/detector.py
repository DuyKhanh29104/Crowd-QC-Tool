"""Detector adapter. Ultralytics owns letterbox inversion; output is coded source pixels."""
import math

TRANSFORM = "ultralytics_orig_xyxy_coded_pixels_v1"


def proposal_objects(boxes, width, height):
    objects = []
    for index, (x1, y1, x2, y2, score, class_id) in enumerate(boxes):
        if class_id != 0:
            raise ValueError("CLASS_MAPPING_MISMATCH")
        if not all(math.isfinite(v) for v in (x1,y1,x2,y2,score)) or not (
                0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height and 0 <= score <= 1):
            raise ValueError("INVALID_SOURCE_BOX")
        objects.append({"object_id": f"person_{index}", "kind": "person_box",
            "geometry": {"xyxy_source_px": [x1,y1,x2,y2]}, "score": score,
            "class_id": 0, "origin": "model", "person_marker": [(x1+x2)/2,(y1+y2)/2],
            "ground_anchor": {"xy_source_px": [(x1+x2)/2,y2], "origin": "model_heuristic", "reviewed": False},
            "membership": "UNCERTAIN", "quality_flags": ["NOT_REVIEWED","GROUND_ANCHOR_UNVERIFIED"]})
    return objects


class PersonDetector:
    def __init__(self, weights, config, device):
        import torch
        from ultralytics import YOLO
        if device != "cpu" and not torch.cuda.is_available():
            raise RuntimeError("CUDA_UNAVAILABLE: không tự chuyển sang CPU")
        self.model = YOLO(str(weights), task="detect")
        if self.model.names.get(0) != "person":
            raise ValueError("CLASS_MAPPING_MISMATCH")
        self.config, self.device = config, device
        self.actual_device = None

    def predict(self, metadata, image):
        result = self.model.predict(source=image, device=self.device, **self.config["predict"])[0]
        self.actual_device = str(next(self.model.model.parameters()).device)
        if self.actual_device != self.device:
            raise RuntimeError("GPU_DEVICE_MISMATCH")
        if tuple(result.orig_shape) != (metadata["height"], metadata["width"]):
            raise ValueError("SOURCE_GEOMETRY_MISMATCH")
        # Results.boxes.xyxy already applies inverse letterboxing. Never rescale twice.
        return proposal_objects(result.boxes.data.cpu().tolist(), metadata["width"], metadata["height"])
