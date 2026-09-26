"""Step 1 freeze report: stable checksums + machine resource conditions.

Prints sha256 + row-count for each frozen artifact, machine RAM (ctypes
GlobalMemoryStatusEx — psutil is not installed), and free disk on work/.
No mutation of any frozen artifact. Read-only.
"""
import ctypes
import hashlib
import os
import shutil
import sys
from ctypes import wintypes

import duckdb

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ART = [
    ("work/split_s1.parquet", "DEVELOPMENT split (val used for SELECTION)"),
    ("work/split_s1_grouped.parquet", "GROUPED stress-test (diagnostic; frozen until policy chosen)"),
    ("code/business_entity_resolution/src/er/normalize.py", "normalization version"),
    ("code/business_entity_resolution/src/er/evaluate.py", "evaluator version"),
]


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class MEMSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def main():
    print("# Step 1 — Foundation freeze report\n")
    con = duckdb.connect()
    print("## Frozen artifact identifiers (sha256)\n")
    for path, role in ART:
        digest = sha(path)
        extra = ""
        if path.endswith(".parquet"):
            n = con.sql(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]
            tr, vl = con.sql(f"""SELECT count(*) FILTER(WHERE split='train'),
                count(*) FILTER(WHERE split='val') FROM read_parquet('{path}')""").fetchone()
            extra = f" rows={n} train={tr} val={vl}"
        print(f"- `{path}`  ({role})")
        print(f"  sha256={digest}{extra}")
    m = MEMSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    du = shutil.disk_usage(os.path.abspath("work"))
    print("\n## Machine resource conditions (measured now)\n")
    print(f"- RAM total={m.ullTotalPhys/2**30:.2f} GiB, available={m.ullAvailPhys/2**30:.2f} GiB, "
          f"memory load={m.dwMemoryLoad}%")
    print(f"- disk (work/ volume): free={du.free/2**30:.1f} GiB of {du.total/2**30:.1f} GiB")
    print(f"- DuckDB experiment config: memory_limit=512MB, threads=4, temp_directory=work/duckdb_tmp")


if __name__ == "__main__":
    main()
