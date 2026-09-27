"""Release artifact checksums and restart manifests."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import ctypes
from ctypes import wintypes
import threading


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def valid_part(path: Path, manifest: Path, expected_inputs: dict | None = None) -> bool:
    if not path.exists() or not manifest.exists():
        return False
    record = json.loads(manifest.read_text(encoding="utf-8"))
    return (record.get("status") == "complete" and
            record.get("sha256") == sha256(path) and
            record.get("bytes") == path.stat().st_size and
            (expected_inputs is None or record.get("inputs") == expected_inputs))


def record_part(path: Path, manifest: Path, rows: int, inputs: dict, **fields) -> dict:
    result = {"status": "complete", "path": path.as_posix(), "bytes": path.stat().st_size,
              "sha256": sha256(path), "rows": rows, "inputs": inputs, **fields}
    save_json(manifest, result)
    return result


def current_rss() -> int:
    """Windows process working set in bytes; this release targets Windows."""
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.WorkingSetSize)


class ResourceMonitor:
    def __init__(self, scratch: Path | None = None):
        self.scratch = scratch
        self.peak_rss_bytes = 0
        self.peak_temp_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _poll(self) -> None:
        self.peak_rss_bytes = max(self.peak_rss_bytes, current_rss())
        if self.scratch is not None and self.scratch.exists():
            size = sum(p.stat().st_size for p in self.scratch.rglob("*") if p.is_file())
            self.peak_temp_bytes = max(self.peak_temp_bytes, size)

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            self._poll()

    def __enter__(self):
        self._poll()
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        self._poll()
