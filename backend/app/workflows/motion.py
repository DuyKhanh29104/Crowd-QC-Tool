"""Planar calibration and reviewed path-length speed. No fabricated observations."""
import math
from statistics import mean
from backend.app.roi.geometry import membership

def solve(a,b):
    a=[list(row)+[v] for row,v in zip(a,b)];n=len(b)
    for i in range(n):
        pivot=max(range(i,n),key=lambda j:abs(a[j][i]))
        if abs(a[pivot][i])<1e-12:raise ValueError('DEGENERATE_CALIBRATION')
        a[i],a[pivot]=a[pivot],a[i];scale=a[i][i];a[i]=[v/scale for v in a[i]]
        for j in range(n):
            if i!=j:
                scale=a[j][i];a[j]=[v-scale*w for v,w in zip(a[j],a[i])]
    return [row[-1] for row in a]

def transform(h,p):
    x,y=p;den=h[6]*x+h[7]*y+h[8]
    if abs(den)<1e-10:raise ValueError('POINT_AT_INFINITY')
    return [(h[0]*x+h[1]*y+h[2])/den,(h[3]*x+h[4]*y+h[5])/den]

def hull(points):
    pts=sorted(set(tuple(p) for p in points))
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    lower=[];upper=[]
    for p in pts:
        while len(lower)>1 and cross(lower[-2],lower[-1],p)<=0:lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper)>1 and cross(upper[-2],upper[-1],p)<=0:upper.pop()
        upper.append(p)
    return lower[:-1]+upper[:-1]

def fit_calibration(points,lengths,roi,repeatability_mps):
    fits=[p for p in points if p['role']=='FIT'];holds=[p for p in points if p['role']=='HOLDOUT']
    if len(fits)<4 or len(holds)<2:raise ValueError('NEED_4_FIT_AND_2_INDEPENDENT_HOLDOUT')
    if len({tuple(p['pixel']) for p in points})!=len(points):raise ValueError('DUPLICATE_CONTROL_POINT')
    sx=max(1,max(abs(v) for p in fits for v in p['pixel']));sw=max(1,max(abs(v) for p in fits for v in p['world']))
    rows=[];rhs=[]
    for p in fits:
        x,y=[v/sx for v in p['pixel']];u,v=[v/sw for v in p['world']]
        rows.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]]);rhs.extend([u,v])
    a=[[sum(row[i]*row[j] for row in rows) for j in range(8)] for i in range(8)];b=[sum(row[i]*v for row,v in zip(rows,rhs)) for i in range(8)]
    h=solve(a,b);h=[h[0]*sw/sx,h[1]*sw/sx,h[2]*sw,h[3]*sw/sx,h[4]*sw/sx,h[5]*sw,h[6]/sx,h[7]/sx,1.]
    support=hull([p['pixel'] for p in fits]);issues=[]
    if len(support)<3 or any(membership(p,support,True)!='INSIDE' for p in roi):issues.append('ROI_NOT_COVERED_BY_FIT_POINTS')
    errors=[{'role':p['role'],'region':p['region'],'error_m':math.dist(transform(h,p['pixel']),p['world'])} for p in points]
    checks=[{'region':p['region'],'expected_m':p['meters'],'measured_m':math.dist(transform(h,p['a']),transform(h,p['b'])),'tolerance_m':max(.05,.02*p['meters'])} for p in lengths]
    for c in checks:c['error_m']=abs(c['measured_m']-c['expected_m'])
    if not checks or any(c['error_m']>c['tolerance_m'] for c in checks):issues.append('LENGTH_VALIDATION_FAILED_OR_MISSING')
    if {p['region'] for p in holds}!={'NEAR','MID','FAR'}:issues.append('HOLDOUT_REGION_COVERAGE_INCOMPLETE')
    if any(e['error_m']>.05 for e in errors if e['role']=='HOLDOUT'):issues.append('HOLDOUT_POSITION_ERROR')
    if repeatability_mps is None or repeatability_mps>=.02:issues.append('SPEED_REPEATABILITY_UNVERIFIED_OR_FAILED')
    return {'homography':h,'support_polygon':support,'point_errors':errors,'length_checks':checks,'issues':issues,'valid':not issues,
        'gates':{'position_m':.05,'length':'max(0.05m,2%)','speed_repeatability_mps':.02},'fit_uses_holdout':False}

def individual_speed(points,end,window,max_gap,calibration):
    if not calibration.get('valid') or not calibration.get('approved'):return {'value':None,'reason':'CALIBRATION_INVALID','coverage':0}
    start=end-window;pts=sorted(points,key=lambda p:p['time']);duration=distance=0.
    for a,b in zip(pts,pts[1:]):
        dt=b['time']-a['time'];lo=max(start,a['time']);hi=min(end,b['time'])
        if hi<=lo:continue
        if dt<=0 or dt>max_gap or not all(p.get('verified') and p.get('observed') and p.get('identity_verified') for p in (a,b)):continue
        if any(membership(p['anchor'],calibration['support_polygon'],True)!='INSIDE' for p in (a,b)):continue
        distance+=math.dist(transform(calibration['homography'],a['anchor']),transform(calibration['homography'],b['anchor']))*(hi-lo)/dt
        duration+=hi-lo
    coverage=duration/window
    return {'value':distance/duration if abs(duration-window)<1e-7 else None,'reason':None if abs(duration-window)<1e-7 else 'TRACK_GAP_OR_UNREVIEWED_OR_TOO_SHORT','coverage':coverage,'duration':duration}

def population_speed(tracks,end,population,polygon,calibration,config):
    if population is None:return {'value':None,'status':'UNKNOWN','reason':'POPULATION_UNKNOWN','people':[],'coverage':0}
    if population==0:return {'value':None,'status':'UNKNOWN','reason':'EMPTY_POPULATION','people':[],'coverage':1}
    if not calibration.get('valid') or not calibration.get('approved'):return {'value':None,'status':'UNKNOWN','reason':'CALIBRATION_INVALID','people':[],'coverage':0}
    people=[];uncertain=False
    for track_id,points in tracks.items():
        point=next((p for p in points if abs(p['time']-end)<1e-7),None)
        if point is None:continue
        state=membership(point['anchor'],polygon,point.get('verified',False))
        if state=='UNCERTAIN':uncertain=True
        if state=='INSIDE':people.append({'track_id':track_id,**individual_speed(points,end,config['window_seconds'],config['max_gap_seconds'],calibration)})
    valid=[p['value'] for p in people if p['value'] is not None]
    complete=not uncertain and len(people)==population and len(valid)==population
    return {'value':mean(valid) if complete else None,'status':'VALID' if complete else 'UNKNOWN','reason':None if complete else 'FULL_POPULATION_SPEED_INCOMPLETE',
        'population_count':population,'valid_count':len(valid),'people':people,'coverage':len(valid)/population,'estimator':'reviewed_path_length_v1','smoothing':'NONE'}

def draft_motion(tracks,calibration,polygon,start,end,config):
    """Diagnostics do not alter observation flags or publish ground truth."""
    def edge_distance(p):
        values=[]
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            dx,dy=b[0]-a[0],b[1]-a[1];d=dx*dx+dy*dy
            t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/d)) if d else 0
            values.append(math.dist(p,[a[0]+t*dx,a[1]+t*dy]))
        return min(values) if values else math.inf
    valid=calibration.get('valid') and calibration.get('approved');result={}
    for tid,points in tracks.items():
        points=sorted(points,key=lambda p:p['time']);rows=[];flags=set()
        if not points:result[tid]={'rows':[],'flags':['EMPTY_TRACK']};continue
        if points[-1]['time']-points[0]['time']<1:flags.add('SHORT_TRACK')
        transformed={}
        for p in points:
            if valid and membership(p['anchor'],calibration['support_polygon'],True)=='INSIDE':
                try:transformed[p['time']]=transform(calibration['homography'],p['anchor'])
                except ValueError:pass
        for i,p in enumerate(points):
            if not start<=p['time']<=end:continue
            issues=[];step=None;previous=points[i-1] if i else None
            if not all(p.get(k) for k in ('verified','observed','identity_verified')):issues.append('REVIEW_REQUIRED')
            if edge_distance(p['anchor'])<=config['boundary_pixels']:issues.append('ANCHOR_NEAR_BOUNDARY')
            if previous:
                dt=p['time']-previous['time']
                if dt>config['max_gap_seconds']:issues.append('OBSERVATION_GAP')
                if 0<dt<=config['max_gap_seconds'] and p['time'] in transformed and previous['time'] in transformed:
                    step=math.dist(transformed[p['time']],transformed[previous['time']])/dt
                    if step>config['max_speed_mps']:issues.append('IMPLAUSIBLE_SPEED_OR_ID_JUMP')
            # Geometric proposal estimate can use estimated points, but remains explicitly draft.
            geometric=[{**q,'verified':True,'observed':True,'identity_verified':True} for q in points]
            draft=individual_speed(geometric,p['time'],1,config['max_gap_seconds'],calibration)
            checked=individual_speed(points,p['time'],1,config['max_gap_seconds'],calibration)
            window=[q for q in points if p['time']-1<=q['time']<=p['time'] and q['time'] in transformed]
            if len(window)>2:
                distance=sum(math.dist(transformed[a['time']],transformed[b['time']]) for a,b in zip(window,window[1:]) if b['time']-a['time']<=config['max_gap_seconds'])
                displacement=math.dist(transformed[window[0]['time']],transformed[window[-1]['time']])
                if displacement<=config['jitter_displacement_m'] and distance>config['jitter_path_m']:issues.append('STATIONARY_JITTER_OR_TURNBACK')
            flags.update(issues)
            rows.append({'frame_id':p['frame_id'],'time':p['time'],'world':transformed.get(p['time']),'draft_speed':draft['value'],'reviewed_speed':checked['value'],'valid_speed':checked['value'] is not None,'coverage':checked['coverage'],'step_speed':step,'flags':issues})
        result[tid]={'rows':rows,'flags':sorted(flags)}
    return {'status':'DRAFT_NOT_GT','tracks':result,'config':config,'heuristics':'REVIEW_HINTS_ONLY_NOT_MEASUREMENT_GATES','calibration_valid':bool(valid)}
