"""V2-only resource monitoring, atomic receipts and prospectively sealed checks."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import duckdb
import pyarrow.parquet as pq

try:
    import psutil
except ImportError:
    psutil = None

ROOT = Path(__file__).resolve().parents[5]
W = ROOT / 'work'
R = W / 'v2_research'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(8<<20), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, data):
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.pending')
    temporary.write_text(json.dumps(data,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def log(operation, **data):
    with (W/'v2_access_ledger.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'recorded_utc':datetime.now(timezone.utc).isoformat(),
                                'operation':operation,'sealed_label_read':False,**data},sort_keys=True)+'\n')


def connect(name='analysis'):
    temporary = R / 'tmp' / name
    temporary.mkdir(parents=True,exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads=1; SET memory_limit='700MB'; SET preserve_insertion_order=false")
    con.execute('SET temp_directory=?',[temporary.as_posix()])
    con.execute("SET max_temp_directory_size='24GB'")
    return con


def populations():
    """Retired API: never decode sealed membership to authorize research."""
    raise PermissionError('Broad population reader retired by V3 firewall; use research-only positive authorization')


class Monitor:
    def __init__(self,directory):
        self.directory=Path(directory); self.done=threading.Event()
        self.rss=0; self.disk=0; self.start=time.monotonic()

    def __enter__(self):
        def rss_bytes():
            if psutil is not None:
                process=psutil.Process()
                return sum(p.memory_info().rss for p in [process,*process.children(recursive=True)] if p.is_running())
            # The frozen candidate generator is in process; no child workers.
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD),
                            ('PeakWorkingSetSize',ctypes.c_size_t),('WorkingSetSize',ctypes.c_size_t),
                            ('QuotaPeakPagedPoolUsage',ctypes.c_size_t),('QuotaPagedPoolUsage',ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage',ctypes.c_size_t),('QuotaNonPagedPoolUsage',ctypes.c_size_t),
                            ('PagefileUsage',ctypes.c_size_t),('PeakPagefileUsage',ctypes.c_size_t)]
            value=Counters(); value.cb=ctypes.sizeof(value)
            ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
            handle=ctypes.windll.kernel32.GetCurrentProcess()
            if not ctypes.windll.psapi.GetProcessMemoryInfo(handle,ctypes.byref(value),value.cb):
                raise OSError('GetProcessMemoryInfo failed')
            return int(value.WorkingSetSize)
        def watch():
            while not self.done.is_set():
                try:
                    self.rss=max(self.rss,rss_bytes())
                    self.disk=max(self.disk,sum(p.stat().st_size for p in self.directory.rglob('*') if p.is_file()))
                except (OSError,RuntimeError): pass
                self.done.wait(1)
        self.thread=threading.Thread(target=watch,daemon=True); self.thread.start()
        return self

    def __exit__(self,*args):
        self.done.set(); self.thread.join()
        self.wall=time.monotonic()-self.start

    def result(self):
        return {'wall_seconds':self.wall,'sampled_peak_process_tree_rss_bytes':self.rss,
                'sampled_peak_temp_disk_bytes':self.disk,'sampling_interval_seconds':1,
                'rss_scope':'process tree' if psutil is not None else 'current process (frozen generator has no child workers)',
                'duckdb_threads':1,'duckdb_memory_mb':700}
