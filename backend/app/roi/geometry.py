import math
from backend.app.errors import DomainError


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def on_segment(a, b, p):
    return abs(cross(a,b,p)) <= 1e-8 and min(a[0],b[0])-1e-8 <= p[0] <= max(a[0],b[0])+1e-8 and min(a[1],b[1])-1e-8 <= p[1] <= max(a[1],b[1])+1e-8


def intersects(a,b,c,d):
    if any((on_segment(a,b,c), on_segment(a,b,d), on_segment(c,d,a), on_segment(c,d,b))):
        return True
    return (cross(a,b,c)>0) != (cross(a,b,d)>0) and (cross(c,d,a)>0) != (cross(c,d,b)>0)


def validate_polygon(points, width, height):
    def fail(message):
        raise DomainError("INVALID_POLYGON", message, 422)
    if len(points) < 3 or len(points) > 200:
        fail("ROI cần từ 3 đến 200 đỉnh.")
    if any(len(p)!=2 or not all(math.isfinite(v) for v in p) for p in points):
        fail("Tọa độ phải hữu hạn.")
    if len(set(map(tuple, points))) != len(points):
        fail("ROI có đỉnh trùng.")
    if any(not (0<=x<=width and 0<=y<=height) for x,y in points):
        fail("Đỉnh ROI nằm ngoài ảnh nguồn.")
    n=len(points)
    for i in range(n):
        if abs(cross(points[i-1],points[i],points[(i+1)%n])) <= 1e-8:
            fail("Ba đỉnh liên tiếp thẳng hàng; bỏ đỉnh dư.")
        for j in range(i+1,n):
            if j == i+1 or (i==0 and j==n-1): continue
            if intersects(points[i],points[(i+1)%n],points[j],points[(j+1)%n]):
                fail("ROI tự giao hoặc chạm cạnh không liền kề.")
    return points


def membership(point, polygon, anchor_verified=False):
    if point is None or not anchor_verified:
        return "UNCERTAIN"
    inside=False
    x,y=point
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        if on_segment(a,b,point): return "INSIDE"
        if (a[1]>y)!=(b[1]>y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            inside=not inside
    return "INSIDE" if inside else "OUTSIDE"
