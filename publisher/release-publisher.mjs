import { execFileSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);


try {
  const pythonScript = path.join(__dirname, 'release_publisher.py');
  const result = execFileSync('python', [pythonScript, ...process.argv.slice(2)], {
    encoding: 'utf8',
    stdio: 'inherit',
  });
} catch (err) {
  process.exit(err.status || 1);
}
