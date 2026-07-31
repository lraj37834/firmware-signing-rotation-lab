# Author Notes — Firmware Release Publisher Task

## Task Overview

This benchmark task evaluates a candidate's ability to build an automated firmware release publishing pipeline that handles key rotation, data reconciliation in an embedded database (DuckDB), cryptographic detached signing (OpenSSL CMS), HTTP service integration, and idempotent local persistence.

Release engineering recently rotated the firmware code-signing key. Legacy release publishers continue to attempt signing with the now-revoked keypair, resulting in `UNTRUSTED_SIGNATURE` rejections from the distribution gateway. The candidate must write the publisher module at `/app/publisher/release-publisher.mjs` to resolve the incident.

---

## Skills & Capabilities Evaluated

1. **Data Ingestion & SQL Reconciliation:** Using DuckDB to load flat CSV manifests, filter out duplicate manifest records, and process withdrawal records (`record_type = 'WITHDRAWAL'`, referencing `supersedes_id`) to derive publishable release bundles.
2. **Cryptographic Signing & Key Rotation:** Fetching active signing key metadata from an HTTP gateway (`GET /v1/signing-key/current`), constructing canonical JSON descriptors, and producing OpenSSL CMS detached signatures using the active PEM keypair.
3. **HTTP Service Integration & Idempotency:** POSTing signed release descriptors to the Express distribution gateway (`POST /v1/publications`), handling server receipts, using deterministic idempotency tokens (`token-<bundle_id>`), and persisting state in DuckDB so re-running the publisher is idempotent.
4. **Deterministic CLI Output:** Emitting exact, stable status lines to stdout that match the expected golden file (`reports/publications.expected.txt`).

---

## Workspace & Environment Structure

```text
/app
├── task.toml                       # Harbor task configuration
├── instruction.md                  # Comprehensive task specification
├── AUTHOR_NOTES.md                 # Author & benchmark documentation
├── package.json                    # Defines `npm run report` and duckdb dependency
├── fixtures/
│   └── build_manifest.csv          # Raw build and withdrawal records
├── reports/
│   └── publications.expected.txt   # Golden CLI reference output
├── keys/
│   ├── current/                    # Active keypair (current.key.pem, current.cert.pem)
│   └── revoked/                    # Rotated-out keypair (causes UNTRUSTED_SIGNATURE)
├── distribution-gateway/           # Express gateway service (port 7070)
├── publisher/
│   ├── release-publisher.mjs       # Candidate deliverable (ESM JS entrypoint)
│   └── release_publisher.py        # Python backend module called by Node entrypoint
├── solution/
│   ├── publish.sh                  # Harness entrypoint that deploys reference solution
│   ├── release-publisher.mjs       # Reference solution Node wrapper
│   └── release_publisher.py        # Reference solution Python backend
└── tests/
    ├── test.sh                     # Verifier test harness
    └── test_outputs.py             # Pytest suite asserting 6 functional criteria
```

---

## Key Design Considerations & Edge Cases

- **Exact Descriptor Canonicalization:** The signed bytes and sent JSON string must be identical (UTF-8, sorted keys, no insignificant whitespace). Any mismatch will cause OpenSSL signature verification to fail on the gateway.
- **Withdrawal Semantics:** A withdrawal row cancels a specific `BUILD` entry via `supersedes_id`. If a bundle's builds are all withdrawn (e.g. `BND-104`), it has 0 surviving builds and must NOT be published.
- **Duplicate Manifest Rows:** Flat CSV rows identical across all columns represent duplicate manifest emissions and must be collapsed into a single record.
- **Revoked Key Trap:** Signing with keys in `/app/keys/revoked/` reproduces the production incident (`UNTRUSTED_SIGNATURE`). The solution must dynamically discover and use `/app/keys/current/`.
- **Idempotency:** Local storage in `/app/releases.duckdb` prevents duplicate POST requests on repeated execution. The gateway ledger in `/app/distribution-gateway/data/gateway.json` records 3 unique publications.

---

## Verification & Proof Testing

The verifier (`tests/test.sh` driving `tests/test_outputs.py`) evaluates 6 distinct criteria:
1. `test_report_output_matches`: Standard output of `npm run report` matches golden output (masked receipt IDs).
2. `test_withdrawals_and_duplicates_reconciled`: Reconciled publishable set includes `BND-101..103` and excludes `BND-104`.
3. `test_bundles_signed_with_current_key_accepted`: Submissions to `/v1/publications` return `STATUS=PUBLISHED`.
4. `test_receipts_and_tokens_persisted_in_duckdb`: Table `publications` in `releases.duckdb` holds receipts and tokens.
5. `test_idempotent_rerun_no_duplicate_publications`: Re-running `npm run report` is idempotent and produces no duplicate publications on the gateway.
6. `test_revoked_key_signature_rejected`: Submitting a revoked-key signature is rejected by gateway with `UNTRUSTED_SIGNATURE`.

### Proof Criteria:
- **Proof A (Empty / Untouched State):** Without `publisher/release-publisher.mjs` implemented, running `tests/test.sh` produces `0` in `/logs/verifier/reward.txt`.
- **Proof B (Completed Solution):** Executing `solution/publish.sh` deploys the reference solution to `publisher/`, after which running `tests/test.sh` produces `1` in `/logs/verifier/reward.txt`.
