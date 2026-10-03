from types import SimpleNamespace
import av
import pytest
from backend.app.media.decoder import DecoderPool
from backend.app.media.demo import make_video
from backend.app.media.service import digest


def test_persistent_decoder_vfr_seek_reopen_and_release(tmp_path,monkeypatch):
    path=make_video(tmp_path/'source.mkv',[1000,1100,1370,1500,2200])
    with av.open(str(path)) as container:
        expected=[(f.pts,f.to_image().tobytes(),f.width,f.height) for f in container.decode(video=0)]
    media=SimpleNamespace(sha256=digest(path),stream_index=0,width=expected[0][2],height=expected[0][3])
    original=av.open;opens=[]
    def tracked(*args,**kwargs):
        opens.append(args[0]);return original(*args,**kwargs)
    monkeypatch.setattr(av,'open',tracked)
    pool=DecoderPool(capacity=1,forward_frames=2)
    try:
        for ordinal in [0,1,2,4,1,0,4]:
            assert pool.image(path,media,SimpleNamespace(ordinal=ordinal,pts=expected[ordinal][0])).tobytes()==expected[ordinal][1]
        assert len(opens)==1
        # Persistent handles must not prevent rename/delete on Windows.
        moved=path.with_name('moved.mkv');path.rename(moved)
        assert pool.image(moved,media,SimpleNamespace(ordinal=0,pts=expected[0][0])).tobytes()==expected[0][1]
        assert len(opens)==2 and opens[0].closed
        with pytest.raises(Exception,match='Seek'):
            pool.image(moved,media,SimpleNamespace(ordinal=2,pts=1200))
        assert opens[-1].closed
    finally:pool.close()
    assert all(f.closed for f in opens)
