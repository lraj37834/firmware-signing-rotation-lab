# Firmware Release Publisher — Task Instructions

## Overview

Release engineering recently rotated the firmware **code-signing key**. Since the key rotation, every firmware release bundle submitted by the legacy publisher service is rejected by the distribution gateway with an `UNTRUSTED_SIGNATURE` error because release descriptors are still being signed using the now-revoked private key.

Your objective is to author the release publisher application at `/app/publisher/release-publisher.mjs`. The publisher must ingest the build manifest, reconcile withdrawn builds and duplicate entries using SQL in DuckDB, generate OpenSSL CMS detached signatures using the current active keypair, submit signed release descriptors to the Express distribution gateway over HTTP, persist receipts locally for idempotency, and print deterministic status lines.

---

## Workspace Layout & Key Paths

All project files are located under `/app` inside the execution container:

| Absolute Path | Description |
| --- | --- |
| `/app/publisher/release-publisher.mjs` | **Primary Deliverable.** Script executed when running `npm run report`. |
| `/app/fixtures/build_manifest.csv` | Raw input CSV manifest containing build and withdrawal records. |
| `/app/reports/publications.expected.txt` | Golden reference output that `npm run report` must reproduce. |
| `/app/keys/current/` | Directory containing active certificate (`current.cert.pem`) and private key (`current.key.pem`). |
| `/app/keys/revoked/` | Directory containing revoked keypair (`revoked.cert.pem`, `revoked.key.pem`). Do **not** sign with these keys. |
| `/app/distribution-gateway/` | Provided Express distribution gateway service listening on `http://127.0.0.1:7070`. |
| `/app/releases.duckdb` | Database created and maintained by the publisher at runtime. |
| `/app/package.json` | Project configuration defining `npm run report` (`node publisher/release-publisher.mjs --report`). |

---

## Detailed Requirements & Specification

### 1. Data Ingestion & SQL Reconciliation (DuckDB)

The raw manifest at `/app/fixtures/build_manifest.csv` contains fields:
`entry_id, bundle_id, component_id, version, size_bytes, record_type, supersedes_id, recorded_at`

Your publisher must create or connect to `/app/releases.duckdb` and perform data reconciliation:
1. **Collapse Duplicate Rows:** Manifest rows that are exact duplicates across every column must be treated as a single record.
2. **Apply Withdrawal Cancellations:** Records with `record_type = 'WITHDRAWAL'` reference a previous `BUILD` record via `supersedes_id`. Any `BUILD` record whose `entry_id` matches a `WITHDRAWAL` record's `supersedes_id` is cancelled and excluded from release calculations.
3. **Filter Publishable Bundles:** A bundle (`bundle_id`) is considered **publishable** if it has at least one surviving (non-withdrawn) build artifact after deduplication and withdrawal processing. Bundles whose builds have all been withdrawn must be skipped.
4. **Aggregate Bundle Statistics:** For each publishable bundle, derive:
   - `artifact_count`: Count of surviving build records for the bundle.
   - `total_bytes`: Sum of `size_bytes` across surviving build records for the bundle.
5. **Sort Order:** Bundles must be processed and output in ascending lexicographical order by `bundle_id`.

### 2. Fetch Active Signing Key Metadata

Query the Express distribution gateway endpoint:
`GET http://127.0.0.1:7070/v1/signing-key/current`

Response schema:
```json
{
  "key_id": "fw-signing-2026-current",
  "algorithm": "sha256WithRSAEncryption",
  "certificate_ref": "/app/keys/current/current.cert.pem",
  "status": "current"
}
```
Extract `key_id` for reporting and use the referenced active certificate (`/app/keys/current/current.cert.pem`) alongside its matching private key (`/app/keys/current/current.key.pem`) for cryptographic signing.

### 3. Canonical Descriptor Construction & OpenSSL CMS Signing

For each publishable bundle, build a canonical release descriptor JSON string:
- Keys must be sorted lexicographically: `artifact_count`, `bundle_id`, `total_bytes`.
- Formatted with UTF-8 encoding and no insignificant whitespace.
- Example: `{"artifact_count":4,"bundle_id":"BND-101","total_bytes":435050}`

Generate a detached OpenSSL CMS PEM signature over the exact canonical descriptor bytes using:
```bash
openssl cms -sign -in <descriptor_temp_file> \
  -signer /app/keys/current/current.cert.pem \
  -inkey /app/keys/current/current.key.pem \
  -outform PEM -binary
```

### 4. HTTP Gateway Submission & Idempotency Persistence

Submit each signed descriptor to the distribution gateway:
`POST http://127.0.0.1:7070/v1/publications`

Request JSON payload:
```json
{
  "descriptor": "{\"artifact_count\":4,\"bundle_id\":\"BND-101\",\"total_bytes\":435050}",
  "signature": "-----BEGIN CMS-----\n...",
  "request_token": "token-BND-101"
}
```

- `request_token` must follow the deterministic format `token-<bundle_id>`.
- Success Response (`200 OK`):
  ```json
  {
    "publication_id": "pub-BND-101",
    "request_token": "token-BND-101",
    "status": "PUBLISHED"
  }
  ```
- Save each publication receipt (`bundle_id`, `request_token`, `publication_id`, `status`) in `/app/releases.duckdb` in table `publications`.
- **Idempotency Rule:** Before submitting a bundle to the gateway, check `/app/releases.duckdb` for an existing publication record for `bundle_id`. If a record exists, reuse the persisted receipt and token rather than making a duplicate POST request to the gateway.

### 5. Deterministic Output Format

`npm run report` must print exactly two lines to stdout for each publishable bundle, ordered by `bundle_id` ascending:

```text
BUNDLE <bundle_id> SIGNED KEY=<key_id>
BUNDLE <bundle_id> PUBLISHED RECEIPT=<publication_id> TOKEN=<request_token> STATUS=PUBLISHED
```

Example matching golden output:
```text
BUNDLE BND-101 SIGNED KEY=fw-signing-2026-current
BUNDLE BND-101 PUBLISHED RECEIPT=pub-BND-101 TOKEN=token-BND-101 STATUS=PUBLISHED
BUNDLE BND-102 SIGNED KEY=fw-signing-2026-current
BUNDLE BND-102 PUBLISHED RECEIPT=pub-BND-102 TOKEN=token-BND-102 STATUS=PUBLISHED
BUNDLE BND-103 SIGNED KEY=fw-signing-2026-current
BUNDLE BND-103 PUBLISHED RECEIPT=pub-BND-103 TOKEN=token-BND-103 STATUS=PUBLISHED
```

---

## Constraints & Rules

1. Interact with the distribution gateway **only via HTTP** (`http://127.0.0.1:7070`). Do not read or modify `/app/distribution-gateway/data/gateway.json` directly.
2. Do **not** sign descriptors using keys in `/app/keys/revoked/`.
3. Do **not** hardcode publication IDs, bundle lists, or output lines. All values must be dynamically computed.
4. Ensure `npm run report` executes successfully via `node publisher/release-publisher.mjs --report`.

---

## Verification & Completion Criteria

Your solution is complete when:
1. Running `npm run report` outputs the exact expected lines matching `/app/reports/publications.expected.txt` (with `RECEIPT` values matching returned IDs).
2. Re-running `npm run report` produces byte-identical output without re-submitting duplicate publications to the gateway.
3. Database file `/app/releases.duckdb` contains the persisted publication records.
4. Tests run via `bash tests/test.sh` execute pytest and write `1` to `/logs/verifier/reward.txt`.
