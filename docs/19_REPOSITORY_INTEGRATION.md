# 19 — Repository Integration & Legacy Crawler Reuse

**Version:** 1.0 · **Status:** Proposed / requires code audit · **Scope:** existing `feature/fnb-data-platform` working tree
**Related:** `05_SYSTEM_ARCHITECTURE.md`, `09_ROADMAP.md`, `16_GOVERNANCE_SECURITY.md`, `20_CODEX_WORKFLOW.md`

## 1. Hiện trạng đã biết

Từ ảnh VS Code của nhánh feature (chưa có quyền đọc source trong repo), ta thấy:

| Path | Vai trò dự kiến | Điều phải xác minh |
|---|---|---|
| `config/` | Runtime settings, dataset/category/boundary config | Các cấu hình có hard-code secret/path không? |
| `crawl_experiment/` | Crawler đã chuyển từ nhánh dự án trước | Entry points, deps, ownership, source-rights gates, trạng thái SQLite/Parquet |
| `data/` | Local lake / fixtures / raw inputs | Đang ignored; dữ liệu nào là thật, permissions, backup, dung lượng |
| `docs/` | Tài liệu thiết kế và runbooks | Giữ `01–04`, thêm `05–20` theo version, không overwrite tùy ý |
| `output/` | Logs, metrics, artifacts, run receipts | Đang ignored; không làm mất evidence |
| `pipelines/` | Pipeline CLI/tasks | Entry points, current features, imports |
| `scripts/` | Utility & reproducibility commands | An toàn chạy? Có live/network side effects không? |
| `src/` | Thư viện, core models, source adapters | Public API, version/dependency boundaries |
| `tests/` | Tests cũ và tests mới | Offline vs live, fixtures, hiện trạng pass/fail |
| `pyproject.toml` | Python packaging | Python version, reproducible lock, local extras |
| `README.md` | Repo entry point | Existing setup instructions; sync khi doc được duyệt |
| `REUSE_MANIFEST.json` | Inventory code được chuyển | Kiểm tra integrity/checksum và ý nghĩa từng mục |
| `.gitignore`, `LICENSE`, `NOTICE.md` | Ignore/rights/attribution | Chưa tự động giả định quyền chia sẻ derived datasets |

**Ảnh không xác nhận nội dung file hoặc runnable status**. `M` = modified tracked, `U` = untracked; icon ignored ở `data/` và `output/` cần kiểm tra bằng `git status --ignored`. Không coi file untracked là đã backup/commit.

## 2. M0 audit — bắt buộc trước khi sửa code

1. Chụp inventory `git status --short`, `git status --ignored --short`, `git branch --show-current`, `git log -1 --oneline`, `git worktree list` và liệt kê paths (không tiết lộ secrets).
2. Đọc `README.md`, `REUSE_MANIFEST.json`, `pyproject.toml`, entry points và CI config nếu có.
3. Nhận diện code đang dùng, code legacy, code không rõ, files sensitive, external services, trạng thái restriction/HOLD; **không chạy live crawler khi audit**.
4. Map dependency `crawl_experiment` → `src`/`pipelines`/`scripts`; chỉ đề xuất reuse/refactor sau khi hiểu import graph.
5. Inventory runtime SQLite `.db/.sqlite/.sqlite3`, `-wal`, `-shm`, Parquet, JSON manifests, ignored datasets, model artifacts, cookies/credentials (chỉ đường dẫn và trạng thái, không in secret).
6. Kiểm tra tests theo nhóm. Chỉ chạy tests offline đã chứng minh không chạm live systems; nếu không chắc, báo `UNVERIFIED` và đề xuất smoke run sau.
7. Trả report **trong chat**: VERIFIED / UNVERIFIED / BLOCKED, risks, preservation plan và tasks M1. Chỉ lưu `output/audits/{audit_id}/` sau khi owner cho phép ghi file. **Không sửa source trong M0 read-only**.

## 3. Bảo toàn dữ liệu/legacy state

- **Không** `git clean -fdx`, `git reset --hard`, `git checkout -- .`, `git stash -u` hoặc xóa worktree/hardlink khi chưa có kế hoạch và approval.
- Branch/commit **không** bảo vệ file ignored và có thể không chứa SQLite/WAL, data dumps, logs, browser profile, outputs.
- Với SQLite đang mở: backup nhất quán bằng SQLite backup API hoặc ngừng writer đúng cách; không copy riêng `.db` khi WAL còn pending. Giữ manifest checksums và thử restore vào bản sao ở nơi cách ly.
- Giữ `LICENSE`/`NOTICE` và nguồn gốc code. Không tự reset quyền truy cập, thay credential/VM hay bypass crawler restriction.
- `data/`/`output/` và secrets không đưa vào Git khi chưa có phân loại quyền; backup riêng theo dung lượng và retention policy.

## 4. Kiến trúc module và ranh giới reuse

```text
existing source/crawler                         new data platform
crawl_experiment/ --[audited adapter]----> pipelines/review_ingest/   (Release 2)
                                              [rights + live gate]

src/poi_sources/{overture,osm}/ ----------> pipelines/poi_ingest/    (M1)
config/{geography,categories}/ ------------> pipelines/normalize/    (M1)
src/entity_resolution/ ------------------> pipelines/entity_match/   (M2)
src/catalog/ + quality gates ------------> pipelines/publish/        (M3)
                                             |
                                             v
                               data/{bronze,silver,gold}/ Parquet
```

Sơ đồ này là **gợi ý namespace**, không yêu cầu di chuyển code tự động. Giữ backward-compatibility hoặc migration adapter khi có consumers hiện hữu.

**Tách ba nhóm state:** (a) raw/analytical datasets ở Parquet, (b) operator approvals/ER manual decisions ở nguồn lưu bền vững có version, (c) crawler job state ở SQLite legacy hoặc PostgreSQL khi shared control plane thật sự cần. Không biến `.duckdb` thành bản copy authoritative thứ hai của Gold.

## 5. Safe development sequence

- **Task 0 (read-only):** audit, risk inventory, run plan; không mutation.
- **Task 1 (approved):** baseline project config, fixtures, adapters; add tests; không ảnh hưởng crawler cũ.
- **Task 2:** ingest Overture/OSM Khánh Hội theo manifest/provenance, data quality report.
- **Task 3:** normalize + matching + labeled evaluation.
- **Task 4:** catalog + publication + SQL consumer acceptance.
- **Task 5+ (gated):** snapshot diffs, rights-cleared review integration, ops; không biến các bước này thành điều kiện hoàn thành MVP.

Mỗi task phải có path scope, commands/tests, rollback strategy, modified files, evidence. `VERIFIED` chỉ khi command/tests và artifacts được thực thi tại environment phù hợp.

## 6. Done criteria của tích hợp repo

- `git diff --name-status` và `git status` rõ ràng; không mất file untracked/ignored có trước task.
- Mỗi module mới có entry point có thể chạy, tests offline và biết dữ liệu/manifest nào là đầu vào.
- Không chạy live APIs/crawlers, cloud billing hoặc xóa dữ liệu khi chưa được ủy quyền.
- Existing crawler chưa bị rewrite/disable; nếu audit thấy blocker thì ghi `BLOCKED` thay vì lách.
- Docs + ADR cập nhật khi quyết định thay đổi; không tuyên bố đã deploy/scale khi chưa kiểm chứng.
