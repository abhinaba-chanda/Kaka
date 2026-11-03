"""KAKA v1.1: PC doctor - "PC slow kyun hai?", "PC check karo".

A full check-up built on core/kaka_health.py (battery, CPU, RAM, top apps,
temperatures, drives) plus: every drive's free space, how long the PC has been
on (uptime), how many apps start with Windows, process count - and a detailed
diagnosis with concrete tips. Read-only; nothing is changed. Never raises.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _gb(b) -> str:
    b = float(b or 0)
    if b >= 1024 ** 3:
        return f"{b / 1024 ** 3:.1f} GB"
    return f"{b / 1024 ** 2:.0f} MB"


def uptime_s():
    try:
        import psutil
        return max(0.0, time.time() - psutil.boot_time())
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            return ctypes.windll.kernel32.GetTickCount64() / 1000.0
        except Exception:
            pass
    return None


def uptime_text(s) -> str:
    if s is None:
        return "pata nahi"
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d} din {h} ghante"
    if h:
        return f"{h} ghante {m} minute"
    return f"{m} minute"


def startup_apps() -> list:
    """Names of apps that start with Windows (Run keys + Startup folders)."""
    names = []
    if sys.platform == "win32":
        try:
            import winreg
            keys = [(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
                    (winreg.HKEY_LOCAL_MACHINE,
                     r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run")]
            disabled = set()
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                                    r"\StartupApproved\Run") as k:
                    i = 0
                    while True:
                        try:
                            n, v, _t = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        if isinstance(v, (bytes, bytearray)) and v[:1] in (b"\x03", b"\x01"):
                            disabled.add(n.lower())
                        i += 1
            except Exception:
                pass
            for hive, path in keys:
                try:
                    with winreg.OpenKey(hive, path) as k:
                        i = 0
                        while True:
                            try:
                                n, _v, _t = winreg.EnumValue(k, i)
                            except OSError:
                                break
                            if n.lower() not in disabled:
                                names.append(n)
                            i += 1
                except Exception:
                    pass
        except Exception:
            pass
        for folder in (Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup",
                       Path(os.environ.get("ProgramData", r"C:\ProgramData"))
                       / r"Microsoft\Windows\Start Menu\Programs\Startup"):
            try:
                for f in folder.iterdir():
                    if f.suffix.lower() in (".lnk", ".exe", ".bat", ".cmd", ".url"):
                        names.append(f.stem)
            except Exception:
                pass
    else:
        try:
            for f in (Path.home() / ".config" / "autostart").glob("*.desktop"):
                names.append(f.stem)
        except Exception:
            pass
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def all_drives() -> list:
    """[(name, free, total)] for every fixed drive."""
    out = []
    try:
        import psutil
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except Exception:
                continue
            name = part.device.rstrip("\\/") if sys.platform == "win32" else part.mountpoint
            if any(name == n for n, _f, _t in out):
                continue
            out.append((name, u.free, u.total))
    except Exception:
        pass
    return out


def process_count():
    try:
        import psutil
        return len(psutil.pids())
    except Exception:
        return None


def checkup() -> dict:
    """-> {"text": detailed spoken diagnosis, "detail": popup text, "data": raw}"""
    t0 = time.monotonic()
    data = {}
    try:
        from core import kaka_health as H
        data = dict(H.report("all").get("data") or {})
    except Exception:
        try:
            import psutil
            vm = psutil.virtual_memory()
            data = {"cpu": psutil.cpu_percent(interval=0.5), "ram_total": vm.total,
                    "ram_used": vm.total - vm.available, "top_ram": [], "top_cpu": [],
                    "battery": {}, "cpu_temp": None, "gpu": None}
        except Exception:
            data = {}
    up = uptime_s()
    starts = startup_apps()
    drives = all_drives() or data.get("disks") or []
    procs = process_count()
    data.update({"uptime": up, "startup": starts, "drives": drives, "procs": procs})
    text, detail = diagnose(data)
    data["secs"] = round(time.monotonic() - t0, 2)
    return {"text": text, "detail": detail, "data": data}


def diagnose(d: dict) -> tuple:
    cpu = d.get("cpu")
    ram_pct = (round(100 * d["ram_used"] / d["ram_total"])
               if d.get("ram_total") and d.get("ram_used") is not None else None)
    lines, problems, tips = [], [], []
    if cpu is not None:
        lines.append(f"CPU: {cpu}% busy")
    if ram_pct is not None:
        lines.append(f"RAM: {_gb(d['ram_used'])} / {_gb(d['ram_total'])} ({ram_pct}%)")
    if d.get("top_ram"):
        lines.append("Sabse zyada RAM: " + ", ".join(f"{n} {_gb(v)}" for n, v in d["top_ram"]))
    if d.get("top_cpu"):
        lines.append("Sabse zyada CPU: " + ", ".join(f"{n} {v:.0f}%" for n, v in d["top_cpu"]))
    for name, free, total in d.get("drives") or []:
        pct_free = round(100 * free / total) if total else 0
        lines.append(f"Drive {name}: {_gb(free)} khaali of {_gb(total)} ({pct_free}% free)")
        if total and (free < 10 * 1024 ** 3 or pct_free < 10):
            problems.append(f"{name} drive mein sirf {_gb(free)} jagah bachi hai")
            tips.append(f"{name} drive se purani downloads / videos hatao, ya 'junk saaf karo' bolo")
    ct, g = d.get("cpu_temp"), d.get("gpu")
    if ct is not None:
        lines.append(f"CPU temp: {ct:.0f} C")
    if g:
        lines.append(f"GPU: {g.get('name', '')} {g.get('temp', 0):.0f} C, {g.get('util', 0):.0f}% busy")
    if ct is None and not g:
        lines.append("Temperature: Windows nahi bata raha (admin / sensor nahi)")
    up = d.get("uptime")
    lines.append(f"PC on hai: {uptime_text(up)}")
    starts = d.get("startup") or []
    lines.append(f"Windows ke saath start hone wale apps: {len(starts)}"
                 + (f" ({', '.join(starts[:8])}{'...' if len(starts) > 8 else ''})" if starts else ""))
    if d.get("procs") is not None:
        lines.append(f"Chal rahe processes: {d['procs']}")
    b = d.get("battery") or {}
    if b.get("has"):
        lines.append(f"Battery: {b.get('pct')}%" + (" (charging)" if b.get("charging") else ""))

    if cpu is not None and cpu >= 80:
        top = d["top_cpu"][0][0] if d.get("top_cpu") else ""
        problems.append(f"CPU {cpu}% busy hai" + (f", sabse zyada {top} kha raha hai" if top else ""))
        tips.append((f"{top} band karo ya uska kaam khatam hone do" if top else
                     "jo heavy app chal raha hai use band karo"))
    if ram_pct is not None and ram_pct >= 80:
        top = d["top_ram"][0][0] if d.get("top_ram") else ""
        problems.append(f"RAM {ram_pct}% bhari hai" + (f", sabse zyada {top}" if top else ""))
        tips.append("browser ke extra tabs aur jo apps use nahi ho rahe wo band karo")
    if (ct is not None and ct >= 85) or (g and g.get("temp", 0) >= 85):
        problems.append("PC garam hai - garam hone pe Windows speed kam kar deta hai")
        tips.append("laptop ko hard surface pe rakho, vents saaf karo, cooling pad helpful hai")
    if up is not None and up > 3 * 86400:
        problems.append(f"PC {uptime_text(up)} se restart nahi hua")
        tips.append("ek baar restart karo - memory leaks aur pending updates saaf ho jaate hain")
    if len(starts) > 8:
        problems.append(f"{len(starts)} apps Windows ke saath start hote hain")
        tips.append("Task Manager > Startup apps mein jo zaroori nahi unhe Disable karo")
    if b.get("has") and b.get("charging") is False and (b.get("pct") or 100) <= 20:
        problems.append("battery kam hai, Windows battery bachane ke liye slow chalata hai")
        tips.append("charger lagao")

    if problems:
        say = ("PC check kar liya. Slow hone ki main wajah: " + "; ".join(problems) + ". "
               + "Kya karo: " + "; ".join(dict.fromkeys(tips)) + ".")
    else:
        say = ("PC check kar liya, sab theek lag raha hai"
               + (f": CPU {cpu}%" if cpu is not None else "")
               + (f", RAM {ram_pct}%" if ram_pct is not None else "")
               + f", PC {uptime_text(up)} se on hai, {len(starts)} startup apps. "
               + "Agar phir bhi slow lage toh restart karo aur 'junk saaf karo' bolo.")
    return say, "\n".join(lines + ([""] + ["Problem: " + p for p in problems] if problems else [])
                         + ([""] + ["Tip: " + t for t in dict.fromkeys(tips)] if tips else []))
