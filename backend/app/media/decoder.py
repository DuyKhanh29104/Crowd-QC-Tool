"""Bounded persistent PyAV readers; exact source PTS is always authoritative."""
import atexit
from collections import OrderedDict
from pathlib import Path
import threading

import av
from backend.app.errors import DomainError
from backend.app.media.shared_file import open_shared_read


class DecoderPool:
    """Serialize reader access and close LRU handles before opening another source.

    Adjacent forward requests reuse the decode iterator. Reverse/long jumps seek
    to the preceding keyframe, including VFR and sources with a nonzero start PTS.
    No decoded images are retained here; the frame caches own their RAM budget.
    """

    def __init__(self, capacity: int = 2, forward_frames: int = 12):
        if capacity < 1 or forward_frames < 0:
            raise ValueError('Invalid decoder pool limits')
        self.capacity = capacity
        self.forward_frames = forward_frames
        self._readers = OrderedDict()
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            for reader in self._readers.values():
                self._close_reader(reader)
            self._readers.clear()

    @staticmethod
    def _close_reader(reader) -> None:
        try:
            reader['container'].close()
        finally:
            reader['source'].close()

    def image(self, path: Path, media, frame):
        stat = path.stat()
        key = (str(path.resolve()), media.sha256, stat.st_size, stat.st_mtime_ns, media.stream_index)
        with self._lock:
            reader = self._readers.pop(key, None)
            if reader is None:
                while len(self._readers) >= self.capacity:
                    self._close_reader(self._readers.popitem(last=False)[1])
                source = open_shared_read(path)
                try:
                    container = av.open(source)
                except Exception:
                    source.close()
                    raise
                reader = {'container': container, 'stream': container.streams[media.stream_index],
                          'source':source, 'ordinal': None, 'iterator': None, 'pts': None}
            self._readers[key] = reader
            try:
                previous = reader['ordinal']
                if (previous is None or not 0 < frame.ordinal-previous <= self.forward_frames
                        or reader['pts'] is None or frame.pts <= reader['pts']):
                    reader['container'].seek(frame.pts, backward=True, any_frame=False, stream=reader['stream'])
                    reader['iterator'] = reader['container'].decode(reader['stream'])
                for decoded in reader['iterator']:
                    if decoded.pts is None or decoded.pts < frame.pts:
                        continue
                    if decoded.pts != frame.pts:
                        break
                    if (decoded.width, decoded.height) != (media.width, media.height):
                        raise DomainError('SOURCE_GEOMETRY_MISMATCH', 'Kích thước frame khác metadata.', 409)
                    reader.update(ordinal=frame.ordinal, pts=frame.pts)
                    return decoded.to_image()
                raise DomainError('FRAME_NOT_FOUND', 'Seek không trả được đúng PTS đã lập chỉ mục.', 409)
            except Exception:
                self._close_reader(self._readers.pop(key))
                raise


decoders = DecoderPool()
atexit.register(decoders.close)
