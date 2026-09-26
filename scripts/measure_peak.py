"""Measure the TRUE process peak working set (RSS high-water mark) of a data job,
not DuckDB's internal buffer-pool figure.

Runs the target **in this process** via runpy (so a venv ``python.exe`` launcher
stub cannot hide the real interpreter as an unmeasured grandchild — the failure mode
that made a naive child-process probe report a constant ~5 MiB), then prints the
OS-maintained ``PeakWorkingSetSize`` for the current process. That peak is the whole
Python + pyarrow + DuckDB resident high-water mark — strictly larger than
``duckdb_memory()``, which counts only DuckDB's own buffers.

    .venv/Scripts/python scripts/measure_peak.py --script scripts/integrity_check.py -- --data-dir dataset
    .venv/Scripts/python scripts/measure_peak.py --module er.split -- --data-dir dataset --seed 42
"""
import argparse
import ctypes
import runpy
import sys
from ctypes import wintypes


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_working_set() -> int:
    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
    c = PROCESS_MEMORY_COUNTERS()
    c.cb = ctypes.sizeof(c)
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--script")
    g.add_argument("--module")
    ap.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    rest = args.rest[1:] if args.rest and args.rest[0] == "--" else args.rest

    rc = 0
    try:
        if args.script:
            sys.argv = [args.script, *rest]
            runpy.run_path(args.script, run_name="__main__")
        else:
            sys.argv = [args.module, *rest]
            runpy.run_module(args.module, run_name="__main__", alter_sys=True)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)

    pk = peak_working_set()
    print(f"[measure_peak] rc={rc} peak_working_set_bytes={pk} ({pk/2**20:.1f} MiB)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
