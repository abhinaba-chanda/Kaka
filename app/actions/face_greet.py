r"""
actions/face_greet.py - Kaka update 21 (feature 30): "mera chehra yaad karo",
"mera chehra bhool jao" (asks once), "face greet band / chalu karo".
Logic: core/kaka_face.py (+ core/kaka_alarm.py, imported here so the
unlock/come-back watcher starts with Kaka; the camera is never touched at
startup).
"""
try:                                     # stdlib-only imports; register the starter
    from core import kaka_face as _F
    from core import kaka_alarm as _A    # noqa: F401
except Exception:                        # never block tool discovery
    _F = None


def face_greet(parameters: dict = None, player=None) -> str:
    try:
        from core import kaka_face as F
    except Exception as e:
        return f"Face greet load nahi hua: {e}"
    p = parameters or {}
    a = str(p.get("action") or "status").lower().strip()
    try:
        if a in ("enroll", "enrol", "learn", "yaad"):
            return F.enroll_async(bool(p.get("save_photo")))
        if a in ("forget", "delete", "bhool"):
            if not F.enrolled():
                n = F.forget()
                return "Tumhara chehra pehle se yaad nahi hai." + (" Bacha hua data bhi saaf kar diya." if n else "")
            if not (p.get("go_ahead") or p.get("confirm")):
                return ("[ASK_ONCE] Ek baar poocho: \"Tumhare chehre ka data mita doon? Phir greet aur chor "
                        "alarm tumhe pehchan nahi payenge.\" Haan pe isi tool ko action=forget, go_ahead=true "
                        "ke saath call karo.")
            n = F.forget()
            return f"Mita diya - F:\\Kaka\\Data\\face khaali ({n} file). Ab main tumhara chehra nahi pehchanunga."
        if a in ("on", "chalu"):
            F.save_settings(greet=True)
            extra = "" if F.enrolled() else " Pehle 'mera chehra yaad karo' bolo."
            return "Face greet chalu - 10 min baad wapas aaoge toh 'Welcome back' bolunga." + extra
        if a in ("off", "band"):
            F.save_settings(greet=False)
            return "Face greet band kar diya. (Chor alarm alag hai.)"
        return F.status()
    except Exception as e:
        return f"Face greet mein dikkat: {e}"


TOOL = {
    "name": "face_greet",
    "description": ("Kaka pehchane uska chehra (webcam). 'mera chehra yaad karo' -> enroll (camera ~5 s, "
                    "sirf numbers save, photo nahi; save_photo=true sirf agar woh photo rakhne bole); "
                    "'mera chehra bhool jao' -> forget (asks once); 'face greet band/chalu karo' -> off/on; "
                    "'face greet on hai?' -> status. Use this, not kaka_memory, for his face. Say the result as-is."),
    "parameters": {"type": "OBJECT", "properties": {
        "action": {"type": "STRING", "enum": ["enroll", "forget", "on", "off", "status"]},
        "save_photo": {"type": "BOOLEAN"},
        "go_ahead": {"type": "BOOLEAN"}},
        "required": ["action"]},
    "handler": face_greet,
}
