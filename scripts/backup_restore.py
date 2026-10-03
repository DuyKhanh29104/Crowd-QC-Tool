import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.app.workflows.backup import backup,restore
parser=argparse.ArgumentParser(description='Consistent Crowd QC backup; restore only into a new directory.')
parser.add_argument('action',choices=['backup','restore']);parser.add_argument('source');parser.add_argument('destination');parser.add_argument('--include-sources',action='store_true')
args=parser.parse_args()
result=backup(args.source,args.destination,args.include_sources) if args.action=='backup' else restore(args.source,args.destination)
print(json.dumps(result,indent=2))
