# Delivery status

Updated: 2026-10-28. Branch: feature/fnb-data-platform.
Current task: [CURRENT_TASK.md](CURRENT_TASK.md).

## VERIFIED
- M0 baseline: 53f72e0b; offline CI: 02cda33b; legacy retirement: d7277343.
- User confirms hosted Windows/Python 3.11 CI on d7277343: fresh install PASS,
  31 tests PASS. This hosted result predates the M1.1 changes.
- Archive preserved: archive/legacy-crawlers-20261028 at 2f0f8ecc.
- M0.1 historical Overture: 378,036 Bronze rows; 682 unique catalog keys;
  checksums/schema/counts verified then. Not a completeness claim.
- M1.1: offline OSM node/way/relation adapter, versioned F&B mapping, source
  observations/provenance, deterministic dedup and geometry diagnostics added.
- Stable identity: osm:<type>/<id>; highest element version wins, canonical
  JSON breaks ties, output sorts by type/id. Missing version ranks zero.
- Reuses CanonicalPlace, PlaceCatalog, current WGS84 ward validator and covers.
- Existing catalog models and Overture adapter/data unchanged; no publication.
- Local CPython 3.12.7: 75 passed (44 OSM + 31 existing), 2 existing optional
  pandas dependency warnings, 0.88s; socket-blocked fixture runner.
- Executed command (existing environment, no installation):
```powershell
& C:/Users/MSI/anaconda3/python.exe -B -c "import sys,runpy; sys.path.insert(0,r'D:\Projects\integration-recomentdation\output\ci01-2ac84b90\venv\Lib\site-packages'); runpy.run_path('scripts/run_offline_tests.py',run_name='__main__')"
```
- 16 protected crawler/script file SHA-256 values match pre-task digest
  4373983ef0f29add0df315b2a93234164bb1ffecdbca7a96d957ecb3380dc2c2.
- Seven M1.1 paths approved for the local adapter commit; crawler work excluded.
- No push, source access or runtime-state changes in M1.1.

## UNVERIFIED / LIMITATIONS
- M1.1 hosted/Python 3.11 execution and fresh package build remain unverified.
- No real OSM POI extract, rights review or coverage evidence. Polygon/center
  coordinates are approximate; raw Overpass way/member geometry assembly is
  unsupported. Unknown categories are unclassified; selected retail/bar tags review.
- No ingestion publication, new Bronze files, ER or M1.2–M1.4 work performed.
- Full private backup skipped by owner; ignored/runtime assets and the 10 modified
  plus 6 untracked crawler files remain outside verified backup protection.

## Next exact action
Review M1.1; separately approve M1.2 bounded OSM acquisition/input conversion,
with cutoff, source URI, checksum, geometry assembly, rights and count profiling.
No live extraction is authorized by M1.1.
