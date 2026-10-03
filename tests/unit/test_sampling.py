from fractions import Fraction
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session

from backend.app.db.models import Frame
from backend.app.jobs.worker import selected_frames


@pytest.mark.parametrize('step,start,end,limit', [
    ('0.3', '0', '9', 5000),
    ('0.001', '0', '9', 1201),
    ('0.07', '0.1', '0.9', 4),
    ('1', '100', '101', 5),
])
def test_exact_vfr_sampling_hydrates_only_selected_frames(step, start, end, limit):
    engine = create_engine('sqlite://')
    Frame.__table__.create(engine)
    stamps = [1000 + i * 3 + i // 7 for i in range(1201)]
    records = [dict(id=str(i), media_id='video', ordinal=i, pts=pts,
                    time_base_num=1, time_base_den=1000,
                    relative_seconds=(pts-1000)/1000) for i, pts in enumerate(stamps)]
    with engine.begin() as connection:
        connection.execute(insert(Frame), records)
    expected = []
    target = Fraction(start)
    for i, pts in enumerate(stamps):
        stamp = Fraction(pts-1000, 1000)
        if stamp > Fraction(end):
            break
        if stamp >= target:
            expected.append(str(i))
            while target <= stamp:
                target += Fraction(step)
            if len(expected) == limit:
                break
    try:
        with Session(engine) as db:
            loaded = []
            event.listen(db, 'loaded_as_persistent', lambda _, obj: loaded.append(obj.id))
            result = selected_frames(db, SimpleNamespace(id='video', start_pts=1000,
                                     time_base_num=1, time_base_den=1000),
                                     dict(every_seconds=step, start_seconds=start,
                                          end_seconds=end, max_frames=limit))
            assert [frame.id for frame in result] == expected
            assert sorted(loaded) == sorted(expected)
    finally:
        engine.dispose()
