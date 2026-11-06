#!/usr/bin/env python3
"""Check build shell syntax, firmware selection, and remote package sources."""
import concurrent.futures
import gzip
from pathlib import Path
import re
import subprocess
import urllib.request

root = Path(__file__).resolve().parent
failures = []

def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}", flush=True)
    if not ok:
        failures.append(name)

for path in sorted(root.glob('*.sh')):
    result = subprocess.run(['bash', '-n', str(path)], capture_output=True, text=True)
    check(path.name, result.returncode == 0, result.stderr.strip())

for path in [root / 'Dockerfile', *root.glob('*.Dockerfile')]:
    text = path.read_text().replace('\\\n', ' ')
    for index, match in enumerate(re.finditer(r'^RUN (.*)$', text, re.M), 1):
        command = re.sub(r'^\s*(?:--mount=\S+\s+)*', '', match.group(1))
        result = subprocess.run(['bash', '-n'], input=command, capture_output=True, text=True)
        check(f'{path.name} RUN {index}', result.returncode == 0, result.stderr.strip())
    if path.name in ('Dockerfile', 'firmware.Dockerfile'):
        guard = re.search(r'case "\$firmware_debian_version" in .*?esac', text).group()
        for version, expected in [('11.11', 0), ('12.0', 1), ('13.0', 1)]:
            result = subprocess.run(['bash', '-c', guard],
                                    env={'firmware_debian_version': version}, capture_output=True)
            check(f'{path.name} firmware guard {version}', result.returncode == expected)

workflow = (root / '.github/workflows/build.yml').read_text()
check('workflow firmware selection', 'FW_URL=' not in workflow,
      'workflow must use the compatible firmware.txt default')

urls = [
    (root / 'firmware.txt').read_text().strip(),
    'https://archive.debian.org/debian/dists/bullseye/Release',
    'https://archive.debian.org/debian-security/dists/bullseye-security/Release',
    'https://apt-archive.postgresql.org/pub/repos/apt/dists/bullseye-pgdg/Release',
    'https://apt-archive.postgresql.org/pub/repos/apt/dists/bullseye-pgdg/main/binary-arm64/Packages.gz',
    'https://nginx.org/packages/debian/dists/bullseye/Release',
    'https://apt.artifacts.ui.com/dists/bullseye/Release',
    'https://www.postgresql.org/media/keys/ACCC4CF8.asc',
]
for path in [root / 'protect.Dockerfile']:
    urls += re.findall(r'ARG \w+_STABLE_URL="([^"]+)"', path.read_text())

def probe(url):
    try:
        request = urllib.request.Request(url, headers={'Range': 'bytes=0-0', 'User-Agent': 'Wget/1.25.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            return url, response.status in (200, 206), str(response.status)
    except Exception as exc:
        return url, False, str(exc)

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    for url, ok, detail in pool.map(probe, urls):
        check(url, ok, detail)

try:
    url = 'https://apt-archive.postgresql.org/pub/repos/apt/dists/bullseye-pgdg/main/binary-arm64/Packages.gz'
    with urllib.request.urlopen(url, timeout=30) as response:
        packages = gzip.decompress(response.read()).decode()
    found = any(block.startswith('Package: postgresql-14\n')
                and '\nArchitecture: arm64\n' in block
                for block in packages.split('\n\n'))
    check('PostgreSQL 14 Bullseye ARM64 package metadata', found)
except Exception as exc:
    check('PostgreSQL package metadata', False, str(exc))

raise SystemExit(bool(failures))
