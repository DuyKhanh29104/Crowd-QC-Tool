"""Idempotent Alembic upgrade for local project metadata."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.db.session import make_engine,migrate

parser=argparse.ArgumentParser()
parser.add_argument('--data-root',type=Path,default=Path('workspace_data'))
args=parser.parse_args()
engine=make_engine(args.data_root)
migrate(engine)
engine.dispose()
print('Schema at head:',args.data_root.resolve())
