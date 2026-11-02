# F&B Data Platform

**A reproducible, quality-aware data pipeline for discovering and integrating food & beverage places in Ho Chi Minh City, Vietnam.**

This project studies how to build a trustworthy local F&B place catalog from heterogeneous sources, measure source quality, resolve duplicate business listings at the **branch/location** level, and prepare reliable datasets for future recommendations.

**Status:** research and engineering in progress. The F&B platform replaces the earlier crawler-only foundation through reviewed source and experimental workstreams.

## Problem

A place may appear under different names, addresses, coordinates, and categories across providers. A high raw POI count does not prove that the records are active, unique, complete, or licensed for downstream use. The platform therefore evaluates sources **before** combining them, keeps uncertain identity decisions reviewable, and records evidence for every published snapshot.

## Data sources and roles

| Source | Role | Current interpretation |
| --- | --- | --- |
| **Overture Maps Places** | Preferred starting catalog | Release-aware snapshots, category profiling, geographic filtering and stable source IDs; not assumed to be ground truth. |
| **Foody** | Candidate supplementary POIs and enrichment | Experimental discovery/details and offline profiling; incomplete coverage and reuse rights must be verified before production use. |
| **Google Maps** | Optional identity validation and review enrichment | Existing experimental crawler and matching probes; search results and shared Google identifiers alone are insufficient to confirm matches. |
| **OpenStreetMap** | Geographic boundary/provenance; historical POI experiment | Khánh Hội boundary support; OSM POI coverage was relatively low in the pilot, so it is not the primary catalog source. |

Source-specific attribution, retention and permitted-use requirements apply. **No source is treated as a complete or verified list of operating restaurants.**

## Architecture

```text
Overture release snapshot        Foody experimental snapshot
          |                                |
          v                                v
    Raw / Bronze Parquet          Raw observations + provenance
          |                                |
          +------------+-------------------+
                       v
          Profiling & quality validation
                       |
                       v
          Geographic scope / normalization
                       |
                       v
            Candidate generation (ER)
                       |
                       v
          Match / Non-match / Human review
                       |
                       v
              Silver place catalog
                       |
                       v
       Gold analytics / recommendation (planned)
```

**Python + Parquet + DuckDB** are the primary local tools. Published datasets are designed around versioned snapshots, source provenance, quality checks, and atomic publication. No distributed infrastructure is needed for the current pilot.

## Measured pilot evidence

The current evaluation focuses on **Phường Khánh Hội** within the historical District 4 study area. A local offline comparison of the Overture October 2026 snapshot and an **incomplete, discovery-biased** Foody sample observed:

| Measure | Observed |
| --- | ---: |
| Overture F&B records inside the same pilot polygon | 725 |
| Foody records inside the same pilot polygon | 557 |
| Direct matching candidate pairs generated | 2,854 |
| Manual review rows prepared (not independently labeled) | 169 |

These are **sample measurements**, not confirmed unique restaurants or verified cross-source matches. The earlier Overture baseline of 682 refers to a different snapshot/filter and should not be compared as if equivalent. Foody's incremental verified active POIs, overlap precision, and commercial reuse status remain **unverified**. The source-selection decision is currently **INCONCLUSIVE**.

Experimental measurements and artifacts reside in the local, work-in-progress [source-evaluation area](experiments/source_evaluation); not all local results are published to GitHub.

## Repository guide

The current F&B development branch contains:

- `src/fnb_platform/` — source adapters, typed catalog, geography, and offline preparation.
- `config/admin_areas/` — pilot boundary and geographic provenance.
- `scripts/run_offline_tests.py` — focused offline test runner.
- `docs/` — source contracts, pipeline decisions, and delivery checkpoints.
- `experiments/` — isolated source profiling, matching and crawler feasibility experiments where committed.

Large raw data, browser profiles, credentials, checkpoints, and generated output stay outside Git.

## Run the offline checks

From the repository root, in a Python environment with the dependencies declared in `pyproject.toml`:

```powershell
python -B scripts/run_offline_tests.py
```

A separate offline catalog-preparation command reads cached inputs and **writes** output, so it should only be run when the required local data is present. Live crawlers, network acquisition and browser sessions are never prerequisites for the offline tests.

## Roadmap

1. Finalize reproducible source inventory, data profiling, release-to-release change detection and usage-rights assessment.
2. Independently validate a stratified sample of Foody–Overture entity matches, branch identity, and operating status.
3. Promote validated records through Silver/Gold datasets and evaluate incremental value for discovery and recommendations.

## Scope and limitations

This is a **data engineering project under development**, not a deployed recommendation service. Current crawler experiments, tentative Google identity links and unlabeled ER candidates are not production-quality truth. The project tracks data quality, input fingerprints and source changes so future scheduled jobs can detect changes safely rather than assume every restaurant updates on each release.

See [delivery status](docs/_delivery/STATUS.md) and the [source evaluation experiments](experiments/source_evaluation) for implementation evidence. Earlier crawler work remains available in archive history. Dataset licensing and third-party terms must be reviewed before redistribution or commercial use.
