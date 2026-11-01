# Source selection decision

**Decision: INCONCLUSIVE** (current evidence). Overture is the project-preferred reproducible baseline; it is not ground truth. Foody contains potentially useful price, cuisine and displayed review-count fields, but there are no independently verified active novel POIs or validated enrichment joins.

| Dimension | Overture | Foody |
|---|---|---|
| Scope | 2026-10-21.0 District 4 strict F&B, re-clipped to Khánh Hội: 725 | first 1,000 detailed discovery URLs, re-clipped: 557 |
| Quality | [`quality_metrics.csv`](quality_metrics.csv) and the detailed profile | [`quality_metrics.csv`](quality_metrics.csv) and the detailed profile |
| Freshness | record provenance update timestamps where present; active status unverified | collection timestamps; active status unverified |
| Attributes | names, taxonomy, address, optional contact and source provenance | names, address, price/cuisine/review-count signals |
| Matching | 2854 candidate pairs jointly; no verified overlap | same unresolved entity decisions |
| Reliability | repeatable local Parquet; extraction/API reliability not measured here | discovery incomplete; pagination/access failures recorded |
| Cost | UNKNOWN | UNKNOWN |
| Rights | record-specific source licenses require review | collection/storage/commercial rights UNVERIFIED |

Risks: wrong branch/entity joins, stale listings, Foody discovery bias, unknown operating status, rights limitations, and browser maintenance effort. No arbitrary threshold or automatic production ingestion decision is supported.

1. Independently label the review queue, including branch identity and open/closed/unknown status with source and date.
2. Resolve Foody permitted storage/commercial use and Overture record-level attribution obligations.
3. Once labels exist, quantify validated uplift/enrichment and maintenance effort using a comparable, less biased geography sample.
