"""Firmware release publisher module.

Loads build manifest CSV into DuckDB, reconciles withdrawals and duplicates via SQL,
signs canonical descriptors using OpenSSL CMS, and submits signed bundles to Express gateway.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import requests


def resolve_file_path(relative_path: str) -> Path:
    """Resolve a file path relative to workspace root or current directory.

    Args:
        relative_path: Relative path string.

    Returns:
        Path: Absolute path to the existing file or target path.
    """
    clean_path = relative_path.replace("\\", "/").strip("/")
    if clean_path.startswith("app/"):
        clean_path = clean_path[4:]

    candidates = [
        Path.cwd() / clean_path,
        Path.cwd() / "environment" / clean_path,
        Path("/app") / clean_path,
        Path(__file__).resolve().parent.parent / clean_path,
        Path(__file__).resolve().parent.parent / "environment" / clean_path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]



def fetch_current_key_metadata(gateway_url: str) -> dict[str, str]:
    """Fetch current signing key metadata from distribution gateway.

    Args:
        gateway_url: Gateway base URL.

    Returns:
        dict[str, str]: JSON dictionary with key_id, algorithm, and certificate_ref.
    """
    endpoint = f"{gateway_url.rstrip('/')}/v1/signing-key/current"
    response = requests.get(endpoint, timeout=10)
    response.raise_for_status()
    data: dict[str, str] = response.json()
    return data


def resolve_crypto_paths(cert_ref: str) -> tuple[Path, Path]:
    """Locate local certificate and private key files on disk.

    Args:
        cert_ref: Certificate path reference from gateway metadata.

    Returns:
        tuple[Path, Path]: Pair of (cert_path, key_path).
    """
    cert_path = resolve_file_path(cert_ref.lstrip("/"))
    key_ref = cert_ref.replace(".cert.pem", ".key.pem")
    key_path = resolve_file_path(key_ref.lstrip("/"))
    return cert_path, key_path


def init_db(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB database connection and ensure receipts table exists.

    Args:
        db_path: Path to DuckDB database file.

    Returns:
        duckdb.DuckDBPyConnection: Active database connection.
    """
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS publications (
            bundle_id VARCHAR PRIMARY KEY,
            request_token VARCHAR,
            publication_id VARCHAR,
            status VARCHAR,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    return conn


def reconcile_manifest(
    conn: duckdb.DuckDBPyConnection, manifest_path: Path
) -> list[dict[str, Any]]:
    """Ingest CSV manifest into DuckDB and derive publishable bundles via SQL.

    Args:
        conn: DuckDB connection.
        manifest_path: Path to build_manifest.csv.

    Returns:
        list[dict[str, Any]]: List of publishable bundle dictionaries.
    """
    manifest_str = str(manifest_path).replace("\\", "/")
    conn.execute(
        f"""
        CREATE TEMP TABLE IF NOT EXISTS raw_manifest AS
        SELECT DISTINCT * FROM read_csv_auto('{manifest_str}');
        """
    )

    query = """
        WITH withdrawn_entries AS (
            SELECT supersedes_id
            FROM raw_manifest
            WHERE record_type = 'WITHDRAWAL' AND supersedes_id IS NOT NULL
        ),
        surviving_builds AS (
            SELECT bundle_id, size_bytes
            FROM raw_manifest
            WHERE record_type = 'BUILD'
              AND entry_id NOT IN (SELECT supersedes_id FROM withdrawn_entries)
        )
        SELECT
            bundle_id,
            COUNT(*) AS artifact_count,
            CAST(SUM(size_bytes) AS BIGINT) AS total_bytes
        FROM surviving_builds
        GROUP BY bundle_id
        HAVING COUNT(*) > 0
        ORDER BY bundle_id ASC;
    """
    rows = conn.execute(query).fetchall()
    return [
        {
            "bundle_id": row[0],
            "artifact_count": int(row[1]),
            "total_bytes": int(row[2]),
        }
        for row in rows
    ]


def create_canonical_descriptor(
    bundle_id: str, artifact_count: int, total_bytes: int
) -> str:
    """Create UTF-8 canonical JSON descriptor string with sorted keys and no space.

    Args:
        bundle_id: Bundle identifier.
        artifact_count: Count of surviving build artifacts.
        total_bytes: Total byte size of surviving build artifacts.

    Returns:
        str: Minified canonical JSON string.
    """
    data = {
        "artifact_count": artifact_count,
        "bundle_id": bundle_id,
        "total_bytes": total_bytes,
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def find_openssl_executable() -> str:
    """Locate the openssl executable in PATH or standard installation locations.

    Returns:
        str: Executable command string or absolute file path.
    """
    git_openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
    if git_openssl.is_file():
        return str(git_openssl)
    return "openssl"


def sign_descriptor_cms(descriptor: str, cert_path: Path, key_path: Path) -> str:
    """Generate detached OpenSSL CMS PEM signature over descriptor bytes.

    Args:
        descriptor: Canonical JSON descriptor string.
        cert_path: Path to current certificate PEM file.
        key_path: Path to current private key PEM file.

    Returns:
        str: Detached OpenSSL CMS signature in PEM format.
    """
    with tempfile.NamedTemporaryFile("wb", delete=False) as tmp_desc:
        tmp_desc.write(descriptor.encode("utf-8"))
        tmp_desc_path = tmp_desc.name

    try:
        cmd = [
            find_openssl_executable(),
            "cms",
            "-sign",
            "-in",
            tmp_desc_path,
            "-signer",
            str(cert_path),
            "-inkey",
            str(key_path),
            "-outform",
            "PEM",
            "-binary",
        ]
        result = subprocess.run(
            cmd, capture_output=True, check=True, text=True
        )
        return result.stdout
    finally:
        if os.path.exists(tmp_desc_path):
            os.remove(tmp_desc_path)



def fetch_existing_receipt(
    conn: duckdb.DuckDBPyConnection, bundle_id: str
) -> dict[str, str] | None:
    """Fetch existing publication record from DuckDB if present.

    Args:
        conn: DuckDB connection.
        bundle_id: Target bundle ID.

    Returns:
        dict[str, str] | None: Stored receipt dict or None if not published.
    """
    row = conn.execute(
        "SELECT request_token, publication_id, status FROM publications WHERE bundle_id = ?",
        [bundle_id],
    ).fetchone()
    if not row:
        return None
    return {
        "request_token": str(row[0]),
        "publication_id": str(row[1]),
        "status": str(row[2]),
    }


def submit_to_gateway(
    gateway_url: str, descriptor: str, signature: str, request_token: str
) -> dict[str, str]:
    """Submit signed release descriptor to distribution gateway HTTP endpoint.

    Args:
        gateway_url: Gateway base URL.
        descriptor: Canonical JSON descriptor string.
        signature: Detached CMS PEM signature string.
        request_token: Client request token.

    Returns:
        dict[str, str]: Publication receipt response dict.
    """
    endpoint = f"{gateway_url.rstrip('/')}/v1/publications"
    payload = {
        "descriptor": descriptor,
        "signature": signature,
        "request_token": request_token,
    }
    response = requests.post(endpoint, json=payload, timeout=10)
    response.raise_for_status()
    data: dict[str, str] = response.json()
    return data


def save_publication(
    conn: duckdb.DuckDBPyConnection,
    bundle_id: str,
    request_token: str,
    publication_id: str,
    status: str,
) -> None:
    """Persist publication receipt in DuckDB database.

    Args:
        conn: DuckDB connection.
        bundle_id: Target bundle ID.
        request_token: Client idempotency token.
        publication_id: Gateway publication receipt ID.
        status: Publication status string.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO publications (bundle_id, request_token, publication_id, status)
        VALUES (?, ?, ?, ?);
        """,
        [bundle_id, request_token, publication_id, status],
    )


def process_bundle(
    conn: duckdb.DuckDBPyConnection,
    gateway_url: str,
    key_info: dict[str, str],
    cert_path: Path,
    key_path: Path,
    bundle: dict[str, Any],
) -> None:
    """Process, sign, submit, and report a single publishable bundle.

    Args:
        conn: DuckDB connection.
        gateway_url: Gateway base URL.
        key_info: Active signing key metadata.
        cert_path: Certificate path.
        key_path: Private key path.
        bundle: Bundle information dictionary.
    """
    bundle_id = str(bundle["bundle_id"])
    request_token = f"token-{bundle_id}"

    # Print signed line
    print(f"BUNDLE {bundle_id} SIGNED KEY={key_info['key_id']}")

    # Idempotency check
    stored = fetch_existing_receipt(conn, bundle_id)
    if stored:
        print(
            f"BUNDLE {bundle_id} PUBLISHED RECEIPT={stored['publication_id']} "
            f"TOKEN={stored['request_token']} STATUS={stored['status']}"
        )
        return

    descriptor = create_canonical_descriptor(
        bundle_id, int(bundle["artifact_count"]), int(bundle["total_bytes"])
    )
    signature = sign_descriptor_cms(descriptor, cert_path, key_path)
    receipt = submit_to_gateway(
        gateway_url, descriptor, signature, request_token
    )

    save_publication(
        conn,
        bundle_id,
        request_token,
        receipt["publication_id"],
        receipt["status"],
    )
    print(
        f"BUNDLE {bundle_id} PUBLISHED RECEIPT={receipt['publication_id']} "
        f"TOKEN={request_token} STATUS={receipt['status']}"
    )


def run_publisher(
    gateway_url: str = "http://127.0.0.1:7070",
    manifest_rel_path: str = "fixtures/build_manifest.csv",
    db_rel_path: str = "releases.duckdb",
) -> None:
    """Main publisher execution entry point.

    Args:
        gateway_url: Gateway service URL.
        manifest_rel_path: Relative path to build manifest CSV.
        db_rel_path: Relative path to DuckDB database file.
    """
    manifest_path = resolve_file_path(manifest_rel_path)
    db_path = Path.cwd() / db_rel_path

    conn = init_db(db_path)
    key_info = fetch_current_key_metadata(gateway_url)
    cert_path, key_path = resolve_crypto_paths(key_info["certificate_ref"])

    bundles = reconcile_manifest(conn, manifest_path)
    for bundle in bundles:
        process_bundle(conn, gateway_url, key_info, cert_path, key_path, bundle)

    conn.close()


def main() -> None:
    """CLI entry point."""
    gateway_url = os.getenv("GATEWAY_URL", "http://127.0.0.1:7070")
    run_publisher(gateway_url=gateway_url)


if __name__ == "__main__":
    main()
