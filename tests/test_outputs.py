"""Verifier test suite for firmware release publisher.

Evaluates data reconciliation, current-key signing verification, DuckDB receipt persistence,
idempotent re-execution, and revoked-key signature rejection against Express gateway.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest
import requests


def resolve_path(rel_path: str) -> Path:
    """Resolve relative workspace or absolute container path.

    Args:
        rel_path: Relative path string.

    Returns:
        Path: Resolved absolute path.
    """
    clean_path = rel_path.replace("\\", "/").strip("/")
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


def is_gateway_alive(gateway_url: str) -> bool:
    """Check if distribution gateway endpoint is active.

    Args:
        gateway_url: Base gateway URL.

    Returns:
        bool: True if endpoint responds OK, False otherwise.
    """
    try:
        url = f"{gateway_url.rstrip('/')}/v1/signing-key/current"
        resp = requests.get(url, timeout=2)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def start_gateway_process(gateway_dir: Path) -> subprocess.Popen[str]:
    """Start distribution gateway Node server process in background.

    Args:
        gateway_dir: Directory containing server.js.

    Returns:
        subprocess.Popen[str]: Subprocess handle.
    """
    env = os.environ.copy()
    cert_path = resolve_path("keys/current/current.cert.pem")
    if cert_path.exists():
        env["CURRENT_CERT_PATH"] = str(cert_path)

    node_modules = gateway_dir / "node_modules"
    if not node_modules.exists():
        subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund"],
            cwd=str(gateway_dir),
            check=True,
            capture_output=True,
        )

    return subprocess.Popen(
        ["node", "server.js"],
        cwd=str(gateway_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture(scope="session", autouse=True)
def gateway_service() -> Any:
    """Session fixture ensuring Express distribution gateway is running."""
    gateway_url = os.getenv("GATEWAY_URL", "http://127.0.0.1:7070")
    if is_gateway_alive(gateway_url):
        yield gateway_url
        return

    gateway_dir = resolve_path("distribution-gateway")
    proc = start_gateway_process(gateway_dir)

    started = False
    for _ in range(30):
        if proc.poll() is not None:
            stderr_out = proc.stderr.read() if proc.stderr else ""
            pytest.fail(
                f"Gateway process exited prematurely (code={proc.returncode}).\n"
                f"stderr: {stderr_out}"
            )
        if is_gateway_alive(gateway_url):
            started = True
            break
        time.sleep(0.2)

    if not started and proc.poll() is None:
        # Process is still running but not responding — yield anyway and let
        # individual tests fail with ConnectionRefusedError.
        pass

    yield gateway_url

    proc.terminate()
    proc.wait(timeout=5)


def run_report_command() -> str:
    """Run `npm run report` and return stdout output.

    Returns:
        str: Captured stdout string.
    """
    publisher_mjs = resolve_path("publisher/release-publisher.mjs")
    if not publisher_mjs.exists():
        pytest.fail("Deliverable publisher/release-publisher.mjs is missing.")

    cmd = ["node", str(publisher_mjs), "--report"]
    res = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(Path.cwd()), check=False
    )
    if res.returncode != 0:
        pytest.fail(f"npm run report failed code={res.returncode}\n{res.stderr}")
    return res.stdout


def mask_receipt_ids(text: str) -> str:
    """Mask dynamic receipt IDs in CLI output string.

    Args:
        text: Raw CLI output text.

    Returns:
        str: Masked output text.
    """
    return re.sub(r"RECEIPT=[^\s]+", "RECEIPT=<id>", text.strip())


def test_report_output_matches() -> None:
    """Verify CLI status lines match golden expected output."""
    output = run_report_command()
    expected_path = resolve_path("reports/publications.expected.txt")
    expected_text = expected_path.read_text(encoding="utf-8")

    masked_actual = mask_receipt_ids(output)
    masked_expected = mask_receipt_ids(expected_text)

    assert masked_actual == masked_expected, (
        f"CLI output mismatch:\nExpected:\n{masked_expected}\nActual:\n{masked_actual}"
    )


def test_withdrawals_and_duplicates_reconciled() -> None:
    """Verify SQL reconciliation excludes withdrawn builds and duplicate rows."""
    db_path = Path.cwd() / "releases.duckdb"
    if not db_path.exists():
        pytest.fail("releases.duckdb was not created.")

    conn = duckdb.connect(str(db_path))
    rows = conn.execute("SELECT bundle_id FROM publications ORDER BY bundle_id ASC").fetchall()
    conn.close()

    bundle_ids = [row[0] for row in rows]
    assert bundle_ids == ["BND-101", "BND-102", "BND-103"], (
        f"Expected reconciled publishable bundles ['BND-101', 'BND-102', 'BND-103'], got {bundle_ids}"
    )


def test_bundles_signed_with_current_key_accepted() -> None:
    """Verify submitted descriptors are accepted by gateway with STATUS=PUBLISHED."""
    db_path = Path.cwd() / "releases.duckdb"
    conn = duckdb.connect(str(db_path))
    rows = conn.execute("SELECT status FROM publications").fetchall()
    conn.close()

    statuses = [row[0] for row in rows]
    assert len(statuses) == 3, f"Expected 3 published records, found {len(statuses)}"
    assert all(s == "PUBLISHED" for s in statuses), (
        f"All publication statuses must be PUBLISHED, got {statuses}"
    )


def test_receipts_and_tokens_persisted_in_duckdb() -> None:
    """Verify table publications stores valid request tokens and publication IDs."""
    db_path = Path.cwd() / "releases.duckdb"
    conn = duckdb.connect(str(db_path))
    rows = conn.execute(
        "SELECT bundle_id, request_token, publication_id, status FROM publications"
    ).fetchall()
    conn.close()

    assert len(rows) == 3
    for bundle_id, token, receipt_id, status in rows:
        assert token == f"token-{bundle_id}"
        assert receipt_id.startswith("pub")
        assert status == "PUBLISHED"


def test_idempotent_rerun_no_duplicate_publications() -> None:
    """Verify re-executing report produces identical output without duplicating records."""
    first_output = run_report_command()
    second_output = run_report_command()

    assert first_output == second_output, (
        "Re-running publisher output was not identical to initial execution output."
    )

    ledger_path = resolve_path("distribution-gateway/data/gateway.json")
    if ledger_path.exists():
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        pubs = data.get("publications", {})
        assert len(pubs) == 3, f"Expected 3 ledger publications, found {len(pubs)}"


def sign_descriptor_with_key(
    descriptor: str, cert_path: Path, key_path: Path
) -> str:
    """Generate detached OpenSSL CMS signature for test validation.

    Args:
        descriptor: Canonical JSON descriptor string.
        cert_path: Path to certificate file.
        key_path: Path to private key file.

    Returns:
        str: Detached PEM signature string.
    """
    with tempfile.NamedTemporaryFile("wb", delete=False) as tmp:
        tmp.write(descriptor.encode("utf-8"))
        tmp_name = tmp.name

    try:
        git_openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        exe = str(git_openssl) if git_openssl.is_file() else "openssl"
        cmd = [
            exe,
            "cms",
            "-sign",
            "-in",
            tmp_name,
            "-signer",
            str(cert_path),
            "-inkey",
            str(key_path),
            "-outform",
            "PEM",
            "-binary",
        ]
        res = subprocess.run(cmd, capture_output=True, check=True, text=True)
        return res.stdout
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def test_revoked_key_signature_rejected(gateway_service: str) -> None:
    """Verify descriptors signed with revoked key are rejected with UNTRUSTED_SIGNATURE."""
    revoked_cert = resolve_path("keys/revoked/revoked.cert.pem")
    revoked_key = resolve_path("keys/revoked/revoked.key.pem")

    descriptor = json.dumps(
        {"artifact_count": 1, "bundle_id": "BND-TEST-REVOKED", "total_bytes": 1000},
        separators=(",", ":"),
        sort_keys=True,
    )
    sig = sign_descriptor_with_key(descriptor, revoked_cert, revoked_key)

    endpoint = f"{gateway_service.rstrip('/')}/v1/publications"
    payload = {
        "descriptor": descriptor,
        "signature": sig,
        "request_token": "token-BND-TEST-REVOKED",
    }
    resp = requests.post(endpoint, json=payload, timeout=10)

    assert resp.status_code == 400, (
        f"Expected HTTP 400 rejection for revoked key, got status {resp.status_code}"
    )
    data = resp.json()
    assert data.get("error") == "UNTRUSTED_SIGNATURE", (
        f"Expected UNTRUSTED_SIGNATURE error, got {data}"
    )
