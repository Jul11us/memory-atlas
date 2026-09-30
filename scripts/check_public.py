"""Check the explicit public file list; never print matched credential contents.

This is a release guard, not a guarantee that arbitrary prose contains no private data.
Review new public files and demo screenshots before extending PUBLIC_FILES.
"""

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = {
    '.gitignore', '.github/workflows/check.yml', 'README.md', 'docs/integration.md',
    'assets/memory-atlas.svg', 'assets/demo-preview.jpg', 'memory_atlas.py',
    'assets/demo-recall.jpg', 'assets/demo-inspector.jpg', 'assets/demo-decay.jpg',
    'start_website.py', '一键启动.bat', 'static/app.js', 'static/index.html',
    'static/styles.css', 'static/recall.js', 'tests/test_memory_atlas.py',
    'tests/test_recall.cjs', 'tests/test_decay.py', 'examples/recall.py',
    'memory_atlas_mcp.py', 'tests/test_mcp.py',
    'examples/demo-memories/memory_summary.md', 'examples/demo-memories/MEMORY.md',
    'scripts/check_public.py',
}
PATTERNS = {
    'credential-like token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'personal Windows home': re.compile(r'(?i)[A-Z]:[\\/]+Users[\\/]+(?!Example[\\/])[^\s"<>\\/]+'),
    'personal Unix home': re.compile(r'/(?:Users|home)/(?!Example/)[^\s"<>/]+/'),
    'email address': re.compile(r'\b[A-Za-z0-9_.+-]+@(?!(?:users\.noreply\.github\.com|example\.(?:com|org))\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
}


def main() -> None:
    output = subprocess.check_output(['git', '-c', 'core.quotepath=false', 'ls-files',
                                      '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT)
    files = set(output.decode('utf-8').split('\0')) - {''}
    findings = []
    for filename in sorted(files):
        if filename not in PUBLIC_FILES:
            findings.append(f'{filename}: not in the reviewed public file list')
            continue
        path = ROOT / filename
        if not path.is_file():
            continue
        if path.suffix == '.jpg':
            # Only the reviewed fictional demo screenshots are permitted; inspect pixels separately.
            if path.read_bytes()[:3] != b'\xff\xd8\xff':
                findings.append(f'{filename}: invalid JPEG')
            continue
        text = path.read_text(encoding='utf-8-sig')
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f'{filename}: {label}')
    if findings:
        raise SystemExit('Public-file check failed:\n' + '\n'.join(findings))
    print(f'Public-file check passed: {len(files)} reviewed paths; no runtime data or matching secrets.')


if __name__ == '__main__':
    main()
