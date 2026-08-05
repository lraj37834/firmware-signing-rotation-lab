import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';
import duckdb from 'duckdb';

function resolvePath(relPath) {
  const cleanPath = relPath.replace(/\\/g, '/').replace(/^\/+/, '');
  const candidates = [
    path.join(process.cwd(), cleanPath),
    path.join('/app', cleanPath),
    path.join(process.cwd(), 'environment', cleanPath),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return candidates[0];
}

function getOpenSSLBin() {
  if (process.platform === 'win32' && fs.existsSync('C:/Program Files/Git/usr/bin/openssl.exe')) {
    return '"C:/Program Files/Git/usr/bin/openssl.exe"';
  }
  return 'openssl';
}

const manifestPath = resolvePath('fixtures/build_manifest.csv').replace(/\\/g, '/');
const dbPath = path.join(process.cwd(), 'releases.duckdb');
const certPath = resolvePath('keys/current/current.cert.pem').replace(/\\/g, '/');
const keyPath = resolvePath('keys/current/current.key.pem').replace(/\\/g, '/');
const gatewayUrl = process.env.GATEWAY_URL || process.env.GATEWAY_BASE_URL || 'http://127.0.0.1:7070';

const db = new duckdb.Database(dbPath);
const con = db.connect();

con.run(`
  CREATE TABLE IF NOT EXISTS publications (
    bundle_id VARCHAR PRIMARY KEY,
    publication_id VARCHAR,
    request_token VARCHAR,
    key_id VARCHAR,
    status VARCHAR
  )
`);

const query = `
  WITH raw AS (
    SELECT DISTINCT * FROM read_csv_auto('${manifestPath}')
  ),
  withdrawn AS (
    SELECT supersedes_id FROM raw WHERE record_type = 'WITHDRAWAL' AND supersedes_id IS NOT NULL AND supersedes_id != ''
  ),
  valid AS (
    SELECT * FROM raw 
    WHERE record_type = 'BUILD' 
    AND entry_id NOT IN (SELECT supersedes_id FROM withdrawn)
  )
  SELECT 
    bundle_id, 
    CAST(COUNT(entry_id) AS INTEGER) as artifact_count, 
    CAST(SUM(size_bytes) AS BIGINT) as total_bytes
  FROM valid
  GROUP BY bundle_id
  HAVING COUNT(entry_id) > 0
  ORDER BY bundle_id ASC;
`;

con.all(query, async (err, bundles) => {
  if (err) {
    console.error("Failed to query manifest:", err);
    process.exit(1);
  }

  try {
    const keyRes = await fetch(`${gatewayUrl.replace(/\/+$/, '')}/v1/signing-key/current`);
    if (!keyRes.ok) {
      throw new Error(`Failed to fetch signing key metadata: ${keyRes.statusText}`);
    }
    const keyInfo = await keyRes.json();
    const keyId = keyInfo.key_id;

    for (const bundle of bundles) {
      const bundleId = bundle.bundle_id;
      const token = `token-${bundleId}`;

      const existing = await new Promise((resolve, reject) => {
        con.all(`SELECT publication_id, request_token, status FROM publications WHERE bundle_id = '${bundleId}'`, (err, res) => {
          if (err) reject(err);
          else resolve(res && res.length > 0 ? res[0] : null);
        });
      });

      console.log(`BUNDLE ${bundleId} SIGNED KEY=${keyId}`);

      if (existing) {
        console.log(`BUNDLE ${bundleId} PUBLISHED RECEIPT=${existing.publication_id} TOKEN=${existing.request_token} STATUS=${existing.status}`);
        continue;
      }

      const descriptor = JSON.stringify({
        artifact_count: Number(bundle.artifact_count),
        bundle_id: bundleId,
        total_bytes: Number(bundle.total_bytes)
      });

      const tmpDesc = path.join(process.cwd(), `.desc_${bundleId}.bin`);
      const tmpSig = path.join(process.cwd(), `.sig_${bundleId}.pem`);

      fs.writeFileSync(tmpDesc, descriptor, 'utf8');

      try {
        const opensslCmd = `${getOpenSSLBin()} cms -sign -in "${tmpDesc}" -signer "${certPath}" -inkey "${keyPath}" -outform PEM -binary`;
        const signature = execSync(opensslCmd, { encoding: 'utf8' });

        const publishRes = await fetch(`${gatewayUrl.replace(/\/+$/, '')}/v1/publications`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            descriptor,
            signature,
            request_token: token
          })
        });

        const publishData = await publishRes.json();
        const receiptId = publishData.publication_id;
        const status = publishData.status || 'PUBLISHED';

        await new Promise((resolve, reject) => {
          con.run(
            `INSERT OR REPLACE INTO publications (bundle_id, publication_id, request_token, key_id, status) VALUES (?, ?, ?, ?, ?)`,
            [bundleId, receiptId, token, keyId, status],
            (err) => {
              if (err) reject(err);
              else resolve();
            }
          );
        });

        console.log(`BUNDLE ${bundleId} PUBLISHED RECEIPT=${receiptId} TOKEN=${token} STATUS=${status}`);
      } finally {
        if (fs.existsSync(tmpDesc)) fs.unlinkSync(tmpDesc);
        if (fs.existsSync(tmpSig)) fs.unlinkSync(tmpSig);
      }
    }
  } catch (e) {
    console.error("Error during publishing:", e);
    process.exit(1);
  } finally {
    con.close();
  }
});
