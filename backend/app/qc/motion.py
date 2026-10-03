"""Image/metric displacement estimates with explicit plane and track provenance."""
from collections import Counter
import math

from backend.app.roi.geometry import membership
from backend.app.workflows.motion import transform


def motion_context(camera, roi, settings):
    head = settings.get('detection_target', 'person') == 'head'
    plane = 'head' if head else 'ground'
    cal = camera.get('head_calibration' if head else 'calibration')
    result = {
        'enabled': bool(settings.get('speed', True) and settings.get('tracking', True)),
        'plane': plane, 'anchor': 'HEAD_CENTER' if head else 'BOX_BOTTOM_CENTER',
        'method': 'PLANAR_DISPLACEMENT_OVER_SOURCE_PTS',
        'homography': None, 'support_polygon': None, 'metric_available': False,
        'reference_height_m': cal.get('reference_height_m') if cal else None,
        'reason': None, 'status': 'ESTIMATE_NOT_GT',
    }
    if not result['enabled']:
        result['reason'] = 'SPEED_OR_TRACKING_DISABLED'
    elif not cal:
        result['reason'] = 'HEAD_CALIBRATION_REQUIRED' if head else 'GROUND_CALIBRATION_REQUIRED'
    elif cal.get('plane', 'ground') != plane:
        result['reason'] = 'CALIBRATION_PLANE_MISMATCH'
    elif head and not (cal.get('reference_height_m') or 0) > 0:
        result['reason'] = 'HEAD_REFERENCE_HEIGHT_REQUIRED'
    elif not cal.get('distance_verified'):
        result['reason'] = 'CALIBRATION_DISTANCE_UNVERIFIED'
    elif any(membership(point, cal['points'], True) != 'INSIDE' for point in roi['polygon']):
        result['reason'] = 'ROI_OUTSIDE_CALIBRATION'
    else:
        result.update(homography=cal['homography'], support_polygon=cal['points'], metric_available=True)
    return result


def track_history(objects, stamp):
    """Never silently pair an ambiguous ID with whichever box happened to be last."""
    counts = Counter(o.get('track_id') for o in objects if o.get('track_id') is not None)
    return {o['track_id']: {'anchor': list(o['anchor']), 'time': stamp}
            for o in objects if o.get('track_id') is not None and counts[o['track_id']] == 1}


def measure_objects(objects, roi, settings, previous, stamp, context):
    head = settings.get('detection_target', 'person') == 'head'
    counts = Counter(o.get('track_id') for o in objects if o.get('track_id') is not None)
    for obj in objects:
        a, b, c, d = obj['box']
        anchor = [(a+c)/2, (b+d)/2 if head else d]
        obj.update(anchor=anchor, inside=membership(anchor, roi['polygon'], True) == 'INSIDE',
                   speed=None, velocity_mps=None, speed_px_s=None, velocity_px_s=None,
                   speed_reason=None)
        tid = obj.get('track_id')
        old = previous.get(tid) if tid is not None else None
        reason = None
        if not context['enabled']: reason = 'SPEED_OR_TRACKING_DISABLED'
        elif not obj['inside']: reason = 'OUTSIDE_ROI'
        elif tid is None: reason = 'TRACK_ID_REQUIRED'
        elif counts[tid] != 1: reason = 'DUPLICATE_TRACK_ID'
        elif old is None: reason = 'TRACK_HISTORY_REQUIRED'
        elif membership(old['anchor'], roi['polygon'], True) != 'INSIDE': reason = 'PREVIOUS_POINT_OUTSIDE_ROI'
        elif not 0 < stamp-old['time'] <= 2.1/settings['sampling_fps']: reason = 'TRACK_TIME_GAP'
        if reason:
            obj['speed_reason'] = reason
            continue
        dt = stamp-old['time']
        velocity = [(p-q)/dt for p, q in zip(anchor, old['anchor'])]
        obj.update(velocity_px_s=velocity, speed_px_s=math.hypot(*velocity))
        if not context['metric_available']:
            obj['speed_reason'] = context['reason']
            continue
        support = context.get('support_polygon')
        if support and any(membership(p, support, True) != 'INSIDE' for p in (anchor, old['anchor'])):
            obj['speed_reason'] = 'POINT_OUTSIDE_CALIBRATION'
            continue
        try:
            before, after = (transform(context['homography'], p) for p in (old['anchor'], anchor))
            velocity = [(p-q)/dt for p, q in zip(after, before)]
            if not all(math.isfinite(v) for v in velocity): raise ValueError('NONFINITE_PROJECTION')
            obj.update(velocity_mps=velocity, speed=math.hypot(*velocity))
        except (ValueError, ZeroDivisionError):
            obj['speed_reason'] = 'INVALID_PROJECTION'
