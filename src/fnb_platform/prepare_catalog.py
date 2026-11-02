"""Prepare one reusable ward catalog from a cached snapshot or explicit Overture release."""
from __future__ import annotations

import argparse
import importlib.metadata
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .catalog import (
    DEFAULT_CATALOG, DEFAULT_SOURCE, DEFAULT_SOURCE_METADATA, DEFAULT_WARD, prepare_catalog,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--source-metadata", type=Path, default=DEFAULT_SOURCE_METADATA)
    parser.add_argument("--ward", type=Path, default=DEFAULT_WARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--download", action="store_true", help="Explicit bounded bbox access, not latest-release discovery")
    parser.add_argument("--release", help="Actual release to query; required with --download")
    parser.add_argument("--bronze-dir", type=Path, help="Fresh raw snapshot directory; required with --download")
    args = parser.parse_args(argv)
    observed_at = datetime.now(timezone.utc).isoformat()
    reader = None
    if args.download:
        if not args.release or args.bronze_dir is None:
            parser.error("--download requires an explicit --release and fresh --bronze-dir")
        if args.bronze_dir.exists():
            parser.error('Bronze directory already exists; use a fresh snapshot directory')
        raw_path = args.bronze_dir/'places.parquet'
        if args.output.resolve() == raw_path.resolve():
            parser.error('Catalog output cannot overwrite its Bronze input')
        from overturemaps import record_batch_reader
        lineage = dict(source_release=args.release, client_version=importlib.metadata.version("overturemaps"),
                       retrieved_at=observed_at, source_catalog="https://stac.overturemaps.org/catalog.json",
                       license_attribution="Overture Maps source-specific attribution: https://docs.overturemaps.org/attribution/")
        def reader(*, bbox, release):
            args.bronze_dir.mkdir(parents=True, exist_ok=False)
            batches = record_batch_reader("place", bbox=bbox, release=release, stac=True,
                                          connect_timeout=10, request_timeout=60)
            if batches is None:
                raise RuntimeError("Overture returned no reader")
            table = batches.read_all()
            # Reuse the original Arrow/Parquet snapshot path without the old
            # Google comparison experiment. Never overwrite an existing run.
            import pyarrow.parquet as pq
            temporary = raw_path.with_suffix('.parquet.tmp')
            pq.write_table(table, temporary, compression='zstd')
            restored = pq.ParquetFile(temporary).read()
            if not restored.equals(table):
                raise RuntimeError('Bronze Parquet read-back mismatch')
            metadata = dict(lineage, bbox=list(bbox), rows=table.num_rows,
                artifact_sha256=hashlib.sha256(temporary.read_bytes()).hexdigest())
            temporary.replace(raw_path)
            # A file without its metadata is not a completed snapshot.
            (args.bronze_dir/'source_metadata.json').write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            return table
    else:
        if args.output.resolve() == args.source.resolve():
            parser.error('Catalog output cannot overwrite its Bronze input')
        lineage = json.loads(args.source_metadata.read_text(encoding="utf-8"))
        if lineage.get('artifact_sha256') and hashlib.sha256(args.source.read_bytes()).hexdigest()!=lineage['artifact_sha256']:
            raise ValueError('Bronze snapshot checksum mismatch')
    _, manifest = prepare_catalog(args.source, args.ward, args.output, lineage,
                                 observed_at=observed_at, reader=reader)
    print(json.dumps(dict(catalog_path=str(args.output), **manifest), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
