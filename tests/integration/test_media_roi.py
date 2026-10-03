import io
import json
from pathlib import Path
import time
import subprocess
import av
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from backend.app.main import create_app
from backend.app.media.demo import make_video


def finish(client,job_id):
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        job=client.get(f"/api/jobs/{job_id}").json()
        if job["state"] not in ("QUEUED","RUNNING"): return job
        time.sleep(.05)
    pytest.fail("Import job timeout")


def import_video(client,project,path,**extra):
    result=client.post(f"/api/projects/{project}/media",json={"source_path":str(path),"camera_id":"test_camera","evidence_type":"DEMO",**extra})
    assert result.status_code==202,result.text
    return finish(client,result.json()["job_id"])


def test_cfr_vfr_seek_cache_remap_and_restart(tmp_path):
    root=tmp_path/"data"
    app=create_app(root,cache_bytes=8000)
    with TestClient(app) as client:
        project=client.post("/api/projects",json={"name":"Persistence test"}).json()["id"]
        cfr=make_video(tmp_path/"cfr.mkv",[0,100,200,300])
        vfr=make_video(tmp_path/"vfr.mkv",[1000,1100,1370,1900,2300])
        for path,expected in [(cfr,[0,100,200,300]),(vfr,[1000,1100,1370,1900,2300])]:
            job=import_video(client,project,path)
            assert job["state"]=="SUCCEEDED",job
            media_id=job["result"]["media_id"]
            rows=client.get(f"/api/media/{media_id}/frames").json()["items"]
            assert [f["pts"] for f in rows]==expected
            assert all((r["time_base_num"],r["time_base_den"])==(1,1000) for r in rows)
            # Check actual decoded pixel identity after backwards/nonsequential seeks.
            with av.open(str(path)) as container:
                expected_pixels={f.pts:f.to_image().tobytes() for f in container.decode(video=0)}
            for row in reversed(rows):
                response=client.get(row["image_url"])
                assert response.status_code==200,response.text
                assert response.headers["x-source-pts"]==str(row["pts"])
                assert Image.open(io.BytesIO(response.content)).tobytes()==expected_pixels[row["pts"]]
            assert sum(p.stat().st_size for p in (root/"frame_cache").glob("*.png"))<=8000
        selected=client.get(f"/api/media/{media_id}/frames?timestamp=0.2&limit=1").json()["items"][0]
        assert selected["pts"]==1370 and selected["relative_seconds"]==.37
        assert client.get(f"/api/media/{media_id}/frames?offset=2&limit=2").json()["items"][0]["id"]==selected["id"]
        original_frames=rows
        original_bytes=vfr.read_bytes()
        vfr.unlink()
        assert client.get(f"/api/media/{media_id}").json()["source_status"]=="SOURCE_MISSING"
        assert client.get(selected["image_url"]).status_code==409
        assert client.post(f"/api/media/{media_id}/remap",json={"source_path":str(cfr)}).status_code==409
        moved=tmp_path/"renamed.mkv"
        moved.write_bytes(original_bytes)
        assert client.post(f"/api/media/{media_id}/remap",json={"source_path":str(moved)}).status_code==200
        assert client.get(selected["image_url"]).status_code==200
    with TestClient(create_app(root,cache_bytes=1)) as client:
        assert client.get("/api/projects").json()[0]["id"]==project
        assert client.get(f"/api/media/{media_id}").json()["source_status"]=="VERIFIED"
        assert client.get(f"/api/media/{media_id}/frames").json()["items"]==original_frames
        assert client.get(selected["image_url"]).status_code==200
        assert sum(p.stat().st_size for p in (root/"frame_cache").glob("*.png"))<=1
        moved.write_bytes(b"changed source")
        assert client.get(f"/api/media/{media_id}").json()["source_status"]=="SOURCE_HASH_MISMATCH"
        assert client.get(selected["image_url"]).status_code==409


def test_clip_offset_failed_import_and_roi_versions(tmp_path):
    app=create_app(tmp_path/"db")
    with TestClient(app) as client:
        project=client.post("/api/projects",json={"name":"ROI test"}).json()["id"]
        parent=make_video(tmp_path/"parent.mkv",[1000,1100,1370,1900,2300])
        parent_id=import_video(client,project,parent)["result"]["media_id"]
        clip=make_video(tmp_path/"clip.mkv",[0,100,200])
        clip_job=import_video(client,project,clip,parent_media_id=parent_id,source_offset_seconds=.5)
        assert clip_job["state"]=="SUCCEEDED",clip_job
        media_id=clip_job["result"]["media_id"]
        row=client.get(f"/api/media/{media_id}/frames?offset=1&limit=1").json()["items"][0]
        assert row["mapped_source_timestamp_seconds"]==1.6
        assert row["source_media_id"]==parent_id
        failed_clip=import_video(client,project,clip,parent_media_id=parent_id,source_offset_seconds=100)
        assert failed_clip["state"]=="FAILED" and failed_clip["error"]["code"]=="CLIP_OUT_OF_RANGE"
        corrupt=tmp_path/"broken.mp4"
        corrupt.write_bytes(b"not a video")
        assert import_video(client,project,corrupt)["state"]=="FAILED"
        assert len(client.get(f"/api/projects/{project}/media").json())==2
        value={"name":"Lobby","polygon_source_px":[[20,20],[300,20],[300,300],[20,300]],"area_m2":20,"area_evidence":"DEMO area, not measured","evidence_type":"DEMO"}
        first=client.post(f"/api/media/{parent_id}/rois",json=value)
        assert first.status_code==201,first.text
        first=first.json()
        update={**value,"area_m2":25,"base_version_id":first["id"]}
        second=client.post(f"/api/rois/{first['roi_id']}/versions",json=update)
        assert second.status_code==201
        assert client.post(f"/api/rois/{first['roi_id']}/versions",json=update).status_code==409
        versions=client.get(f"/api/media/{parent_id}/rois").json()
        assert [r["area_m2"] for r in versions]==[20,25]
        assert versions[0]==first
        for point,verified,expected in [([20,100],True,"INSIDE"),([10,100],True,"OUTSIDE"),([20,100],False,"UNCERTAIN"),(None,True,"UNCERTAIN")]:
            assert client.post(f"/api/roi-versions/{first['id']}/membership",json={"point":point,"anchor_verified":verified}).json()["membership"]==expected
        for bad in [{"area_m2":0},{"polygon_source_px":[[20,20],[300,300],[300,20],[20,300]]},{"polygon_source_px":[[0,0],[1000,0],[100,100]]},{"area_evidence":" "},{"evidence_type":"MEASURED"},{"valid_from_seconds":1,"valid_to_seconds":.5}]:
            response=client.post(f"/api/media/{parent_id}/rois",json={**value,**bad})
            assert response.status_code==422,response.text
        assert client.post("/api/projects",json={"name":"attack"},headers={"Origin":"https://unrelated.example"}).status_code==403
    with app.state.engine.connect() as db:
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar()=="0006_workflows"


def test_demo_endpoint(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        demo=client.post("/api/demo",json={})
        assert demo.status_code==202,demo.text
        for job_id in demo.json()["job_ids"]:
            assert finish(client,job_id)["state"]=="SUCCEEDED"
        assert len(client.get(f"/api/projects/{demo.json()['project_id']}/media").json())==2


def test_interframe_seek_decodes_from_keyframe(tmp_path):
    source=make_video(tmp_path/"raw.mkv",list(range(0,2000,100)))
    encoded=tmp_path/"interframe.mp4"
    subprocess.run(["ffmpeg","-v","error","-i",str(source),"-c:v","libx264","-g","12","-bf","2","-pix_fmt","yuv420p",str(encoded)],check=True)
    with av.open(str(encoded)) as container:
        expected=[(f.pts,f.to_image().tobytes(),f.key_frame) for f in container.decode(video=0)]
    assert any(not f[2] for f in expected)
    with TestClient(create_app(tmp_path/"data")) as client:
        project=client.post("/api/projects",json={"name":"GOP fixture"}).json()["id"]
        media=import_video(client,project,encoded)["result"]["media_id"]
        rows=client.get(f"/api/media/{media}/frames").json()["items"]
        for i in [15,3,19,0,11]:
            response=client.get(rows[i]["image_url"])
            assert response.status_code==200,response.text
            assert rows[i]["pts"]==expected[i][0]
            assert Image.open(io.BytesIO(response.content)).tobytes()==expected[i][1]


def test_roi_minimal_form_applies_entire_video(tmp_path):
    with TestClient(create_app(tmp_path / "data")) as client:
        project=client.post("/api/projects",json={"name":"Whole video"}).json()["id"]
        source=make_video(tmp_path / "input.mkv",[0,100,200])
        for kind in ("REAL","DEMO"):
            media=import_video(client,project,source,evidence_type=kind)["result"]["media_id"]
            body={"name":"Entrance","area_m2":12,"polygon_source_px":[[0,0],[200,0],[200,200],[0,200]]}
            response=client.post(f"/api/media/{media}/rois",json=body)
            assert response.status_code==201,response.text
            roi=response.json()
            assert roi["valid_from_seconds"]==0 and roi["valid_to_seconds"] is None
            assert roi["evidence_type"]==("DEMO" if kind=="DEMO" else "USER_DECLARED")
            for limited in ({"valid_from_seconds":.1},{"valid_to_seconds":.2}):
                assert client.post(f"/api/media/{media}/rois",json={**body,**limited}).status_code==422
            updated=client.post(f"/api/rois/{roi['roi_id']}/versions",json={**body,"area_m2":15,"base_version_id":roi["id"]})
            assert updated.status_code==201,updated.text
            assert updated.json()["valid_to_seconds"] is None


def test_browser_video_upload_is_managed_and_imported(tmp_path):
    root=tmp_path/"data"
    source=make_video(tmp_path/"source.mkv",[0,100,200])
    with TestClient(create_app(root)) as client:
        project=client.post("/api/projects",json={"name":"Upload"}).json()["id"]
        paths=[]
        for _ in range(2):
            response=client.post(f"/api/projects/{project}/media/upload",params={"filename":"../Sảnh.mkv","camera_id":"cam_01","evidence_type":"REAL"},content=source.read_bytes(),headers={"Content-Type":"application/octet-stream"})
            assert response.status_code==202,response.text
            job=finish(client,response.json()["job_id"])
            assert job["state"]=="SUCCEEDED",job
            media=client.get(f"/api/media/{job['result']['media_id']}").json()
            uploaded=Path(media["source_path"])
            assert uploaded.is_relative_to(root/"projects"/project/"sources")
            assert uploaded.name=="Sảnh.mkv" and uploaded.read_bytes()==source.read_bytes()
            assert media["frame_count"]==3 and media["evidence_type"]=="REAL"
            paths.append(uploaded)
        assert paths[0]!=paths[1]
        params={"filename":"empty.mp4","camera_id":"cam"}
        assert client.post(f"/api/projects/{project}/media/upload",params=params,content=b'').status_code==422
        for invalid in ({"filename":"CON.mp4"},{"camera_id":"   "}):
            assert client.post(f"/api/projects/{project}/media/upload",params={**params,**invalid},content=b'abc').status_code==422
        assert not list(root.rglob('*.partial'))
        assert client.post('/api/projects/missing/media/upload',params=params,content=b'abc').status_code==404
        assert client.post(f"/api/projects/{project}/media/upload",params=params,content=b'abc',headers={"Origin":"https://unrelated.example"}).status_code==403
