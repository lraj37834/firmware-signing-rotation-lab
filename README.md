# Firmware Signing Rotation Lab

A background **Firmware Release Publisher** application built in **Node.js** with **DuckDB** and **OpenSSL CMS**, designed to reconcile firmware build manifests, sign release bundles with active cryptographic certificates, integrate with an Express distribution gateway over HTTP, and maintain idempotent publication receipts locally.

---

## 🏗️ Architecture & System Overview

```
                                  ┌──────────────────────────┐
                                  │ fixtures/build_manifest  │
                                  └────────────┬─────────────┘
                                               │
                                               ▼
┌─────────────────────────┐        ┌─────────────────────────┐
│ Express Gateway (:7070) │        │ DuckDB SQL Engine       │
│ GET /v1/signing-key/curr│        │ - Collapse Duplicates   │
│ POST /v1/publications   │        │ - Apply Withdrawals     │
└────────────▲────────────┘        └───────────┬─────────────┘
             │                                 │ (Surviving Bundles)
             │ HTTP POST                       ▼
┌────────────┴───────────────────────────────────────────────┐
│ Node.js Publisher (publisher/release-publisher.mjs)        │
│ 1. Fetch current key info (/v1/signing-key/current)        │
│ 2. Build canonical descriptor JSON                         │
│ 3. Sign via OpenSSL CMS (keys/current/current.key.pem)    │
│ 4. Submit to Gateway & persist receipts in releases.duckdb │
│ 5. Output formatted status lines                           │
└────────────────────────────────────────────────────────────┘
```

---

## ✨ Features

- **SQL Data Reconciliation**: Ingests raw build manifest CSV data into DuckDB, eliminates exact duplicate records, and applies `WITHDRAWAL` cancellations to derive publishable release bundles.
- **Cryptographic CMS Detached Signatures**: Generates UTF-8 canonical JSON descriptors and signs them using detached OpenSSL CMS PEM signatures (`openssl cms -sign`).
- **HTTP Gateway Integration**: Queries key metadata from `GET /v1/signing-key/current` and submits signed bundles to `POST /v1/publications`.
- **Local Persistence & Idempotency**: Stores publication tokens and receipt IDs in `releases.duckdb`, ensuring repeat runs replay existing receipts without creating duplicate server records.
- **Deterministic Reporting**: Emits formatted console status lines matching the golden reference snapshot.

---

## 🔒 Security Note: Key Management & Git Tracking

> [!IMPORTANT]
> **Generated Keypairs Are Excluded From Version Control**:
> The `keys/` directory and all `.pem` / `.key` files are generated dynamically at image build time (or local setup time) and are **ignored by `.gitignore`**. 
> 
> **Never commit private key files (`*.key.pem`) to GitHub or public version control.**

---

## 🚀 Getting Started & Execution

### Prerequisites
- Node.js 18+ (with `duckdb` package)
- OpenSSL CLI (`openssl`)

### Running the Application

1. **Start the Distribution Gateway**:
   ```bash
   node environment/distribution-gateway/server.js
   ```

2. **Publish Solution**:
   ```bash
   bash solution/publish.sh
   ```

3. **Run via NPM Script**:
   ```bash
   npm run report
   ```

---

## 📁 Repository Layout

- `solution/release-publisher.mjs`: Pure Node.js application solution.
- `publisher/release-publisher.mjs`: Delivered Node ESM entry point.
- `environment/distribution-gateway/`: Express distribution service and key metadata endpoints.
- `fixtures/build_manifest.csv`: Input build manifest dataset.
- `reports/publications.expected.txt`: Golden expected output reference file.
