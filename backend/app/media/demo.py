"""Procedural video fixtures with known PTS; no real footage or GT claims."""
from fractions import Fraction
from pathlib import Path
import av
from PIL import Image,ImageDraw


def make_video(path, pts, width=640, height=360):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists(): raise FileExistsError(path)
    with av.open(str(path),"w") as container:
        stream=container.add_stream("ffv1",rate=10)
        stream.width,stream.height=width,height
        stream.pix_fmt="bgr0"
        stream.time_base=stream.codec_context.time_base=Fraction(1,1000)
        for i,stamp in enumerate(pts):
            image=Image.new("RGB",(width,height),"#243d43")
            draw=ImageDraw.Draw(image)
            draw.rectangle((30,45,width-30,height-25),fill="#a4b7ae")
            draw.rectangle((width/2-50,45,width/2+50,110),fill="#50746b",outline="#e1eee1",width=3)
            for y in range(130,height-20,45): draw.line((30,y,width-30,y),fill="#869f93",width=1)
            for x in range(70,width-20,70): draw.line((x,110,x,height-25),fill="#869f93",width=1)
            for j in range(3):
                x=100+j*140+(i*9)%60;y=165+(j%2)*80
                draw.ellipse((x-12,y-24,x+12,y),fill=["#dca665","#387674","#5478a2"][j])
                draw.rounded_rectangle((x-17,y-2,x+17,y+45),radius=9,fill=["#dca665","#387674","#5478a2"][j])
            draw.text((18,15),f"DEMO - NOT CCTV / frame {i} / PTS {stamp}",fill="white")
            frame=av.VideoFrame.from_image(image)
            frame.pts,frame.time_base=stamp,Fraction(1,1000)
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    return path
