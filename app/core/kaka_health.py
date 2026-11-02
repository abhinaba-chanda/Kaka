r"""
core/kaka_health.py - Kaka update 19: PC health + low-battery alert.

battery()  GetSystemPowerStatus (no admin) -> % / charging / time left
cpu, RAM   psutil (in his venv since update 1), ctypes fallback for RAM
disks      C:\ and F:\ free space
top apps   top 3 by RAM and by CPU (psutil, one 0.5 s sample)
temps      MSAcpi_ThermalZoneTemperature (often needs admin -> honestly "Windows
           nahi bata raha"), GPU via nvidia-smi when present (RTX 2050)
alert      background check every 60 s: battery <= 20 % and not charging ->
           Kaka says it once, again at <= 10 %. Game mode -> HUD popup only.
Nothing here raises.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional

from core import kaka_services as ks

_IS_WIN = sys.platform == "win32"
CHECK_S = 60.0
_ALERT = {"20": False, "10": False}
_STARTED = {"on": False}
_NO_WINDOW = 0x08000000 if _IS_WIN else 0

# test hooks (tests replace these)
_power_status = None        # () -> dict


# ------------------------------------------------------------------ battery
def _win_power() -> Optional[dict]:
    import ctypes
    from ctypes import wintypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    s = SPS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    return {"ac": s.ACLineStatus & 0xFF, "flag": s.BatteryFlag & 0xFF,
            "pct": s.BatteryLifePercent & 0xFF, "secs": int(s.BatteryLifeTime),
            "saver": s.SystemStatusFlag & 0xFF}


def battery() -> dict:
    """-> {"has": bool, "pct": int|None, "charging": bool|None, "left_s": int|None}"""
    raw = None
    try:
        raw = _power_status() if _power_status else (_win_power() if _IS_WIN else None)
    except Exception:
        raw = None
    if raw:
        flag, pct = raw.get("flag", 255), raw.get("pct", 255)
        if flag == 128 or (flag == 255 and pct == 255):
            return {"has": False, "pct": None, "charging": None, "left_s": None}
        secs = raw.get("secs")
        return {"has": True, "pct": None if pct == 255 else int(pct),
                "charging": (raw.get("ac") == 1) if raw.get("ac") in (0, 1) else None,
                "left_s": None if secs in (None, -1, 0xFFFFFFFF) else int(secs),
                "saver": bool(raw.get("saver"))}
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is None:
            return {"has": False, "pct": None, "charging": None, "left_s": None}
        left = b.secsleft if isinstance(b.secsleft, int) and b.secsleft > 0 else None
        return {"has": True, "pct": int(round(b.percent)), "charging": bool(b.power_plugged),
                "left_s": left}
    except Exception:
        return {"has": False, "pct": None, "charging": None, "left_s": None, "unknown": True}


def battery_text(b: dict) -> str:
    if not b.get("has"):
        return ("Battery nahi mili (desktop / charger pe hi chalta hai)" if not b.get("unknown")
                else "Battery Windows nahi bata raha")
    if b.get("pct") is None:
        return "Battery hai par % Windows nahi bata raha"
    s = f"Battery {b['pct']}%"
    if b.get("charging"):
        s += ", charge ho rahi hai"
    elif b.get("charging") is False:
        s += ", charger nahi laga"
        if b.get("left_s"):
            h, m = divmod(b["left_s"] // 60, 60)
            s += f", lagbhag {h} ghanta {m} minute bachi" if h else f", lagbhag {m} minute bachi"
    return s


# ------------------------------------------------------------------ cpu / ram / disk / apps
def _ram_ctypes() -> Optional[tuple]:
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys, m.ullTotalPhys - m.ullAvailPhys
    except Exception:
        return None


def sample(seconds: float = 0.5) -> dict:
    """One CPU sample for the system and every process (~0.5 s)."""
    out = {"cpu": None, "ram_total": None, "ram_used": None, "top_ram": [], "top_cpu": []}
    try:
        import psutil
    except Exception:
        psutil = None
    if psutil is not None:
        procs = []
        try:
            for p in psutil.process_iter(["name"]):
                try:
                    p.cpu_percent(None)
                    procs.append(p)
                except Exception:
                    pass
            psutil.cpu_percent(None)
            time.sleep(seconds)
            out["cpu"] = round(psutil.cpu_percent(None))
            vm = psutil.virtual_memory()
            out["ram_total"], out["ram_used"] = vm.total, vm.total - vm.available
            ncpu = psutil.cpu_count() or 1
            ram, cpu = {}, {}
            for p in procs:
                try:
                    n = (p.info.get("name") or "?").replace(".exe", "")
                    if n.lower() in ("system idle process", "idle", "system", "registry", "memory compression"):
                        continue
                    ram[n] = ram.get(n, 0) + p.memory_info().rss
                    cpu[n] = cpu.get(n, 0.0) + p.cpu_percent(None) / ncpu
                except Exception:
                    pass
            out["top_ram"] = sorted(ram.items(), key=lambda x: -x[1])[:3]
            out["top_cpu"] = [x for x in sorted(cpu.items(), key=lambda x: -x[1])[:3] if x[1] >= 1.0]
        except Exception as e:
            ks._print(f"[health] psutil sample failed: {e}")
    if out["ram_total"] is None and _IS_WIN:
        r = _ram_ctypes()
        if r:
            out["ram_total"], out["ram_used"] = r
    return out


def disks() -> list:
    res = []
    for d in (("C:\\", "F:\\") if _IS_WIN else ("/",)):
        try:
            u = shutil.disk_usage(d)
            res.append((d[:2] if _IS_WIN else d, u.free, u.total))
        except Exception:
            pass
    return res


# ------------------------------------------------------------------ temperatures
def _run(args, timeout=4.0) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           creationflags=_NO_WINDOW)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def cpu_temp() -> Optional[float]:
    """ACPI thermal zone (needs admin on most PCs -> None)."""
    if not _IS_WIN:
        return None
    out = _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                "(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
                "-ErrorAction Stop | Select-Object -ExpandProperty CurrentTemperature) -join ','"],
               timeout=5.0)
    return parse_acpi(out)


def parse_acpi(out: str) -> Optional[float]:
    vals = []
    for tok in (out or "").replace("\n", ",").split(","):
        tok = tok.strip()
        if tok.isdigit():
            c = int(tok) / 10.0 - 273.15
            if 5 < c < 125:
                vals.append(c)
    return round(max(vals), 0) if vals else None


def _nvidia_smi() -> str:
    p = shutil.which("nvidia-smi")
    if p:
        return p
    for c in (r"C:\Windows\System32\nvidia-smi.exe",
              r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if os.path.exists(c):
            return c
    return ""


def gpu() -> Optional[dict]:
    exe = _nvidia_smi()
    if not exe:
        return None
    return parse_nvsmi(_run([exe, "--query-gpu=name,temperature.gpu,utilization.gpu",
                             "--format=csv,noheader,nounits"], timeout=4.0))


def parse_nvsmi(out: str) -> Optional[dict]:
    line = (out or "").strip().splitlines()[0] if (out or "").strip() else ""
    parts = [x.strip() for x in line.split(",")]
    if len(parts) < 3:
        return None
    try:
        return {"name": parts[0], "temp": float(parts[1]), "util": float(parts[2])}
    except ValueError:
        return None


# ------------------------------------------------------------------ report
def _gb(b) -> str:
    return f"{b / 1024 ** 3:.1f} GB"


def report(focus: str = "all") -> dict:
    """-> {"text": spoken summary, "detail": popup text, "data": raw}"""
    t0 = time.monotonic()
    temps = {}

    def _t():
        temps["cpu"] = cpu_temp()

    def _g():
        temps["gpu"] = gpu()
    ths = []
    if focus in ("all", "temp", "slow"):
        ths = [threading.Thread(target=_t, daemon=True), threading.Thread(target=_g, daemon=True)]
        for th in ths:
            th.start()
    b = battery()
    s = sample(0.5) if focus != "battery" else {"cpu": None, "ram_total": None, "ram_used": None,
                                                  "top_ram": [], "top_cpu": []}
    dk = disks() if focus in ("all", "slow", "disk") else []
    for th in ths:
        th.join(timeout=max(0.1, 5.5 - (time.monotonic() - t0)))
    data = {"battery": b, **s, "disks": dk, "cpu_temp": temps.get("cpu"), "gpu": temps.get("gpu")}
    text, detail = compose(data, focus)
    ks.timing("pc health", time.monotonic() - t0, focus)
    return {"text": text, "detail": detail, "data": data}


def compose(d: dict, focus: str = "all") -> tuple:
    lines, say = [], []
    b = d.get("battery") or {}
    bt = battery_text(b)
    lines.append(bt)
    if focus in ("all", "battery"):
        say.append(bt)
    if d.get("cpu") is not None:
        lines.append(f"CPU {d['cpu']}%")
    if d.get("ram_total"):
        pct = round(100 * d["ram_used"] / d["ram_total"])
        lines.append(f"RAM {_gb(d['ram_used'])} / {_gb(d['ram_total'])} ({pct}%)")
    if d.get("top_ram"):
        lines.append("Sabse zyada RAM: " + ", ".join(f"{n} {_gb(v)}" for n, v in d["top_ram"]))
    if d.get("top_cpu"):
        lines.append("Sabse zyada CPU: " + ", ".join(f"{n} {v:.0f}%" for n, v in d["top_cpu"]))
    for name, free, total in d.get("disks") or []:
        lines.append(f"{name} drive: {_gb(free)} khaali (of {_gb(total)})")
    ct, g = d.get("cpu_temp"), d.get("gpu")
    temp_bits = []
    if ct is not None:
        temp_bits.append(f"CPU {ct:.0f} degree")
    if g:
        temp_bits.append(f"GPU ({g['name']}) {g['temp']:.0f} degree, {g['util']:.0f}% busy")
    temp_line = ("Temperature: " + ", ".join(temp_bits)) if temp_bits else \
        "Temperature Windows nahi bata raha (CPU temp ke liye admin chahiye)"
    if focus in ("all", "temp", "slow"):
        lines.append(temp_line)
    # ---- spoken part
    ram_pct = round(100 * d["ram_used"] / d["ram_total"]) if d.get("ram_total") else None
    if focus in ("all", "ram", "cpu"):
        if d.get("cpu") is not None:
            say.append(f"CPU {d['cpu']}%")
        if ram_pct is not None:
            say.append(f"RAM {_gb(d['ram_used'])} of {_gb(d['ram_total'])} use ho rahi ({ram_pct}%)")
        if d.get("top_ram"):
            say.append("sabse zyada " + ", ".join(n for n, _v in d["top_ram"]))
    if focus == "temp":
        hot = (ct is not None and ct >= 85) or (g and g["temp"] >= 85)
        say.append(temp_line + (" - kaafi garam hai" if hot else (" - theek hai" if temp_bits else "")))
        if d.get("cpu") is not None:
            say.append(f"CPU {d['cpu']}% busy")
    if focus == "slow":
        why = []
        if d.get("cpu") is not None and d["cpu"] >= 85:
            why.append(f"CPU {d['cpu']}% busy" + (f", sabse zyada {d['top_cpu'][0][0]}" if d.get("top_cpu") else ""))
        if ram_pct is not None and ram_pct >= 85:
            why.append(f"RAM {ram_pct}% bhari" + (f", sabse zyada {d['top_ram'][0][0]}" if d.get("top_ram") else ""))
        for name, free, total in d.get("disks") or []:
            if name.upper().startswith("C") and free < 10 * 1024 ** 3:
                why.append(f"C drive mein sirf {_gb(free)} khaali")
        if (ct is not None and ct >= 90) or (g and g["temp"] >= 90):
            why.append("PC bahut garam hai (garam hone pe speed kam ho jaati hai)")
        if b.get("has") and b.get("charging") is False and (b.get("saver") or (b.get("pct") or 100) <= 20):
            why.append("battery kam hai / battery saver - charger lagao")
        if why:
            say.append("Slow hone ki wajah: " + "; ".join(why))
        else:
            say.append(f"Abhi sab normal lag raha hai (CPU {d.get('cpu')}%, RAM {ram_pct}%)"
                       + (f"; sabse zyada RAM {d['top_ram'][0][0]} le raha" if d.get("top_ram") else ""))
    if focus == "disk":
        say += [f"{n} drive {_gb(f)} khaali" for n, f, _t in d.get("disks") or []]
    if focus == "all":
        say.append(temp_line)
    return "; ".join(x for x in say if x) + ".", "\n".join(lines)


# ------------------------------------------------------------------ low-battery alert
def check_battery(b: Optional[dict] = None) -> str:
    """One alert step. -> "" or what was said/shown."""
    b = b or battery()
    if not b.get("has") or b.get("pct") is None:
        return ""
    pct = b["pct"]
    if b.get("charging") or pct > 25:
        _ALERT["20"] = _ALERT["10"] = False
        return ""
    msg = ""
    if pct <= 10 and not _ALERT["10"]:
        _ALERT["10"] = _ALERT["20"] = True
        msg = f"Battery sirf {pct}% hai, abhi charger laga lo"
    elif pct <= 20 and not _ALERT["20"]:
        _ALERT["20"] = True
        msg = f"Battery {pct}% hai, charger laga lo"
    if not msg:
        return ""
    ks.popup("Battery \U0001F50B", msg)
    ks.log(f"SYS: {msg}")
    if ks.in_game_mode():
        return "popup:" + msg
    ks.say_async(msg, urgent=False)
    return "say:" + msg


def _loop():
    while True:
        try:
            b = battery()
            if not b.get("has") and not b.get("unknown"):
                ks._print("[health] no battery - low-battery alert off")
                return
            check_battery(b)
        except Exception as e:
            ks._print(f"[health] battery check failed: {e}")
        time.sleep(CHECK_S)


def start() -> None:
    if _STARTED["on"]:
        return
    _STARTED["on"] = True
    threading.Thread(target=_loop, name="kaka-u19-battery", daemon=True).start()
