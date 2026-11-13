"""Build a deterministic public evidence archive from an exact Git commit."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = '06b3889afa906f1ac3af8eca6130f0e6fce6cec9'
FILES = (
    'docs/BLAGO_UEL_0_3.md',
    'docs/uel03/THREAT_MODEL.md',
    'docs/uel03/RUNBOOK.md',
    'docs/uel03/TEST_MATRIX.md',
    'docs/uel03/evidence.json',
    'docs/uel03/checkpoint-vectors.json',
)
ARCHIVE = 'blago-research-uel03.zip'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(destination):
    destination = Path(destination)
    destination.mkdir(parents=False, exist_ok=False)
    package = {
        'README.md': (Path(__file__).parent / 'README.md').read_bytes(),
        'PR_SNAPSHOT.json': (Path(__file__).parent / 'PR_SNAPSHOT.json').read_bytes(),
    }
    for name in FILES:
        package[name] = subprocess.check_output(['git', 'show', SOURCE + ':' + name], cwd=ROOT)
    metadata = json.loads(package['PR_SNAPSHOT.json'])
    evidence = json.loads(package['docs/uel03/evidence.json'])
    if metadata['source_sha'] != SOURCE or evidence['ci']['status'] != 'PASS':
        raise ValueError('Source/evidence mismatch')
    for name, data in package.items():
        if re.search(rb'hf_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----', data):
            raise ValueError('Review required for credential-like content in ' + name)
    manifest = {
        'format': 'blago-public-research-snapshot-1',
        'source_commit': SOURCE,
        'runtime_source_commit': evidence['runtime_source_sha'],
        'observed_utc': metadata['observed_utc'],
        'main_observed_sha': metadata['main_observed_sha'],
        'source_pr': 'https://github.com/xBLAGO/space/pull/32',
        'verified_ci': 'https://github.com/xBLAGO/space/actions/runs/43483535901',
        'status': 'RESEARCH_PRERELEASE',
        'readiness': {'isolated_synthetic': 'GO', 'real_users': 'NO-GO', 'independent_operator': 'NOT EXECUTED'},
        'scope': 'Explicit public documents, synthetic evidence/vectors and public PR metadata',
        'full_source': 'https://github.com/xBLAGO/space/tree/' + SOURCE,
        'files_sha256': {name: sha(data) for name, data in sorted(package.items())},
    }
    package['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
    package['SHA256SUMS'] = ''.join(sha(data) + '  ' + name + '\n' for name, data in sorted(package.items())).encode()
    for name, data in package.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    with zipfile.ZipFile(destination / ARCHIVE, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(package.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 13, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = sha((destination / ARCHIVE).read_bytes())
    (destination / (ARCHIVE + '.sha256')).write_text(digest + '  ' + ARCHIVE + '\n')
    print(json.dumps({'source': SOURCE, 'files': len(package), 'archive_sha256': digest}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)


if __name__ == '__main__':
    main()
