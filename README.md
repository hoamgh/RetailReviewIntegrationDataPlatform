# F&B Data Platform

Overture + OpenStreetMap → Bronze Parquet → catalog → Entity Resolution → Silver/Gold → Recommendation.

## Working now

- Existing Overture release/bbox reader, reused from the archived source CLI.
- Raw Parquet snapshots on explicit download, fresh directory only; read-back and SHA256 metadata.
- Existing taxonomy filtering, WGS84 point-in-polygon selection, stable source place keys and typed catalog persistence.
- Existing OpenStreetMap-derived Khánh Hội boundary and its attribution/provenance.
- Catalog manifest checksum/schema/identity validation and focused tests.

The OSM boundary is cached data, not a new OSM POI downloader. Cross-source entity resolution, Silver/Gold analysis and recommendations remain future work. No browser, Google/TikTok crawler, Qwen or Harness is imported. The catalog schema keeps optional historical Google metadata for compatibility; it does not perform Google collection.

## Layout

- `src/fnb_platform/prepare_catalog.py`: offline/cache or explicit Overture download CLI.
- `src/fnb_platform/catalog.py`, `models.py`, `categories.py`: reused source-independent catalog code.
- `config/admin_areas/`: existing boundary and OSM metadata.
- `data/bronze/overture/`: local ignored raw snapshot and source metadata.
- `data/place_catalog/khanh_hoi/`: local ignored canonical Parquet and manifest.
- `pipelines/`: reserved later stages; no new execution framework.
- `REUSE_MANIFEST.json`: verified archive mappings/hashes and explicit provenance gaps.

## Baseline environment and offline checks

CPython 3.12.7 on Windows is the measured baseline. Core/test versions and their
runtime dependency closure are pinned in `requirements/offline-baseline.txt`;
project metadata pins the same direct dependencies. Artifact hashes and a fresh
environment installation have not been verified. No packages were installed by M0.2B.
The optional download extra is outside the offline dependency closure.

Once these dependencies are available in your chosen Python environment:

```powershell
python -B scripts/run_offline_tests.py
$env:PYTHONPATH = "$PWD\src"
python -B -m fnb_platform.prepare_catalog
```

The first command runs fixtures with socket access blocked and external pytest
plugins disabled. It retains temporary evidence under ignored `output/baseline-tests-*`.
The second command reads cached input and **writes the catalog**; it is not a test.
Source data is not supplied by Git. M0.1 verified 378,036 Bronze rows and 682 unique
catalog keys from release 2026-09-23.1, retrieved in September 2026. This is not a
complete count of actual businesses. The current full backup was skipped by owner
instruction; Git does not protect ignored data, profiles or checkpoints.

## Optional explicit network download

Use the existing official overturemaps client with a release you deliberately select, and a fresh Bronze directory:

```powershell
python -B -m fnb_platform.prepare_catalog --download --release <release> --bronze-dir data/bronze/overture/<new-snapshot> --output data/place_catalog/<new-snapshot>/canonical_places.parquet
```

`--ward` selects a different valid boundary. The CLI queries its bounding box, stores raw data, then applies the polygon for the catalog. Existing snapshot directories are refused; failed/incomplete snapshots are retained and are not treated as completed. SQLite and Parquet are not claimed to form a single transaction. No real download was executed in the restoration task; the download path was tested using controlled batches.

## Tests

Run `python -B scripts/run_offline_tests.py`. See `docs/_delivery/STATUS.md` for the
executed command, measured result and remaining environment limitations.

## Legacy recovery

Archive branch `archive/legacy-crawlers-20261028`, commit `2f0f8ecc2ff5d427d7d7efaa7be46a848d2d16c5`. Google/TikTok/Harness/Qwen remain recoverable there and in their original worktrees. Do not reset dirty historical worktrees or delete the shared Git common directory. No remote publication was performed.

## Attribution

LICENSE is unchanged. Preserve Overture source-specific attribution and OpenStreetMap contributors/ODbL metadata when using the data. No datasets are tracked by Git.
