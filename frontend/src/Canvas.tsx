import { useEffect, useRef, useState } from "react";
import { Stage, Layer, Image as KonvaImage, Line, Circle, Rect, Text } from "react-konva";
import { Point, ProposalObject, ReviewObject } from "./api";

export default function Canvas({
  url,
  preparedImage,
  compact = false,
  width,
  height,
  points,
  onChange,
  mode,
  proposals = [],
  reviewObjects,
  selectedObject,
  onSelectObject,
  onEditObject,
  onAddObject,
  reviewMode = "select",
  reviewDisabled = false,
  onImageReady,
}: {
  url: string;
  preparedImage?: HTMLImageElement;
  compact?: boolean;
  width: number;
  height: number;
  points: Point[];
  onChange: (p: Point[]) => void;
  mode: "draw" | "edit" | "pan";
  proposals?: ProposalObject[];
  reviewObjects?: ReviewObject[];
  selectedObject?: string;
  onSelectObject?: (id:string) => void;
  onEditObject?: (object:ReviewObject) => void;
  onAddObject?: (box:[number,number,number,number]|null,point:Point) => void;
  reviewMode?: "select" | "add" | "marker" | "pan";
  reviewDisabled?: boolean;
  onImageReady?: (ready:boolean)=>void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const stage = useRef<any>(null);
  const [size, setSize] = useState({ width: 640, height: 450 });
  const [loadedImage, setImage] = useState<HTMLImageElement>();
  const image = preparedImage ?? loadedImage;
  const [imageError, setImageError] = useState("");
  const [scale, setScale] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [boxStart, setBoxStart] = useState<Point>();
  useEffect(() => setBoxStart(undefined), [url,reviewMode]);
  const clamp = (x:number,y:number):Point => [Math.max(0,Math.min(width,x)),Math.max(0,Math.min(height,y))];
  const fit = () => {
    const s = Math.max(0.01, Math.min((size.width - (compact ? 0 : 32)) / width, (size.height - (compact ? 0 : 32)) / height));
    setScale(s);
    setPosition({
      x: (size.width - width * s) / 2,
      y: (size.height - height * s) / 2,
    });
  };
  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      if (r.width <= 32) return;
      setSize({ width: r.width, height: Math.max(1, r.height) });
    });
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(fit, [size.width, size.height, width, height,compact]);
  useEffect(() => {
    if (preparedImage) {onImageReady?.(true);return;}
    setImage(undefined);
    onImageReady?.(false);
    setImageError("");
    let active = true;
    const img = new window.Image();
    img.onload = () => {
      if (active) {setImage(img);onImageReady?.(true);}
    };
    img.onerror = () => {
      if (active)
        setImageError(
          "Không tải được ảnh nguồn. Kiểm tra đường dẫn/hash video.",
        );
    };
    img.src = url;
    return () => {
      active = false;
    };
  }, [url,preparedImage]);
  const sourcePoint = (): Point | null => {
    const pointer = stage.current?.getPointerPosition();
    if (!pointer) return null;
    return [(pointer.x - position.x) / scale, (pointer.y - position.y) / scale];
  };
  const zoom = (factor: number) => {
    const center = { x: size.width / 2, y: size.height / 2 };
    const next = Math.min(8, Math.max(0.08, scale * factor));
    setPosition({
      x: center.x - ((center.x - position.x) * next) / scale,
      y: center.y - ((center.y - position.y) * next) / scale,
    });
    setScale(next);
  };
  return (
    <div className={`canvas-shell${compact ? " compact-canvas" : ""}`}>
      <div className="canvas-tools">
        <span>
          {reviewObjects !== undefined ? (reviewMode === "add" ? "Bấm hai góc đối diện để thêm box" : reviewMode === "marker" ? "Bấm để thêm người chỉ có marker" : "Chọn box · kéo để di chuyển · kéo góc để đổi kích thước") : mode === "draw"
            ? "Bấm để thêm đỉnh ROI"
            : mode === "edit"
              ? "Kéo đỉnh để chỉnh ROI"
              : "Kéo để di chuyển ảnh"}
        </span>
        <button onClick={() => zoom(0.8)} aria-label="Thu nhỏ">
          −
        </button>
        <span>{Math.round(scale * 100)}%</span>
        <button onClick={() => zoom(1.25)} aria-label="Phóng to">
          +
        </button>
        <button onClick={fit}>Vừa khung</button>
      </div>
      <div className="canvas-area" ref={container} data-testid="roi-canvas">
        {!image ? (
          <div className="canvas-loading">
            {imageError || "Đang giải mã đúng frame nguồn…"}
          </div>
        ) : (
          <Stage
            ref={stage}
            width={size.width}
            height={size.height}
            scaleX={scale}
            scaleY={scale}
            x={position.x}
            y={position.y}
            draggable={reviewObjects !== undefined ? reviewMode === "pan" : mode === "pan"}
            onDragEnd={(e) => {
              if (e.target === e.target.getStage())
                setPosition({ x: e.target.x(), y: e.target.y() });
            }}
            onWheel={(e) => {
              if (!e.evt.ctrlKey && !e.evt.metaKey) return;
              e.evt.preventDefault();
              zoom(e.evt.deltaY < 0 ? 1.1 : 1 / 1.1);
            }}
            onMouseDown={(e) => {
              if (reviewObjects !== undefined) {
                if (reviewDisabled || e.evt.button !== 0) return;
                const p = sourcePoint();
                if (!p || p[0]<0 || p[0]>width || p[1]<0 || p[1]>height) return;
                if (reviewMode === "marker") onAddObject?.(null,p);
                if (reviewMode === "add") {
                  if (!boxStart) setBoxStart(p);
                  else {
                    const box:[number,number,number,number]=[Math.min(p[0],boxStart[0]),Math.min(p[1],boxStart[1]),Math.max(p[0],boxStart[0]),Math.max(p[1],boxStart[1])];
                    if (box[2]-box[0]>=1 && box[3]-box[1]>=1) onAddObject?.(box,[(box[0]+box[2])/2,(box[1]+box[3])/2]);
                    setBoxStart(undefined);
                  }
                }
                return;
              }
              if (mode !== "draw" || e.evt.button !== 0) return;
              const p = sourcePoint();
              if (
                p &&
                p[0] >= 0 &&
                p[0] <= width &&
                p[1] >= 0 &&
                p[1] <= height
              )
                onChange([...points, p]);
            }}
          >
            <Layer>
              <KonvaImage image={image} width={width} height={height} />
              {reviewObjects?.map(obj => {
                const box=obj.box, editable=!reviewDisabled && reviewMode === "select", chosen=obj.id === selectedObject;
                return box && <Rect key={`edit-${obj.id}`} x={box[0]} y={box[1]} width={box[2]-box[0]} height={box[3]-box[1]} stroke={chosen ? "#ffffff" : "#ffcb66"} strokeWidth={(chosen ? 3 : 2)/scale}
                  onClick={() => onSelectObject?.(obj.id)} draggable={editable && chosen}
                  onDragEnd={e => { const w=box[2]-box[0],h=box[3]-box[1],x=Math.max(0,Math.min(width-w,e.target.x())),y=Math.max(0,Math.min(height-h,e.target.y())); e.target.position({x,y});onEditObject?.({...obj,box:[x,y,x+w,y+h]}); }} />;
              })}
              {reviewObjects?.map(obj => <Circle key={`person-${obj.id}`} x={obj.marker[0]} y={obj.marker[1]} radius={6/scale} fill={obj.id===selectedObject ? "#fff" : "#ffcb66"} stroke="#143438" strokeWidth={1/scale} onClick={() => onSelectObject?.(obj.id)} draggable={!obj.box && !reviewDisabled && reviewMode==="select"}
                onDragEnd={e => {const p=clamp(e.target.x(),e.target.y());e.target.position({x:p[0],y:p[1]});onEditObject?.({...obj,marker:p});}} />)}
              {reviewObjects?.map(obj => obj.anchor && <Circle key={`foot-${obj.id}`} x={obj.anchor[0]} y={obj.anchor[1]} radius={5/scale} fill={obj.anchor_verified ? "#35edc6" : "#ed7ddb"} stroke="#10282e" strokeWidth={1/scale} onClick={() => onSelectObject?.(obj.id)} draggable={!reviewDisabled && reviewMode==="select"}
                onDragEnd={e => {const p=clamp(e.target.x(),e.target.y());e.target.position({x:p[0],y:p[1]});onEditObject?.({...obj,anchor:p,anchor_verified:true});}} />)}
              {reviewObjects?.filter(o=>o.id===selectedObject && o.box).map(obj => <Rect key={`resize-${obj.id}`} x={obj.box![2]-5/scale} y={obj.box![3]-5/scale} width={10/scale} height={10/scale} fill="#fff" stroke="#143438" strokeWidth={1/scale} draggable={!reviewDisabled && reviewMode==="select"}
                onDragEnd={e => {const b=obj.box!,p=clamp(e.target.x()+5/scale,e.target.y()+5/scale);onEditObject?.({...obj,box:[b[0],b[1],Math.min(width,Math.max(b[0]+.1,p[0])),Math.min(height,Math.max(b[1]+.1,p[1]))]});}} />)}
              {boxStart && <Circle x={boxStart[0]} y={boxStart[1]} radius={5/scale} fill="#fff" listening={false} />}
              {proposals.map(obj => {
                const [x1,y1,x2,y2] = obj.geometry.xyxy_source_px;
                const color = obj.roi_hint === "OUTSIDE" ? "#a3aec1" : "#ffcb66";
                return <Rect key={obj.object_id} x={x1} y={y1} width={x2-x1} height={y2-y1} stroke={color} strokeWidth={2/scale} listening={false} />;
              })}
              {proposals.map(obj => <Circle key={`anchor-${obj.object_id}`} x={obj.ground_anchor.xy_source_px[0]} y={obj.ground_anchor.xy_source_px[1]} radius={4/scale} stroke="#ffcb66" strokeWidth={1/scale} listening={false} />)}
              {proposals.map(obj => <Text key={`score-${obj.object_id}`} x={obj.geometry.xyxy_source_px[0]} y={Math.max(0,obj.geometry.xyxy_source_px[1]-15/scale)} text={`${(obj.score*100).toFixed(1)}%`} fontSize={12/scale} fill="#ffcb66" listening={false} />)}
              <Line
                points={points.flat()}
                closed={points.length >= 3}
                fill={points.length >= 3 ? "rgba(21,190,163,.18)" : undefined}
                stroke="#35edc6"
                strokeWidth={2 / scale}
                listening={false}
              />
              {points.map((p, i) => (
                <Circle
                  key={i}
                  x={p[0]}
                  y={p[1]}
                  radius={5 / scale}
                  fill="#0b2528"
                  stroke="#68ffe2"
                  strokeWidth={2 / scale}
                  draggable={mode === "edit" && reviewObjects === undefined}
                  onDragMove={(e) => {
                    const x = Math.max(0, Math.min(width, e.target.x()));
                    const y = Math.max(0, Math.min(height, e.target.y()));
                    e.target.position({ x, y });
                    const copy = [...points];
                    copy[i] = [x, y];
                    onChange(copy);
                  }}
                />
              ))}
            </Layer>
          </Stage>
        )}
      </div>
    </div>
  );
}
