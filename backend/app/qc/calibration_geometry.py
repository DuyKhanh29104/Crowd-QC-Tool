"""Reconstruct a convex planar quadrilateral from measured edges/diagonals."""
import math

from backend.app.roi.geometry import cross


def right_angle_world(lengths):
    """A=(0,0), B=(AB,0), D=(0,DA); pick C opposite A across BD."""
    if not lengths or any(key not in lengths or not math.isfinite(lengths[key]) or lengths[key]<=0
                          for key in ('ab','bc','cd','da')):
        raise ValueError('Cần bốn cạnh AB, BC, CD, DA lớn hơn 0, tính bằng mét; góc A vuông ngoài thực tế.')
    ab,bc,cd,da=(lengths[key] for key in ('ab','bc','cd','da'))
    bd=math.hypot(ab,da)
    if not abs(bc-cd)<bd<bc+cd:
        raise ValueError('BC và CD không tạo được tam giác BCD với BD suy từ góc vuông A. Hãy kiểm tra bốn cạnh.')
    along=(bc*bc-cd*cd+bd*bd)/(2*bd)
    height2=bc*bc-along*along;scale=max(ab,bc,cd,da,bd)
    if height2<=1e-12*scale*scale:
        raise ValueError('Tứ giác quá bẹt hoặc gần thẳng hàng để hiệu chuẩn ổn định.')
    height=math.sqrt(height2)
    cx=ab+(-ab*along+da*height)/bd
    cy=(da*along+ab*height)/bd
    world=[[0.,0.],[ab,0.],[cx,cy],[0.,da]]
    if any(cross(world[i-1],world[i],world[(i+1)%4])<=1e-10*scale*scale for i in range(4)):
        raise ValueError('Bốn cạnh và góc vuông A không tạo tứ giác lồi theo thứ tự A → B → C → D.')
    return world,{'right_angle_at':'A','assumed_angle_degrees':90,'angle_source':'USER_DECLARED',
                  'fit_lengths':['AB','BC','CD','DA'],'derived_diagonals_m':{'ac':math.hypot(cx,cy),'bd':bd}}


def quadrilateral_world(lengths):
    if not lengths or any(key not in lengths or not math.isfinite(lengths[key]) or lengths[key]<=0
                          for key in ('ab','bc','cd','da','ac','bd')):
        raise ValueError('Cần đủ AB, BC, CD, DA, AC, BD lớn hơn 0, tính bằng mét.')
    ab,bc,cd,da,ac,bd=(lengths[key] for key in ('ab','bc','cd','da','ac','bd'))
    for a,b,c in ((ab,bc,ac),(da,cd,ac)):
        if not abs(a-b)<c<a+b:
            raise ValueError('Số đo không tạo thành hai tam giác ABC và ACD hợp lệ. Kiểm tra cạnh và AC.')
    # Fit all four sides and AC. BD is redundant and checks measurement consistency.
    cx=(ab*ab+ac*ac-bc*bc)/(2*ab)
    cy2=ac*ac-cx*cx
    along=(da*da+ac*ac-cd*cd)/(2*ac)
    height2=da*da-along*along
    scale=max(ab,bc,cd,da,ac,bd)
    if min(cy2,height2)<=1e-12*scale*scale:
        raise ValueError('Tứ giác quá bẹt hoặc gần thẳng hàng để hiệu chuẩn ổn định.')
    cy=math.sqrt(cy2);height=math.sqrt(height2)
    # D lies opposite B across AC; this fixes the convex boundary order A-B-C-D.
    dx=(along*cx-height*cy)/ac
    dy=(along*cy+height*cx)/ac
    world=[[0.,0.],[ab,0.],[cx,cy],[dx,dy]]
    if any(cross(world[i-1],world[i],world[(i+1)%4])<=1e-10*scale*scale for i in range(4)):
        raise ValueError('Các số đo không tạo tứ giác lồi theo thứ tự A → B → C → D.')
    derived=math.dist(world[1],world[3]);error=abs(derived-bd);tolerance=max(.05,.02*bd)
    if error>tolerance:
        raise ValueError(f'Số đo không nhất quán: BD nhập {bd:.3f} m, suy từ bốn cạnh và AC là {derived:.3f} m; sai lệch {error:.3f} m vượt {tolerance:.3f} m. Hãy đo lại.')
    return world,{'segment':'BD','measured_m':bd,'derived_m':derived,'error_m':error,
                  'tolerance_m':tolerance,'verified':True,'fit_lengths':['AB','BC','CD','DA','AC']}


def bird_eye_layout(calibration):
    """Uniform metric scale, preserving skew and negative world X coordinates."""
    world=calibration.get('world_points') or [[0,0],[calibration['width_m'],0],
        [calibration['width_m'],calibration['height_m']],[0,calibration['height_m']]]
    left=min(p[0] for p in world);top=min(p[1] for p in world)
    width=max(p[0] for p in world)-left;height=max(p[1] for p in world)-top
    scale=min(600/width,1200/height)
    size=(max(1,min(600,math.ceil(width*scale))),max(1,min(1200,math.ceil(height*scale))))
    return size,[[(x-left)*scale,(y-top)*scale] for x,y in world]
