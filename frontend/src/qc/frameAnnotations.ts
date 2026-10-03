/** Resolve exact-frame annotations, including edits saved through event review. */
export function indexFrameAnnotations(analysis:any) {
 const samples=new Map<string,any>((analysis.rows||[]).map((row:any)=>[row.frame_id,row]));
 const edits=new Map<string,{objects:any[];label:string;reviewId:string;created:string}>();
 for(const candidate of analysis.candidates||[]){
  const review=candidate.review;if(!review)continue;
  for(const [id,objects] of Object.entries(review.payload?.frame_edits||{})){
   if(!Array.isArray(objects))continue;
   const created=review.created_at||'';
   if(!edits.has(id)||created>=edits.get(id)!.created)
    edits.set(id,{objects,label:review.payload.label,reviewId:review.id,created});
  }
 }
 return {samples,edits};
}
