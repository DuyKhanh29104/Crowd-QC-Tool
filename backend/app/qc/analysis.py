"""AI event proposals. Measurements remain estimates until human review."""
import math
import uuid
from backend.app.workflows.motion import solve,transform
from backend.app.roi.geometry import membership,validate_polygon,cross
from .motion import measure_objects
from .calibration_geometry import quadrilateral_world,right_angle_world


def calibration(points,width_m=None,height_m=None,test=None,plane='ground',reference_height_m=None,shape='rectangle',lengths_m=None):
    if len(points)!=4:raise ValueError('Cần 4 điểm A, B, C, D.')
    validate_polygon(points,max(1,max(p[0] for p in points)),max(1,max(p[1] for p in points)))
    turns=[cross(points[i-1],points[i],points[(i+1)%4]) for i in range(4)]
    if not (all(v>0 for v in turns) or all(v<0 for v in turns)):
        raise ValueError('Bốn điểm phải là tứ giác lồi theo thứ tự A → B → C → D.')
    if plane not in ('ground','head'):raise ValueError('Mặt phẳng hiệu chuẩn không hợp lệ.')
    if plane=='head' and (reference_height_m is None or not math.isfinite(reference_height_m) or reference_height_m<=0):
        raise ValueError('Hiệu chuẩn đầu cần độ cao tâm đầu tham chiếu đo bằng mét.')
    check=construction=None
    if shape in ('quadrilateral','right_angle'):
        if shape=='right_angle':world,construction=right_angle_world(lengths_m)
        else:world,check=quadrilateral_world(lengths_m)
        width_m=max(p[0] for p in world)-min(p[0] for p in world)
        height_m=max(p[1] for p in world)-min(p[1] for p in world)
    elif shape=='rectangle':
        if width_m is None or height_m is None or not all(math.isfinite(v) and v>0 for v in (width_m,height_m)):
            raise ValueError('Hình chữ nhật cần AB và BC lớn hơn 0, tính bằng mét.')
        world=[[0,0],[width_m,0],[width_m,height_m],[0,height_m]]
    else:raise ValueError('Dạng vùng hiệu chuẩn không hợp lệ.')
    a=[];b=[]
    for (x,y),(u,v) in zip(points,world):
        a.extend([[x,y,1,0,0,0,-u*x,-u*y],[0,0,0,x,y,1,-v*x,-v*y]]);b.extend([u,v])
    h=solve(a,b)+[1.]
    result={'points':points,'width_m':width_m,'height_m':height_m,'homography':h,'test':test,'distance_verified':False,
            'plane':plane,'reference_height_m':reference_height_m if plane=='head' else None,
            'shape':shape,'world_points':world,'lengths_m':lengths_m if shape!='rectangle' else None,
            'measurement_check':check,'construction':construction}
    if test and len(test.get('points',[]))==2 and test.get('expected_m',0)>0:
        if math.dist(*test['points'])<1e-6:raise ValueError('Hai điểm kiểm tra phải khác nhau.')
        if any(membership(p,points,True)!='INSIDE' for p in test['points']):
            raise ValueError('Hai điểm kiểm tra phải nằm trong vùng hiệu chuẩn.')
        if any(math.dist(p,q)<1e-6 for p in test['points'] for q in points):
            raise ValueError('Dùng hai điểm kiểm độc lập, không trùng bốn góc đã dùng để khớp phép chiếu.')
        actual=math.dist(*(transform(h,p) for p in test['points']))
        error=abs(actual-test['expected_m'])
        result.update(distance_m=actual,error_percent=100*error/test['expected_m'],distance_verified=error<=max(.05,test['expected_m']*.02))
    return result


def metrics(objects,roi,settings,previous,stamp,homography=None,motion=None):
    head=settings.get('detection_target','person')=='head'
    if motion is None:
        # Legacy callers can supply only a floor homography, never a head-plane fit.
        h=None if head else homography
        motion={'enabled':settings.get('speed',True) and settings.get('tracking',True),
                'homography':h,'metric_available':bool(h),'reason':'HEAD_CALIBRATION_REQUIRED' if head else 'GROUND_CALIBRATION_REQUIRED'}
    measure_objects(objects,roi,settings,previous,stamp,motion)
    inside=[o for o in objects if o['inside']]
    speeds=[o['speed'] for o in inside if o['speed'] is not None]
    complete=bool(inside) and len(speeds)==len(inside)
    count=len(inside);density=count/roi['area_m2']
    avg=sum(speeds)/len(speeds) if complete else None
    stationary=sum(v<settings['stationary_speed'] for v in speeds)/len(speeds) if complete else None
    label='NORMAL'
    if density>=settings['density_threshold']:
        label='CROWDED'
        if avg is not None and avg<settings['slow_speed'] and stationary>=settings['stationary_ratio']:label='CONGESTED'
    reasons=[f'{count} '+('đầu' if head else 'người')+f' ước lượng trong ROI; mật độ {density:.2f}/m².']
    if avg is None:reasons.append('Chưa đủ hiệu chuẩn đúng mặt phẳng hoặc lịch sử track để ước lượng tốc độ toàn nhóm.')
    else:reasons.append(f'Tốc độ trung bình {avg:.2f} m/s; {stationary:.0%} gần như đứng yên.')
    if head and motion['metric_available']:reasons.append('Tốc độ xấp xỉ theo mặt phẳng tâm đầu tham chiếu; phụ thuộc chiều cao và tư thế người.')
    pixel_speeds=[o['speed_px_s'] for o in inside if o['speed_px_s'] is not None]
    return {'time':stamp,'count':count,'density':density,'speed':avg,'stationary':stationary,'label':label,'reasons':reasons,
            'speed_valid_count':len(speeds),'speed_coverage':len(speeds)/count if count else 0,
            'speed_px_s':sum(pixel_speeds)/count if count and len(pixel_speeds)==count else None}


def candidates(rows,settings,duration):
    result=[];group=[]
    def emit():
        if not group:return
        start=group[0]['time'];end=min(duration,group[-1]['time']+1/settings['sampling_fps'])
        label=group[0]['label']
        if label=='CONGESTED' and end-start<settings['min_duration']:label='CROWDED'
        speeds=[r['speed'] for r in group if r['speed'] is not None];stationary=[r['stationary'] for r in group if r['stationary'] is not None]
        result.append({'id':uuid.uuid4().hex,'start':start,'end':end,'label':label,'peak_count':max(r['count'] for r in group),
                       'avg_speed':sum(speeds)/len(speeds) if len(speeds)==len(group) else None,
                       'stationary':sum(stationary)/len(stationary) if len(stationary)==len(group) else None,
                       'reasons':list(dict.fromkeys(group[max(range(len(group)),key=lambda i:group[i]['count'])]['reasons']))+[f'Tín hiệu duy trì {end-start:.1f} giây.'],
                       'qc_status':'PENDING'})
    for row in rows:
        if row['label']=='NORMAL':emit();group=[];continue
        if group and (row['label']!=group[-1]['label'] or row['time']-group[-1]['time']>2/settings['sampling_fps']):emit();group=[]
        group.append(row)
    emit();return result
