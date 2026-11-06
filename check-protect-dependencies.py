#!/usr/bin/env python3
"""Resolve the edge assets dependency chain using an isolated ARM64 APT state.

Downloads metadata only; never installs packages or changes the host's APT state.
The temporary local repository is trusted solely for this install simulation.
"""
import gzip
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

def fetch(url, limit=None):
    headers = {'User-Agent': 'Wget/1.25.0'}
    if limit:
        headers['Range'] = f'bytes=0-{limit-1}'
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(limit) if limit else response.read()

def control(product):
    url = ('https://fw-update.ubnt.com/api/firmware-latest?filter=eq~~product~~'
           + product + '&filter=eq~~channel~~release&filter=eq~~platform~~uos-deb11-arm64')
    entry = json.loads(fetch(url))['_embedded']['firmware'][0]
    deb = fetch(entry['_links']['data']['href'], 262144)
    assert deb[:8] == b'!<arch>\n', product
    offset = 8
    while offset + 60 <= len(deb):
        header = deb[offset:offset+60]
        size = int(header[48:58])
        name = header[:16].decode().strip().rstrip('/')
        offset += 60
        if name.startswith('control.tar'):
            if offset + size > len(deb):
                assert offset + size < 16 * 1024 * 1024, 'Unexpectedly large control archive'
                deb = fetch(entry['_links']['data']['href'], offset + size)
            with tarfile.open(fileobj=io.BytesIO(deb[offset:offset+size])) as archive:
                text = archive.extractfile('./control').read().decode().strip()
            print(text, flush=True)
            return text + '\nFilename: metadata-only.deb\nSize: 1\n\n'
        offset += size + size % 2
    raise RuntimeError(f'Control archive exceeds metadata range: {product}')

metadata = {name: control(name) for name in
            ('unifi-protect', 'unifi-user-assets', 'unifi-face-shared-lib')}
indexes = [
    'https://archive.debian.org/debian/dists/bullseye/main/binary-arm64/Packages.gz',
    'https://archive.debian.org/debian-security/dists/bullseye-security/main/binary-arm64/Packages.gz',
    'https://apt-archive.postgresql.org/pub/repos/apt/dists/bullseye-pgdg/main/binary-arm64/Packages.gz',
    'https://apt.artifacts.ui.com/dists/bullseye/release/binary-arm64/Packages.gz',
]
with tempfile.TemporaryDirectory(prefix='unvr-apt-check-') as directory:
    root = Path(directory)
    repo = root / 'repo'
    repo.mkdir()
    packages = metadata['unifi-user-assets'] + metadata['unifi-face-shared-lib']
    for url in indexes:
        print('Reading', url, flush=True)
        packages += gzip.decompress(fetch(url)).decode() + '\n\n'
    (repo / 'Packages').write_text(packages)
    (root / 'sources.list').write_text(f'deb [trusted=yes] file:{repo} ./\n')
    (root / 'status').touch()
    (root / 'lists/partial').mkdir(parents=True)
    options = ['-o', f'Dir={root}', '-o', f'Dir::Etc::sourcelist={root}/sources.list',
               '-o', 'Dir::Etc::sourceparts=-', '-o', f'Dir::State::status={root}/status',
               '-o', f'Dir::State::lists={root}/lists', '-o', 'APT::Architecture=arm64',
               '-o', 'Debug::NoLocking=1', '-o', 'APT::Sandbox::User=root']
    subprocess.run(['apt-get', *options, 'update'], check=True, stdout=subprocess.DEVNULL)
    result = subprocess.run(['apt-get', *options, '--simulate', '--no-install-recommends',
                             'install', 'unifi-user-assets', 'unifi-face-shared-lib',
                             'postgresql-14'], capture_output=True, text=True)
    print(result.stdout[-6000:], result.stderr, flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
    print('PASS: ARM64 assets, face library, PostgreSQL and transitive dependencies resolve on Bullseye.')
    print('Scope: metadata solver only; Protect/firmware installation and runtime are not tested.')
