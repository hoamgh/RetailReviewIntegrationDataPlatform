# 16 — Data Governance, Rights & Security

**Version:** 1.0 · **Status:** Proposed · **Related:** `01_BRD.md`, `10_DATA_SOURCES.md`, `12_ORCHESTRATION_OPERATIONS.md`

## 1. Source registry and allowed use

Before ingestion, create a registry per dataset with `source`, `release`, `owner/provider`, `access_method`, `license`, `access_terms_checked_at`, `storage_allowed`, `redistribution_allowed`, `derivatives_allowed`, `attribution_required`, `retention_limit`, `approval_status`, `contact/decision_reference`. **Unknown rights = blocked for publication**, not automatic permission.

- **OSM:** ODbL attribution and share-alike obligations may apply to derived databases; publishing a mixed canonical database demands a separate license review.
- **Overture:** different source providers and licensing conditions can apply; preserve per-record source lineage and exact release information.
- **Google Maps / TikTok:** existing crawler code does not establish permission to scrape, store, cache, publish or redistribute reviews. Do not bypass access gates, use alternate credentials to evade restrictions or resume blocked collection without authorization.
- **Foursquare:** excluded from new scope. Remove access token from shell/process after experiment; never commit tokens.

This doc records engineering controls, not a legal opinion; unresolved licensing remains a blocking review item before publishing derived datasets outside internal experimentation.

## 2. Classification and retention

| Class | Examples | Default handling |
|---|---|---|
| Public/open source POI | permitted OSM/Overture extracts | Keep attribution and source/version; share only under terms |
| Restricted/provider content | third-party reviews, media, contact details where restricted | Store only if allowed; restrict access/retention |
| Internal operational | job states, errors, manifests, human decisions | Internal use; controlled access and backup |
| Secrets | API tokens, service account keys, cookies | Environment/secret store only; never Git, logs or Parquet |

Review-derived analytics may remain restricted if derived from licensed material; never assume aggregation automatically removes all obligations.

## 3. Data lineage and auditability

Store provenance at three levels: **source snapshot → normalized source record → canonical store/source link → gold data product**. Every publication has `publication_id`, code revision, rule versions, source snapshot digests, output counts and quality results. For manually accepted matches keep actor (or local pseudonym), timestamp, reason, prior/new decision and evidence.

## 4. Security controls

- `data/`, `output/`, local state DBs, cookies and `.env` stay out of Git. Review `.gitignore`, do not rely on ignore alone for existing tracked files.
- Never print `$HF_TOKEN`, Google cookies, auth headers or full URLs containing keys. Use masked logs and short-lived/least-privilege credentials where possible.
- Separate paths for Bronze raw, publications, logs, secrets; least privilege for readers vs writer; access rights of imported data persist downstream.
- Sanitize untrusted source fields against CSV/formula injection and HTML rendering injection in any future UI.
- Sensitive personal review content: minimize, restrict, define retention and deletion; do not build cross-source personal profiles.
- Avoid untrusted files executing scripts/macros; validate archive extraction paths, file size and schema.

## 5. Backup and recovery

MVP: persist source manifest checksums, immutable publications, ER decision files and off-machine backup **when risk warrants**. SQLite WAL database from legacy crawler must be backed up consistently (e.g., SQLite backup API, not ad hoc copy while writes active). A Git branch **does not** protect ignored/untracked runtime data. Test restore of at least one small publication and decisions file.

## 6. Source change / deletion requests

Data source snapshot differences classify `new`, `changed`, `missing_in_new_snapshot`; **missing is not proof of closure**. If rights revoked or deletion required, identify affected source records/publications via lineage, isolate consumers, follow compliance decision and re-publish or suppress as needed. Do not promise endless immutable retention for restricted content.

## 7. Governance gates

1. Check approved sources and permitted usage before obtaining data.
2. Lock schema/versions and ensure no secrets/PII leak into logs.
3. Run data quality and identity integrity checks before publication.
4. Review mixing and attribution obligations before sharing catalog publicly.
5. Maintain provenance, reproducible commands and change/incident log.
