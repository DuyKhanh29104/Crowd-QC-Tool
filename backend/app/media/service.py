from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import uuid
import av
from backend.app.errors import DomainError
from backend.app.media.decoder import decoders

CACHE_LOCK = threading.Lock()


def trim_cache(cache_dir, budget):
    # Other worker processes can evict files despite our in-process lock.
    entries=[]
    for entry in cache_dir.glob("*.png"):
        try:
            stat=entry.stat()
        except FileNotFoundError:
            continue
        entries.append((stat.st_mtime_ns,stat.st_size,entry))
    total=sum(size for _,size,_ in entries)
    if total<=budget:return
    for _,size,entry in sorted(entries):
        if total<=budget: break
        try:
            entry.unlink(missing_ok=True)
        except PermissionError:
            # Windows may temporarily prevent eviction of a file being read.
            continue
        total-=size


def digest(path):
    value=hashlib.sha256()
    with Path(path).open("rb") as file:
        for block in iter(lambda:file.read(1024*1024),b""):
            value.update(block)
    return value.hexdigest()


def local_file(value):
    path=Path(value).expanduser().resolve()
    if not path.is_file():
        raise DomainError("SOURCE_MISSING","Không tìm thấy video nguồn. Chọn đường dẫn mới để kiểm hash.",409)
    return path


def verify_source(media, full=False):
    path=local_file(media.source_path)
    stat=path.stat()
    if full or stat.st_size!=media.size_bytes or str(stat.st_mtime_ns)!=media.mtime_ns:
        if digest(path)!=media.sha256:
            raise DomainError("SOURCE_HASH_MISMATCH","Video đã thay đổi; cache không được dùng. Hãy trỏ về đúng file gốc.",409)
    return path


def inspect_video(path, video_stream, on_frame=None):
    probe=subprocess.run(["ffprobe","-v","error","-show_format","-show_streams","-of","json",str(path)],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=60)
    if probe.returncode:
        raise DomainError("INVALID_MEDIA","ffprobe không đọc được video.",422,probe.stderr[-1000:])
    metadata=json.loads(probe.stdout)
    rows=[]
    frame_count=0
    first_pts=None
    with av.open(str(path)) as container:
        if video_stream>=len(container.streams.video):
            raise DomainError("VIDEO_STREAM_MISSING","Video stream không tồn tại.",422)
        stream=container.streams.video[video_stream]
        first=None
        previous=None
        width,height=stream.width,stream.height
        last_duration=Fraction(0)
        for ordinal,frame in enumerate(container.decode(stream)):
            if frame.pts is None or frame.time_base is None:
                raise DomainError("MISSING_SOURCE_PTS","Frame thiếu PTS; không suy từ FPS.",422)
            timestamp=frame.pts*frame.time_base
            if previous is not None and timestamp<=previous:
                raise DomainError("NON_MONOTONIC_PTS","PTS trùng hoặc không tăng.",422)
            if (frame.width,frame.height)!=(width,height) or frame.time_base!=stream.time_base:
                raise DomainError("GEOMETRY_TIMEBASE_CHANGED","Video đổi kích thước/timebase; cần tách đoạn có provenance.",422)
            if first is None:
                first=timestamp
                first_pts=frame.pts
            record={"ordinal":ordinal,"pts":frame.pts,"time_base_num":frame.time_base.numerator,"time_base_den":frame.time_base.denominator,"relative_seconds":float(timestamp-first)}
            if on_frame is None:
                rows.append(record)
            else:
                on_frame(record)
            frame_count+=1
            last_duration=Fraction(frame.duration or 0)*frame.time_base
            previous=timestamp
        if not frame_count:
            raise DomainError("EMPTY_VIDEO","Video không có frame giải mã được.",422)
        return {"width":width,"height":height,"stream_index":stream.index,
                "time_base_num":stream.time_base.numerator,"time_base_den":stream.time_base.denominator,
                "start_pts":first_pts,"duration_seconds":float(previous-first+last_duration),
                "frame_count":frame_count,"probe":metadata}, rows


def frame_image(media, frame, cache_dir, budget):
    path=verify_source(media)
    cache_dir=Path(cache_dir)
    cache_dir.mkdir(parents=True,exist_ok=True)
    cache=cache_dir/f"{media.sha256}_{media.stream_index}_{frame.pts}_coded_v1.png"
    with CACHE_LOCK:
        try:
            payload=cache.read_bytes()
        except FileNotFoundError:
            payload=None
        if payload is not None:
            try:
                # Unlike touch(), utime() cannot recreate an evicted empty PNG.
                os.utime(cache,None)
            except FileNotFoundError:
                pass
            trim_cache(cache_dir,budget)
            return payload
    image=decoders.image(path,media,frame)
    buffer=io.BytesIO()
    # Same lossless pixels; spend less CPU compressing interactive review frames.
    image.save(buffer,format="PNG",compress_level=1)
    payload=buffer.getvalue()
    with CACHE_LOCK:
        if len(payload)<=budget:
            tmp=cache.with_suffix(f".{uuid.uuid4().hex}.partial")
            try:
                tmp.write_bytes(payload)
                tmp.replace(cache)
            finally:
                tmp.unlink(missing_ok=True)
        trim_cache(cache_dir,budget)
    return payload
