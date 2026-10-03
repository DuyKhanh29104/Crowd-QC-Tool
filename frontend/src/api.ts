export async function api<T = any>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(
    "/api" + path,
    body === undefined
      ? {signal}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal,
        },
  );
  const value = await response.json();
  if (!response.ok) {
    const message =
      value.message ||
      (Array.isArray(value.detail)
        ? value.detail
            .map((d: any) => `${d.loc.join(".")}: ${d.msg}`)
            .join("; ")
        : value.detail) ||
      response.statusText;
    throw new Error(message);
  }
  return value;
}
export type Project = { id: string; name: string };
export type Media = {
  id: string;
  project_id: string;
  name: string;
  camera_id: string;
  evidence_type: string;
  width: number;
  height: number;
  frame_count: number;
  duration_seconds: number;
  sha256: string;
  source_path: string;
  source_status?: string;
  source_error?: { message: string };
};
export type Frame = {
  id: string;
  pts: number;
  ordinal: number;
  time_base_num: number;
  time_base_den: number;
  relative_seconds: number;
  source_timestamp_seconds: number;
  mapped_source_timestamp_seconds: number;
  source_mapping_status: string;
  image_url: string;
};
export type Point = [number, number];
export type ReviewObject = { id: string; box: [number,number,number,number] | null; marker: Point; anchor: Point | null; anchor_verified: boolean; origin?: string; anchor_origin?: string; source_proposal_id?: string | null; score?: number | null };
export type ProposalObject = { object_id: string; geometry: {xyxy_source_px: number[]}; score: number; ground_anchor: {xy_source_px: Point}; roi_hint?: string };
export type ROI = {
  id: string;
  roi_id: string;
  version: number;
  name: string;
  polygon_source_px: Point[];
  area_m2: number;
  area_evidence: string;
  evidence_type: string;
  valid_from_seconds: number;
  valid_to_seconds: number | null;
};

export function uploadVideo(path:string,file:File,onProgress:(percent:number)=>void):Promise<{job_id:string}>{
  return new Promise((resolve,reject)=>{
    const xhr=new XMLHttpRequest();
    xhr.open('POST','/api'+path);
    xhr.setRequestHeader('Content-Type','application/octet-stream');
    xhr.upload.onprogress=e=>{if(e.lengthComputable)onProgress(Math.round(e.loaded/e.total*100));};
    xhr.onerror=()=>reject(new Error('Không tải được video. Kiểm tra kết nối và thử lại.'));
    xhr.onload=()=>{let result:any;try{result=JSON.parse(xhr.responseText);}catch{reject(new Error('Máy chủ trả về phản hồi không hợp lệ.'));return;}
      if(xhr.status>=200&&xhr.status<300)resolve(result);
      else reject(new Error(result.message||'Không thể tải video. Kiểm tra file và thông tin camera.'));};
    xhr.send(file);
  });
}
