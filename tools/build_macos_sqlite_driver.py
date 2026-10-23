"""Optional isolated CPython 3.12.10 driver rebuild for statically linked macOS.

Output goes only to --prefix. Use PYTHONPATH=<prefix>:src to select the rebuilt
_sqlite3, never overwrite the system Python/framework. SQLite is pinned separately.
"""
import argparse
import hashlib
import platform
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
from pathlib import Path

PYTHON_SOURCE_SHA256='15d9c623abfd2165fe816ea1fb385d6ed8cf3c664661ab357f1782e3036a6dac'


def build(archive,sqlite_prefix,prefix):
    if platform.system()!='Darwin' or sys.version_info[:3]!=(3,12,10):
        raise RuntimeError('This isolated driver recipe is for CPython 3.12.10 on macOS only')
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=PYTHON_SOURCE_SHA256:
        raise ValueError('Official CPython source archive SHA256 mismatch')
    prefix.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blago-python-sqlite-') as root:
        root=Path(root)
        with tarfile.open(archive) as source:
            for member in source.getmembers():
                if member.name.startswith('Python-3.12.10/Modules/_sqlite/') and member.isfile() and member.name.endswith(('.c','.h')):
                    relative=Path(member.name).relative_to('Python-3.12.10/Modules/_sqlite')
                    if '..' in relative.parts:
                        raise ValueError('Unsafe source member')
                    target=root/relative
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(source.extractfile(member).read())
        include=Path(sysconfig.get_path('include'))
        output=prefix/('_sqlite3'+sysconfig.get_config_var('EXT_SUFFIX'))
        cmd=['cc','-bundle','-undefined','dynamic_lookup','-O2','-arch',platform.machine(),'-DPy_BUILD_CORE_MODULE','-DMODULE_NAME="sqlite3"',
             '-I'+str(include),'-I'+str(include/'internal'),'-I'+str(root),'-I'+str(sqlite_prefix/'include'),
             *map(str,root.glob('*.c')),str(sqlite_prefix/'libsqlite3.0.dylib'),'-Wl,-rpath,'+str(sqlite_prefix),'-o',str(output)]
        subprocess.run(cmd,check=True)
    print(output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-source',type=Path,required=True)
    parser.add_argument('--sqlite-prefix',type=Path,required=True)
    parser.add_argument('--prefix',type=Path,required=True)
    args=parser.parse_args()
    build(args.python_source,args.sqlite_prefix,args.prefix)
