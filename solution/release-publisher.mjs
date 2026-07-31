import { execFileSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const pythonScript = path.join(__dirname, 'release_publisher.py');

function runPublisher() {
  const pythonCmds = process.platform === 'win32' ? ['python', 'python3'] : ['python3', 'python'];
  let lastErr = null;

  for (const cmd of pythonCmds) {
    try {
      execFileSync(cmd, [pythonScript, ...process.argv.slice(2)], {
        encoding: 'utf8',
        stdio: 'inherit',
      });
      return;
    } catch (err) {
      lastErr = err;
      if (err.code !== 'ENOENT') {
        process.exit(err.status || 1);
      }
    }
  }

  if (lastErr) {
    process.exit(lastErr.status || 1);
  }
}

runPublisher();
