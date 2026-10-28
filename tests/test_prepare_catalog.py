"""Reused entrypoint, fixture-only download and snapshot validation."""
import hashlib
import json
from types import SimpleNamespace
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import Point

from fnb_platform import prepare_catalog as cli
from fnb_platform.catalog import load_catalog


def inputs(tmp_path):
    ward=tmp_path/'ward.json'
    ward.write_text(json.dumps({'type':'Feature','properties':{
        'admin_area_id':'fixture','admin_area_name':'Fixture','is_current':True},
        'geometry':{'type':'Polygon','coordinates':[[[0,0],[2,0],[2,2],[0,2],[0,0]]]}}))
    table=pa.Table.from_pylist([{'id':'fixture:1','names':{'primary':'Cafe'},
        'geometry':Point(1,1).wkb,'taxonomy':{'primary':'coffee_shop','hierarchy':['food_and_drink','coffee_shop']},
        'addresses':[{'freeform':'123 Fixture'}],'confidence':0.8}])
    return ward,table


def test_cached_cli_and_source_not_overwritten(tmp_path):
    ward,table=inputs(tmp_path)
    raw=tmp_path/'raw.parquet';pq.write_table(table,raw)
    checksum=hashlib.sha256(raw.read_bytes()).hexdigest()
    metadata=tmp_path/'source.json';metadata.write_text(json.dumps(dict(source_release='fixture-release',artifact_sha256=checksum)))
    output=tmp_path/'catalog.parquet'
    assert cli.main(['--source',str(raw),'--source-metadata',str(metadata),
                     '--ward',str(ward),'--output',str(output)])==0
    assert len(load_catalog(output).places)==1
    assert hashlib.sha256(raw.read_bytes()).hexdigest()==checksum
    with pytest.raises(SystemExit):
        cli.main(['--source',str(raw),'--output',str(raw)])
    assert hashlib.sha256(raw.read_bytes()).hexdigest()==checksum


def test_download_uses_existing_client_and_preserves_raw_bronze(tmp_path,monkeypatch):
    ward,table=inputs(tmp_path);calls=[]
    def batches(kind,**kwargs):
        calls.append((kind,kwargs))
        return SimpleNamespace(read_all=lambda:table)
    monkeypatch.setitem(sys.modules,'overturemaps',SimpleNamespace(record_batch_reader=batches))
    monkeypatch.setattr(cli.importlib.metadata,'version',lambda _: 'fixture-client')
    bronze=tmp_path/'bronze';output=tmp_path/'catalog.parquet'
    args=['--download','--release','fixture-release','--bronze-dir',str(bronze),
          '--ward',str(ward),'--output',str(output)]
    assert cli.main(args)==0
    assert calls[0][0]=='place' and calls[0][1]['bbox']==(0.0,0.0,2.0,2.0)
    assert calls[0][1]['release']=='fixture-release' and len(calls)==1
    assert pq.ParquetFile(bronze/'places.parquet').read().equals(table)
    info=json.loads((bronze/'source_metadata.json').read_text())
    assert info['artifact_sha256']==hashlib.sha256((bronze/'places.parquet').read_bytes()).hexdigest()
    assert len(load_catalog(output).places)==1
    with pytest.raises(SystemExit):cli.main(args)
    assert len(calls)==1  # Existing snapshots cannot trigger another request.


def test_corrupt_bronze_refuses_before_catalog_write(tmp_path):
    ward,table=inputs(tmp_path);raw=tmp_path/'raw.parquet';pq.write_table(table,raw)
    metadata=tmp_path/'source.json';metadata.write_text(json.dumps(dict(source_release='fixture',artifact_sha256='0'*64)))
    output=tmp_path/'catalog.parquet'
    with pytest.raises(ValueError,match='checksum'):
        cli.main(['--source',str(raw),'--source-metadata',str(metadata),'--ward',str(ward),'--output',str(output)])
    assert not output.exists()


def test_download_requires_explicit_release_and_fresh_bronze(tmp_path):
    with pytest.raises(SystemExit):cli.main(['--download'])
    with pytest.raises(SystemExit):cli.main(['--download','--release','fixture'])
