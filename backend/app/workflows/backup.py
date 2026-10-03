"""SQLite online-backup plus hash-verified immutable artifacts; restore to a NEW directory."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import shutil
import sqlite3
import uuid
import zipfile
from backend.app.media.service import digest

def backup(root,destination,include_sources=False):
    root=Path(root).resolve();destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Backup destination must be new')
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=root/'backup_staging'/uuid.uuid4().hex;stage.mkdir(parents=True)
    with sqlite3.connect(root/'crowd_qc.sqlite') as source,sqlite3.connect(stage/'crowd_qc.sqlite') as target:source.backup(target)
    files={'crowd_qc.sqlite':stage/'crowd_qc.sqlite'};path_map={};sources=[];missing=[]
    with sqlite3.connect(stage/'crowd_qc.sqlite') as db:
        if db.execute("SELECT count(*) FROM jobs WHERE state IN ('QUEUED','RUNNING')").fetchone()[0]:raise ValueError('Finish/cancel active jobs before backup')
        for id,payload in db.execute('SELECT id,manifest FROM dataset_snapshots'):
            folder=root/'datasets'/id
            for name,checksum in json.loads(payload)['files'].items():
                path=folder/name
                if not path.is_file() or digest(path)!=checksum:missing.append(str(path))
            for path in folder.rglob('*'):
                if path.is_file():files[path.relative_to(root).as_posix()]=path
        for payload, in db.execute("SELECT payload FROM work_artifacts WHERE kind='MODEL'"):
            p=json.loads(payload);saved=p['weights_path'];source=resolve_path(root,saved)
            if not source.is_file() or digest(source)!=p['weights_sha256']:missing.append(saved);continue
            name='model_weights/'+p['weights_sha256']+'.pt';files[name]=source;path_map[saved]=name
        for id,source_path,sha in db.execute('SELECT id,source_path,sha256 FROM media'):
            row={'media_id':id,'source_path':source_path,'sha256':sha,'included':False}
            path=Path(source_path)
            if include_sources:
                if not path.is_file() or digest(path)!=sha:missing.append(source_path)
                else:
                    name='sources/'+sha+path.suffix.lower();files[name]=path;row.update(included=True,archive_path=name)
            sources.append(row)
    if missing:raise ValueError('Missing/hash-mismatched requested artifacts: '+', '.join(missing))
    manifest={'schema':'crowd_backup_v1','source_root':str(root),'sources':sources,'path_map':path_map,
        'cache':'OMITTED_REGENERABLE','resume_checkpoints':'NOT_INCLUDED; only published model checkpoints included',
        'files':{name:digest(path) for name,path in files.items()}}
    with zipfile.ZipFile(destination,'x',compression=zipfile.ZIP_DEFLATED) as z:
        for name,path in files.items():z.write(path,name)
        z.writestr('backup_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return {'path':str(destination),'sha256':digest(destination),'files':len(files),'source_count':len(sources),'sources_included':include_sources,'published_model_checkpoints':len(path_map)}

def restore(archive,destination):
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Restore destination must be a NEW directory; never overwrite a running project')
    with zipfile.ZipFile(archive) as z:
        manifest=json.loads(z.read('backup_manifest.json'));names=z.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive entries')
        if set(names)!={*manifest['files'],'backup_manifest.json'}:raise ValueError('Unexpected archive content')
        if any(name not in manifest['files'] for name in manifest.get('path_map',{}).values()):raise ValueError('Model remap references missing/unsafe archive path')
        if any(s.get('included') and s.get('archive_path') not in manifest['files'] for s in manifest.get('sources',[])):raise ValueError('Source remap references missing/unsafe archive path')
        for name,checksum in manifest['files'].items():
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:raise ValueError('Unsafe archive path')
            if hashlib.sha256(z.read(name)).hexdigest()!=checksum:raise ValueError('Backup checksum mismatch: '+name)
        destination.mkdir(parents=True)
        for name in manifest['files']:
            path=destination/name;path.parent.mkdir(parents=True,exist_ok=True)
            with z.open(name) as src,path.open('wb') as dst:shutil.copyfileobj(src,dst)
    mapping={saved:str(destination/name) for saved,name in manifest['path_map'].items()}
    (destination/'restore_paths.json').write_text(json.dumps(mapping,indent=2),encoding='utf-8')
    with sqlite3.connect(destination/'crowd_qc.sqlite') as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Restored SQLite integrity failure')
        for source in manifest['sources']:
            if source['included']:
                path=destination/source['archive_path'];stat=path.stat();db.execute('UPDATE media SET source_path=?,size_bytes=?,mtime_ns=? WHERE id=?',(str(path),stat.st_size,str(stat.st_mtime_ns),source['media_id']))
        db.commit()
    (destination/'backup_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return {'directory':str(destination),'status':'RESTORED','source_mode':'INCLUDED_OR_REFERENCED_PER_MANIFEST','files':len(manifest['files'])}

def resolve_path(root,saved):
    mapping=Path(root)/'restore_paths.json'
    if mapping.is_file():return Path(json.loads(mapping.read_text('utf-8')).get(saved,saved))
    return Path(saved)
