"""Decode sampled PTS in one pass, without seek-per-frame or PNG disk cache."""
import time
import av
from backend.app.media.service import verify_source


def sampled_images(media, frames, heartbeat=lambda:None):
    if not frames:return
    targets=iter(frames);target=next(targets)
    if any(b.pts<=a.pts for a,b in zip(frames,frames[1:])):
        raise ValueError('SAMPLE_PTS_NOT_INCREASING')
    started=last_heartbeat=time.perf_counter()
    with av.open(str(verify_source(media))) as container:
        stream=container.streams[media.stream_index]
        container.seek(target.pts,backward=True,any_frame=False,stream=stream)
        for decoded in container.decode(stream):
            now=time.perf_counter()
            if now-last_heartbeat>=1:
                heartbeat();last_heartbeat=now
            if decoded.pts is None or decoded.pts<target.pts:continue
            if decoded.pts!=target.pts:raise ValueError('SAMPLED_FRAME_NOT_FOUND')
            if (decoded.width,decoded.height)!=(media.width,media.height):
                raise ValueError('SOURCE_GEOMETRY_MISMATCH')
            image=decoded.to_image().convert('RGB')
            yield target,image,time.perf_counter()-started
            target=next(targets,None)
            if target is None:return
            started=time.perf_counter()
        raise ValueError('SAMPLED_FRAME_NOT_FOUND')
