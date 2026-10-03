import csv
import html
import io
import json
import zipfile

def csv_bytes(rows,columns):
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=columns,extrasaction='ignore');writer.writeheader()
    for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    return output.getvalue().encode('utf-8-sig')

def report_files(record):
    p=record['payload'];metrics=[]
    for name,value in p.get('metrics',{}).items():metrics.append({'metric':name,**value})
    errors=sorted(p.get('pairs',[]),key=lambda r:max((abs(v) for v in r.get('errors',{}).values()),default=0),reverse=True)
    coverage=[{'metric':k,'support':v.get('support'),'candidate_samples':v.get('candidate_samples'),'excluded_samples':v.get('excluded_samples')} for k,v in p.get('metrics',{}).items()]
    content=json.dumps(record,ensure_ascii=False,indent=2)
    body='<html lang="vi"><meta charset="utf-8"><title>Crowd QC report</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto;padding:15px}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #bbb}</style>'
    body+='<h1>Crowd QC — '+html.escape(record['kind'])+'</h1><p>'+html.escape(p.get('evidence_type','REFERENCE'))+' · Missing/unknown không được điền 0.</p><h2>Metric và coverage</h2><table><tr><th>Metric</th><th>MAE</th><th>RMSE</th><th>Support</th></tr>'
    for r in metrics:body+='<tr>'+''.join('<td>'+html.escape(str(r.get(k,'N/A')))+'</td>' for k in ('metric','mae','rmse','support'))+'</tr>'
    body+='</table>'
    def table(rows,columns):
        return '<table><tr>'+''.join('<th>'+html.escape(k)+'</th>' for k in columns)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(k,'N/A')))+'</td>' for k in columns)+'</tr>' for r in rows)+'</table>'
    if record['kind']=='POOLED_EVALUATION':
        body+='<h2>Camera / ROI / condition</h2>'+table([{**s['scope'],'metric':k,**v} for s in p['slices'] for k,v in s['metrics'].items() if k in ('count','density','speed')],['camera_id','roi_version_id','condition','metric','mae','rmse','support'])
    if record['kind']=='HELPER_EVALUATION':body+='<h2>Helper validation</h2>'+table(p['results'],['model_id','map50','map50_95','precision','recall','peak_vram_bytes'])
    if record['kind']=='EFFORT_STUDY':body+='<h2>'+html.escape(p['claim_status'])+'</h2>'+table([{'evidence_type':p['evidence_type'],'human_active_reduction':p['human_active_reduction'],**p['summary']}],['evidence_type','pairs','active_reduction_median','total_reduction_median','quality_gate','human_active_reduction'])
    body+='<h2>Manifest và chi tiết</h2><pre>'+html.escape(content)+'</pre></html>'
    files={'metrics.csv':csv_bytes(metrics,['metric','mae','rmse','bias','p95_absolute_error','support','candidate_samples','excluded_samples','macro_f1_supported_classes','matrix']),
        'errors.csv':csv_bytes(errors,['frame_id','time','sut_row','delta_time','gt','sut','errors','excluded_metrics','sut_rule_consistency']),
        'coverage.csv':csv_bytes(coverage,['metric','support','candidate_samples','excluded_samples']),
        'excluded.csv':csv_bytes(p.get('unmatched',[]),['gt_index','frame_id','sut_row','reason','metrics']),
        'run_manifest.json':content.encode('utf-8'),'report.html':body.encode('utf-8')}
    if record['kind']=='HELPER_EVALUATION':
        files['helper_metrics.csv']=csv_bytes(p['results'],['model_id','map50','map50_95','precision','recall'])
        files['helper_errors.csv']=csv_bytes([{'model_id':r['model_id'],**f} for r in p['results'] for f in r['frames']],['model_id','image_path','camera_id','condition','tp','fp','fn','latency_seconds','expected_box_edits','missed_gt_indices','false_positive_indices'])
    if record['kind']=='POOLED_EVALUATION':
        files['slices.csv']=csv_bytes([{**s['scope'],'metric':k,**v} for s in p['slices'] for k,v in s['metrics'].items()],['camera_id','roi_version_id','condition','metric','mae','rmse','bias','p95_absolute_error','support','macro_f1_supported_classes'])
        files['errors.csv']=csv_bytes(errors,['evaluation_id','scope','frame_id','time','sut_row','delta_time','gt','sut','errors','excluded_metrics'])
    if record['kind']=='EFFORT_STUDY':
        files['paired_trials.csv']=csv_bytes(p['rows'],list(p['rows'][0]) if p['rows'] else ['pair_id'])
        summary={'evidence_type':p['evidence_type'],'claim_status':p['claim_status'],'human_active_reduction':p['human_active_reduction'],**p['summary']}
        files['effort_summary.csv']=csv_bytes([summary],list(summary))
        files['excluded.csv']=csv_bytes(p['excluded'],['pair_id','reason'])
        files['effort_slices.csv']=csv_bytes(p['slices'],list(p['slices'][0]) if p['slices'] else ['camera','condition'])
    return files

def zip_bytes(files):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in files.items():archive.writestr(name,data)
    return buffer.getvalue()
