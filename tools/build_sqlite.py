"""Build an explicitly pinned upstream SQLite for Python's existing _sqlite3 ABI.

Explicit development/CI/image-build tool, never called by application startup.
Only the SHA3-verified amalgamation is compiled; archive scripts are not run.
"""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION='3.54.1'
ARCHIVE='sqlite-amalgamation-3540100'
URL='https://www.sqlite.org/2026/'+ARCHIVE+'.zip'
C_SHA3='e2b869d3003b33c942b7dd7063604fbc53553e1b66609570df39b9b7b558ee81'
ARCHIVE_SHA256='0a9c8e34352dad46006aa82836d5383bff9dc6bc9ce5a60553c05aa30e6ad1f9'
SOURCE_ID='2026-10-16 14:45:20 555e07ef33f69a0957d24d8e555b702464a131d6f1607f4c3c1e4551e8e6ca76'


def build(prefix, archive=None):
    prefix.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blago-sqlite-build-') as root:
        root=Path(root)
        package=archive or root/'sqlite.zip'
        if archive is None:
            with urllib.request.urlopen(URL,timeout=60) as response:
                package.write_bytes(response.read())
        if hashlib.sha256(package.read_bytes()).hexdigest()!=ARCHIVE_SHA256:
            raise ValueError('SQLite archive SHA256 mismatch')
        with zipfile.ZipFile(package) as bundle:
            source=bundle.read(ARCHIVE+'/sqlite3.c')
            header=bundle.read(ARCHIVE+'/sqlite3.h')
        if hashlib.sha3_256(source).hexdigest()!=C_SHA3:
            raise ValueError('Upstream sqlite3.c SHA3 mismatch; refusing to compile')
        code=root/'sqlite3.c'
        code.write_bytes(source)
        (prefix/'include').mkdir(exist_ok=True)
        (prefix/'include'/'sqlite3.h').write_bytes(header)
        library=prefix/('libsqlite3.0.dylib' if platform.system()=='Darwin' else 'libsqlite3.so.0')
        flags=['-O2','-fPIC','-DSQLITE_THREADSAFE=1','-DSQLITE_ENABLE_FTS5','-DSQLITE_ENABLE_RTREE','-DSQLITE_ENABLE_COLUMN_METADATA','-DSQLITE_ENABLE_MATH_FUNCTIONS']
        link=['-dynamiclib','-Wl,-install_name,@rpath/libsqlite3.0.dylib'] if platform.system()=='Darwin' else ['-shared','-Wl,-soname,libsqlite3.so.0','-ldl','-lpthread']
        subprocess.run([os.getenv('CC','cc'),*flags,str(code),*link,'-lm','-o',str(library)],check=True)
        (prefix/'build-evidence.json').write_text(json.dumps({'version':VERSION,'source_id':SOURCE_ID,'source_url':URL,
            'sqlite3_c_sha3_256':C_SHA3,'library_sha256':hashlib.sha256(library.read_bytes()).hexdigest()},indent=2)+'\n')
    print(library)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    args=parser.parse_args()
    build(args.prefix,args.archive)
