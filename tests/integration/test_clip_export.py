import subprocess
from types import SimpleNamespace

import av
import pytest

from backend.app.jobs.worker import Cancelled
from backend.app.qc import worker


def test_clip_export_uses_absolute_stream_index_with_audio_first(tmp_path):
    source = tmp_path / 'audio_first.mkv'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=1', '-f', 'lavfi', '-i',
                    'color=c=red:s=64x64:r=12:d=1', '-map', '0:a', '-map', '1:v',
                    '-c:a', 'pcm_s16le', '-c:v', 'ffv1', str(source)],
                   check=True, capture_output=True, timeout=30,
                   creationflags=subprocess.CREATE_NO_WINDOW if worker.os.name == 'nt' else 0)
    with av.open(str(source)) as container:
        assert container.streams.video[0].index == 1
    clip = tmp_path / 'clip.mp4'
    worker.export_clip(SimpleNamespace(source_path=source, stream_index=1), 0, .5, clip, lambda: None)
    with av.open(str(clip)) as container:
        frames = list(container.decode(video=0))
        assert frames and frames[0].width == 64
        assert not container.streams.audio


@pytest.mark.parametrize('failure', ['cancel', 'timeout', 'encoder'])
def test_clip_failure_reaps_encoder_process(tmp_path, monkeypatch, failure):
    class Encoder:
        code = None
        terminated = False
        waited = False

        def wait(self, timeout=None):
            self.waited = True
            self.code = 1
            return self.code

        def poll(self):
            return self.code

        def terminate(self):
            self.terminated = True

    encoder = Encoder()
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *args, **kwargs: encoder)

    def checkpoint():
        if failure == 'cancel':
            raise Cancelled()

    expected = dict(cancel=Cancelled, timeout=subprocess.TimeoutExpired,
                    encoder=subprocess.CalledProcessError)[failure]
    with pytest.raises(expected):
        worker.export_clip(SimpleNamespace(source_path='test.mkv', stream_index=0), 0, 1,
                           tmp_path / 'clip.mp4', checkpoint, timeout=0 if failure == 'timeout' else 600)
    assert encoder.waited and encoder.code is not None
    assert encoder.terminated == (failure != 'encoder')
