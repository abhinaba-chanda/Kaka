import os
import shutil
import subprocess
import sys
import json
import re
import threading
import time
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR           = get_base_dir()
API_CONFIG_PATH    = BASE_DIR / "config" / "api_keys.json"
DESKTOP            = Path.home() / "Desktop"


def _kaka_saved(sub: str = "") -> Path:
    """KAKA: files Kaka writes go to F:\\Kaka\\Data (kaka_paths), not the Desktop."""
    try:
        import kaka_paths as _kpaths
        return _kpaths.data_dir(*(["Saved", sub] if sub else ["Saved"]))
    except Exception:
        return DESKTOP
MAX_BUILD_ATTEMPTS = 3
# Model choice lives in core/gemini.py, and so does the timeout and the
# fallback ladder. Writing a model name here is what left this file hanging
# forever whenever that one alias was unwell.
from core import gemini


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _get_gemini(tier: str = gemini.SMART):
    """Writing and fixing code is the reasoning tier; a 60s deadline because a
    whole file can come back."""
    class _W:
        def generate_content(self, contents):
            resp = gemini.call(contents, tier=tier, timeout_ms=60000)
            if resp is None:
                raise RuntimeError("every Gemini model on the ladder failed")
            return resp

    return _W()


def _clean_code(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _resolve_save_path(output_path: str, language: str) -> Path:
    ext_map = {
        "python": ".py", "py": ".py",
        "javascript": ".js", "js": ".js",
        "typescript": ".ts", "ts": ".ts",
        "html": ".html", "css": ".css",
        "java": ".java", "cpp": ".cpp", "c": ".c", "c++": ".cpp",
        "nodejs": ".js", "node": ".js",
        "bash": ".sh", "shell": ".sh", "powershell": ".ps1",
        "sql": ".sql", "json": ".json", "rust": ".rs", "go": ".go",
    }
    lang = _norm_lang(language)
    ext = ext_map.get(lang, ".py")
    if output_path:
        p = Path(output_path)
        if not p.suffix:
            p = p.with_suffix(ext)
        return p if p.is_absolute() else _kaka_saved("Code") / p
    return _unique(_kaka_saved("Code") / f"kaka_code{ext}")


# KAKA update 8 --------------------------------------------------------------
_LANG_ALIASES = {
    "c": "c", "c language": "c", "c program": "c", "c code": "c", "clang": "c",
    "c++": "cpp", "cpp": "cpp", "c plus plus": "cpp", "cplusplus": "cpp",
    "python": "python", "py": "python", "python3": "python",
    "javascript": "javascript", "js": "javascript", "node": "javascript",
    "nodejs": "javascript", "node js": "javascript", "node.js": "javascript",
    "java": "java", "html": "html", "css": "css", "typescript": "typescript",
    "ts": "typescript", "bash": "bash", "shell": "bash", "powershell": "powershell",
    "sql": "sql", "json": "json", "rust": "rust", "go": "go", "golang": "go",
}


def _norm_lang(language: str) -> str:
    l = re.sub(r"\s+", " ", (language or "python").strip().lower())
    l = re.sub(r"\b(language|program|programming|code|lang)\b", "", l).strip() or l
    return _LANG_ALIASES.get(l, _LANG_ALIASES.get((language or "").strip().lower(), l or "python"))


def _slug(description: str, limit: int = 40) -> str:
    words = re.findall(r"[a-zA-Z0-9]+", (description or "").lower())
    stop = {"a", "an", "the", "in", "of", "to", "for", "and", "write", "make", "create",
            "code", "program", "c", "likho", "likh", "banao", "bana", "do", "karo", "ek",
            "mein", "me", "usme", "which", "that", "simple", "basic", "please", "kaka"}
    keep = [w for w in words if w not in stop][:5]
    s = "_".join(keep)[:limit].strip("_")
    return s or "program"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 200):
        q = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not q.exists():
            return q
    return path


def _code_path(description: str, language: str, output_path: str) -> Path:
    if output_path:
        return _resolve_save_path(output_path, language)
    ext = Path(_resolve_save_path("", language).name).suffix
    return _unique(_kaka_saved("Code") / f"{_slug(description)}{ext}")


def _open_editor(path: Path) -> str:
    """Open the saved file in VS Code (Notepad if VS Code is missing)."""
    try:
        from core import winapps
        ok, editor = winapps.open_in_editor(str(path))
        return editor if ok else ""
    except Exception as e:
        print(f"[Code] could not open editor: {e}")
        return ""


def _read_file(file_path: str) -> tuple[str, str]:
    if not file_path:
        return "", "No file path provided."
    p = Path(file_path)
    if not p.exists():
        return "", f"File not found: {file_path}"
    try:
        return p.read_text(encoding="utf-8"), ""
    except Exception as e:
        return "", f"Could not read file: {e}"


def _save_file(path: Path, content: str) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Saved to: {path}"
    except Exception as e:
        return f"Could not save: {e}"


def _preview(code: str, lines: int = 10) -> str:
    all_lines = code.splitlines()
    preview   = "\n".join(all_lines[:lines])
    suffix    = f"\n... ({len(all_lines) - lines} more lines)" if len(all_lines) > lines else ""
    return preview + suffix


def _has_error(output: str) -> bool:
    error_signals = ["error", "exception", "traceback", "syntaxerror",
                     "nameerror", "typeerror", "stderr", "failed", "crash"]
    return any(s in output.lower() for s in error_signals)


def _take_screenshot() -> Path | None:
    try:
        import pyautogui
        try:
            import kaka_paths as _kpaths
            _shots = _kpaths.screenshots()
        except Exception:
            _shots = Path.home() / "Desktop"
        screenshot_path = _shots / f"kaka_debug_{int(time.time())}.png"
        screenshot = pyautogui.screenshot()
        screenshot.save(str(screenshot_path))
        print(f"[Code] 📸 Screenshot: {screenshot_path}")
        return screenshot_path
    except Exception as e:
        print(f"[Code] ⚠️ Screenshot failed: {e}")
        return None


def _image_to_base64(path: Path) -> str:
    import base64
    return base64.b64encode(path.read_bytes()).decode("utf-8")


_VALID_INTENTS = {"write", "edit", "explain", "run", "build", "screen_debug", "optimize"}


def _detect_intent(description: str, file_path: str, code: str) -> str:
    """
    Language-independent intent detection — NO fixed keyword list.
    Whatever language the user speaks, the description is classified by
    Gemini. If the API is unreachable, it falls back to language-agnostic
    structural hints (does the file exist on disk, was code provided).
    """
    desc        = (description or "").strip()
    file_exists = bool(file_path) and Path(file_path).exists()

    if desc:
        try:
            ctx = []
            if file_path:
                ctx.append(f"a file path is provided (exists on disk: {file_exists})")
            if code:
                ctx.append("an inline code snippet is provided")
            prompt = (
                "Classify a coding assistant request into exactly ONE intent word.\n"
                "The request may be written in ANY language.\n\n"
                f"Request: {desc}\n"
                + (f"Context: {'; '.join(ctx)}\n" if ctx else "")
                + "\nIntents:\n"
                "  write        = create new code from scratch\n"
                "  edit         = modify an existing file\n"
                "  explain      = describe what given code/file does\n"
                "  run          = execute an existing file\n"
                "  build        = write code, run it, and iterate until it works\n"
                "  screen_debug = analyze an error currently visible on the user's screen\n"
                "  optimize     = refactor / clean up / speed up existing code\n\n"
                "Reply with ONLY the intent word, nothing else."
            )
            ans = _get_gemini().generate_content(prompt).text.strip().lower()
            ans = ans.strip("`'\". \n")
            if ans in _VALID_INTENTS:
                return ans
        except Exception as e:
            print(f"[Code] Intent classification failed ({e}) — structural fallback")

    # Structural fallback — not tied to any language
    if file_exists:
        return "edit" if desc else "explain"
    if code:
        return "explain"
    return "write"

def _write(description: str, language: str, output_path: str, player=None) -> tuple[str, Path]:
    lang  = _norm_lang(language)
    model = _get_gemini()

    hint = _LANG_HINTS.get(lang, "")
    prompt = f"""You are an expert {lang} developer.
Write clean, working, well-commented {lang} code for the description below.
{hint}
If the description is short or vague, write a small, complete, correct program
that does the obvious thing (with a main entry point) - never ask for details.

Rules:
- Output ONLY the code. No explanation, no markdown, no backticks.
- Add helpful inline comments.
- Handle errors and edge cases properly.
- Use modern best practices.

Description: {description}

Code:"""

    response = model.generate_content(prompt)
    code     = _clean_code(response.text)
    path     = _code_path(description, lang, output_path)
    if lang == "java" and not output_path:
        path = _java_path(code, path)
    _save_file(path, code)
    return code, path


_LANG_HINTS = {
    "c": "Standard C (C11) for Windows with gcc: #include <stdio.h>, int main(void) returning 0. No conio.h, no Windows-only headers unless asked.",
    "cpp": "Standard C++17: #include <iostream>, int main() returning 0. No Windows-only headers unless asked.",
    "java": "Exactly ONE public class with public static void main(String[] args). No package line.",
    "javascript": "Node.js (CommonJS, require(...), console.log). Runs with `node file.js`. No browser-only APIs like document/window unless asked for a web page.",
    "python": "Python 3, with if __name__ == '__main__': main().",
}


def _java_path(code: str, path: Path) -> Path:
    """Java needs File name == public class name (Main.java for public class Main)."""
    m = re.search(r"public\s+(?:final\s+|abstract\s+)?class\s+([A-Za-z_]\w*)", code)
    if not m:
        return path
    cls = m.group(1)
    target = path.parent / f"{cls}.java"
    if target.exists():
        sub = _unique(path.parent / path.stem)
        sub.mkdir(parents=True, exist_ok=True)
        target = sub / f"{cls}.java"
    return target


# ---------------------------------------------------------------- toolchains
# winget ids checked in microsoft/winget-pkgs on 1 Nov 2026.
TOOLCHAINS = {
    "python": ("Python 3", "Python.Python.3.13"),
    "node": ("Node.js LTS", "OpenJS.NodeJS.LTS"),
    "java": ("Java JDK 21", "Microsoft.OpenJDK.21"),
    "gcc": ("gcc/g++ (WinLibs MinGW)", "BrechtSanders.WinLibs.POSIX.UCRT"),
}


def _glob_first(patterns) -> str:
    import glob
    for pat in patterns:
        pat = os.path.expandvars(pat)
        if "%" in pat:
            continue
        hits = sorted(glob.glob(pat), reverse=True)
        if hits:
            return hits[0]
    return ""


def _find_python() -> list:
    """The PC's own Python (py launcher / python on PATH), not Kaka's venv."""
    py = shutil.which("py")
    if py:
        return [py, "-3"]
    venv = str(Path(sys.executable).parent).lower()
    for name in ("python", "python3"):
        hit = shutil.which(name)
        if hit and "windowsapps" not in hit.lower() and not hit.lower().startswith(venv):
            return [hit]
    hit = _glob_first([r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe",
                       r"%ProgramFiles%\Python3*\python.exe"])
    if hit:
        return [hit]
    base = getattr(sys, "_base_executable", "") or sys.executable
    if base and os.path.basename(base).lower().startswith("pythonw"):
        alt = os.path.join(os.path.dirname(base), "python.exe")
        base = alt if os.path.isfile(alt) else base
    return [base] if base else []


def _find_node() -> str:
    return shutil.which("node") or _glob_first([r"%ProgramFiles%\nodejs\node.exe",
                                                r"%LOCALAPPDATA%\Programs\nodejs\node.exe"])


def _find_java() -> tuple:
    javac = shutil.which("javac")
    if not javac:
        jh = os.environ.get("JAVA_HOME", "")
        cand = os.path.join(jh, "bin", "javac.exe") if jh else ""
        javac = cand if cand and os.path.isfile(cand) else _glob_first([
            r"%ProgramFiles%\Microsoft\jdk-*\bin\javac.exe",
            r"%ProgramFiles%\Eclipse Adoptium\jdk-*\bin\javac.exe",
            r"%ProgramFiles%\Java\jdk*\bin\javac.exe"])
    if not javac:
        return "", ""
    java = os.path.join(os.path.dirname(javac), "java.exe" if os.name == "nt" else "java")
    return javac, (java if os.path.isfile(java) else (shutil.which("java") or ""))


def _find_cc(cpp: bool) -> str:
    names = ("g++", "clang++") if cpp else ("gcc", "clang", "tcc")
    for n in names:
        hit = shutil.which(n)
        if hit:
            return hit
    exe = "g++.exe" if cpp else "gcc.exe"
    return _glob_first([
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\BrechtSanders.WinLibs*\mingw64\bin" + "\\" + exe,
        r"C:\msys64\ucrt64\bin" + "\\" + exe, r"C:\msys64\mingw64\bin" + "\\" + exe,
        r"C:\MinGW\bin" + "\\" + exe, r"%ProgramFiles%\mingw64\bin" + "\\" + exe])


def _missing(tool: str) -> str:
    name, wid = TOOLCHAINS[tool]
    return (f"status=failed. {name} is not installed on this PC, so the file was saved "
            f"but NOT run. Tell the user exactly that in Hinglish and ASK: "
            f"\"{name} install kar doon? (winget install {wid}, free)\". Only if he "
            f"says yes, call code_helper action=install_tool tool={tool}.")


def _q(s: str) -> str:
    return '"' + str(s).replace('"', '') + '"'


def _run_visible(path: Path, args: list) -> str:
    """Run the program in a normal terminal window he can see (it stays open)."""
    ext = path.suffix.lower()
    a = " ".join(_q(x) for x in (args or []))
    lines = ["@echo off", "chcp 65001 >nul", f"cd /d {_q(path.parent)}",
             f"echo ===== Kaka: running {path.name} =====", "echo."]
    if ext == ".py":
        py = _find_python()
        if not py:
            return _missing("python")
        lines.append(" ".join(_q(x) if i == 0 else x for i, x in enumerate(py)) + f" {_q(path.name)} {a}")
    elif ext == ".js":
        node = _find_node()
        if not node:
            return _missing("node")
        lines.append(f"{_q(node)} {_q(path.name)} {a}")
    elif ext == ".java":
        javac, java = _find_java()
        if not javac or not java:
            return _missing("java")
        lines.append(f"{_q(javac)} {_q(path.name)} && {_q(java)} -cp . {path.stem} {a}")
    elif ext in (".c", ".cpp", ".cc"):
        cc = _find_cc(ext != ".c")
        if not cc:
            return _missing("gcc")
        exe = path.stem + ".exe"
        lines.append(f"{_q(cc)} {_q(path.name)} -o {_q(exe)} && {_q(exe)} {a}")
    else:
        return f"status=failed. I cannot run {ext} files."
    lines += ["echo.", "echo ===== done - press any key to close =====", "pause >nul"]
    bat = path.parent / f"_kaka_run_{path.stem}.bat"
    bat.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(bat))          # a visible console window
        else:
            subprocess.Popen(["bash", str(bat)])
    except Exception as e:
        return f"status=failed. Could not open a terminal: {e}"
    return (f"status=started. {path.name} is running in a terminal window on screen "
            f"(it compiles first if needed). Tell him to look at that window.")


def _install_tool(tool: str, player=None) -> str:
    tool = (tool or "").strip().lower()
    tool = {"nodejs": "node", "node.js": "node", "jdk": "java", "javac": "java",
            "g++": "gcc", "c": "gcc", "cpp": "gcc", "c++": "gcc", "mingw": "gcc",
            "py": "python"}.get(tool, tool)
    if tool not in TOOLCHAINS:
        return f"status=failed. Unknown tool '{tool}'. Use python, node, java or gcc."
    name, wid = TOOLCHAINS[tool]
    wg = shutil.which("winget")
    if not wg:
        return "status=failed. winget is not on this PC, so nothing was installed."
    # update 13: he already said yes to the ONE install question, so no second
    # CONFIRM button on screen - the install runs now, in the background.
    def _run():
        r = subprocess.run([wg, "install", "--id", wid, "-e", "--silent",
                            "--accept-source-agreements", "--accept-package-agreements"],
                           capture_output=True, text=True, timeout=1200)
        msg = f"{name} installed." if r.returncode == 0 else f"{name} install failed (winget {r.returncode})."
        if player:
            try:
                player.write_log(f"Kaka: {msg}")
            except Exception:
                pass
        return msg

    def _bg():
        try:
            msg = _run()
        except Exception as e:
            msg = f"{name} install failed: {e}"
        if player and hasattr(player, "request_say"):
            try:
                player.request_say("Tell the user in ONE short Hinglish line what this "
                                   "says, no question: " + msg)
            except Exception:
                pass
    threading.Thread(target=_bg, daemon=True, name=f"install-{tool}").start()
    return (f"status=started. Installing {name} with winget now (he said yes). Say "
            f"only that it is installing (1-5 min) - ask nothing; the result follows.")


def _fix_code(code: str, error_output: str, description: str) -> str:
    model  = _get_gemini()
    prompt = f"""You are an expert debugger.
The code below failed with the following error. Fix it.
Return ONLY the corrected code — no explanation, no markdown, no backticks.

Original goal: {description}

Error:
{error_output[:2000]}

Broken code:
{code}

Fixed code:"""

    response = model.generate_content(prompt)
    return _clean_code(response.text)


def _run_file(path: Path, args: list, timeout: int) -> str:
    interpreters = {
        ".py":  [sys.executable],
        ".js":  ["node"],
        ".ts":  ["ts-node"],
        ".sh":  ["bash"],
        ".ps1": ["powershell", "-File"],
        ".rb":  ["ruby"],
        ".php": ["php"],
    }
    if path.suffix.lower() in (".c", ".cpp"):
        return _run_c(path, args, timeout)
    interp = interpreters.get(path.suffix.lower())
    if not interp:
        return f"No interpreter for {path.suffix}."

    try:
        result = subprocess.run(
            interp + [str(path)] + (args or []),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, cwd=str(path.parent)
        )
        output = result.stdout.strip()
        error  = result.stderr.strip()
        parts  = []
        if output: parts.append(f"Output:\n{output}")
        if error:  parts.append(f"Stderr:\n{error}")
        return "\n\n".join(parts) if parts else "Executed with no output."

    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s."
    except FileNotFoundError:
        return f"Interpreter not found: {interp[0]}."
    except Exception as e:
        return f"Execution error: {e}"


def _run_c(path: Path, args: list, timeout: int) -> str:
    """Compile + run C/C++ only if a compiler exists. MSVC (VS Build Tools) was
    removed by Kaka Update-5, so usually there is none - say that plainly."""
    cc = _find_cc(path.suffix.lower() != ".c")
    if not cc:
        return ("No C compiler on this PC (no gcc/clang; Visual Studio Build Tools "
                "was removed in Update-5), so the program was saved but NOT run. "
                "Tell the user plainly; to run C he needs a compiler like MinGW gcc - "
                "ask before installing anything.")
    exe = path.with_suffix(".exe" if os.name == "nt" else "")
    try:
        r = subprocess.run([cc, str(path), "-o", str(exe)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=max(30, timeout),
                           cwd=str(path.parent))
    except Exception as e:
        return f"Compile error: {e}"
    if r.returncode != 0:
        return f"Stderr:\nCompile failed:\n{(r.stderr or r.stdout).strip()[:1500]}"
    try:
        r = subprocess.run([str(exe)] + (args or []), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=str(path.parent), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s."
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    parts = []
    if out: parts.append(f"Output:\n{out}")
    if err: parts.append(f"Stderr:\n{err}")
    return "\n\n".join(parts) if parts else "Executed with no output."


def _build(description, language, output_path, args, timeout, speak=None, player=None) -> str:
    if not description:
        return "Please describe what you want me to build, sir."

    if player:
        player.write_log("[Code] Build started...")

    lang = language or "python"

    try:
        code, path = _write(description, lang, output_path, player)
        print(f"[Code] ✅ Written: {path}")
    except Exception as e:
        msg = f"Could not write initial code: {e}"
        if speak: speak(msg)
        return msg

    last_output = ""
    for attempt in range(1, MAX_BUILD_ATTEMPTS + 1):
        print(f"[Code] 🔄 Attempt {attempt}/{MAX_BUILD_ATTEMPTS}")
        if player:
            player.write_log(f"[Code] Attempt {attempt}...")

        last_output = _run_file(path, args, timeout)

        if not _has_error(last_output):
            msg = (
                f"Build complete, sir. "
                f"The code is working after {attempt} attempt{'s' if attempt > 1 else ''}. "
                f"Saved to {path}."
            )
            if speak: speak(msg)
            return f"{msg}\n\nOutput:\n{last_output}"

        print(f"[Code] ⚠️ Error on attempt {attempt}, fixing...")
        if player:
            player.write_log(f"[Code] Fixing (attempt {attempt})...")

        try:
            code = _fix_code(code, last_output, description)
            _save_file(path, code)
        except Exception as e:
            msg = f"Could not fix code on attempt {attempt}: {e}"
            if speak: speak(msg)
            return msg

    msg = (
        f"I was unable to build a working version after {MAX_BUILD_ATTEMPTS} attempts, sir. "
        f"The last error was: {last_output[:200]}"
    )
    if speak: speak(msg)
    return f"{msg}\n\nLast code saved to: {path}"

def _write_action(description, language, output_path, player, open_editor=True) -> str:
    if not description:
        return "Please describe what you want me to write, sir."
    if player:
        player.write_log("[Code] Writing code...")
    try:
        code, path = _write(description, language, output_path, player)
        print(f"[Code] ✅ Written: {path}")
        editor = _open_editor(path) if open_editor else ""
        if player:
            player.write_log(f"[Code] Saved: {path}" + (f" - opened in {editor}" if editor else ""))
        opened = (f" Opened it in {editor}." if editor else
                  (" (not opened in an editor)" if not open_editor else
                   " Could not open an editor."))
        return (f"status=done. Code written and saved to: {path}.{opened} "
                f"Tell the user in one short Hinglish line where it is saved "
                f"(file name + 'Kaka Saved > Code' folder) and that it is open.\n\n"
                f"Preview:\n{_preview(code)}")
    except Exception as e:
        return f"Could not generate code: {e}"


def _edit_action(file_path, instruction, player) -> str:
    if not file_path:
        return "Please provide a file path to edit, sir."
    if not instruction:
        return "Please describe what change to make, sir."

    content, err = _read_file(file_path)
    if err:
        return err

    if player:
        player.write_log("[Code] Editing file...")

    model  = _get_gemini()
    prompt = f"""You are an expert code editor.
Apply the following change to the code below.
Return ONLY the complete updated code — no explanation, no markdown, no backticks.

Change: {instruction}

Original code:
{content}

Updated code:"""

    try:
        response = model.generate_content(prompt)
        edited   = _clean_code(response.text)
    except Exception as e:
        return f"Could not edit code: {e}"

    status = _save_file(Path(file_path), edited)
    print(f"[Code] ✅ Edited: {file_path}")
    return f"File edited. {status}\n\nPreview:\n{_preview(edited)}"


def _explain_action(file_path, code, player) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to explain, sir."

    if player:
        player.write_log("[Code] Analyzing code...")

    model  = _get_gemini()
    prompt = f"""Explain what this code does in simple, clear language.
Focus on: what it does, how it works, and any important details.
Be concise — 3 to 6 sentences maximum.

Code:
{code[:4000]}

Explanation:"""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Could not explain code: {e}"


def _run_action(file_path, args, timeout, player) -> str:
    if not file_path:
        return "Please provide a file path to run, sir."
    p = Path(file_path)
    if not p.exists():
        return f"File not found: {file_path}"
    if player:
        player.write_log(f"[Code] Running {p.name}...")
    return _run_file(p, args, timeout)


def _optimize_action(file_path, code, language, output_path, player) -> str:

    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to optimize, sir."

    if player:
        player.write_log("[Code] Optimizing code...")

    lang  = language or "python"
    model = _get_gemini()

    prompt = f"""You are an expert {lang} developer and code reviewer.
Optimize the following code for:
1. Performance — eliminate unnecessary operations, use efficient data structures
2. Readability — clear variable names, proper formatting, logical structure
3. Best practices — modern {lang} patterns, error handling, type hints if applicable
4. Remove dead code, redundant comments, and unnecessary complexity

Return ONLY the optimized code — no explanation, no markdown, no backticks.

Original code:
{code[:6000]}

Optimized code:"""

    try:
        response  = model.generate_content(prompt)
        optimized = _clean_code(response.text)
    except Exception as e:
        return f"Could not optimize code: {e}"

    # Kaydet
    if file_path:
        save_path = Path(file_path)
    else:
        save_path = _resolve_save_path(output_path, lang)

    status = _save_file(save_path, optimized)
    print(f"[Code] ✅ Optimized: {save_path}")

    original_lines  = len(code.splitlines())
    optimized_lines = len(optimized.splitlines())
    diff = original_lines - optimized_lines

    return (
        f"Code optimized. {status}\n"
        f"Lines: {original_lines} → {optimized_lines} "
        f"({'−' if diff > 0 else '+'}{abs(diff)} lines)\n\n"
        f"Preview:\n{_preview(optimized)}"
    )


def _screen_debug_action(description, file_path, player, speak=None) -> str:

    if player:
        player.write_log("[Code] Taking screenshot for analysis...")

    print("[Code] 📸 Capturing screen for debug...")


    screenshot_path = _take_screenshot()
    if not screenshot_path:
        return "Could not take screenshot, sir. Please make sure PyAutoGUI is installed."


    file_content = ""
    if file_path:
        file_content, err = _read_file(file_path)
        if err:
            print(f"[Code] ⚠️ Could not read file: {err}")

    try:
        from google.genai import types

        image_bytes  = screenshot_path.read_bytes()
        image_base64 = _image_to_base64(screenshot_path)

        user_question = description or "What error or problem do you see on the screen? How can it be fixed?"

        context = ""
        if file_content:
            context = f"\n\nAdditionally, here is the related file content:\n```\n{file_content[:4000]}\n```"

        analysis_prompt = f"""You are an expert programmer and debugger analyzing a screenshot.

User's question: {user_question}{context}

Please:
1. Identify any errors, exceptions, or problems visible on the screen
2. Explain what is causing the problem in simple terms
3. Provide a concrete fix or solution
4. If there's code visible, show the corrected version

Be specific and actionable. If you see an error message, quote it exactly."""

        contents = [
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            analysis_prompt,
        ]

        response = gemini.call(contents, tier=gemini.SMART, timeout_ms=45_000)
        if response is None:
            return "Sir, I couldn't reach Gemini to analyse that screenshot."

        analysis = (response.text or "").strip()
        print(f"[Code] ✅ Screen analysis complete")

        try:
            screenshot_path.unlink()
        except Exception:
            pass

        if file_path and file_content:

            code_match = re.search(r"```[a-zA-Z]*\n(.*?)```", analysis, re.DOTALL)
            if code_match:
                fixed_code = code_match.group(1).strip()
                save_path  = Path(file_path)
                _save_file(save_path, fixed_code)
                analysis += f"\n\n✅ Fixed code has been saved to: {file_path}"
                print(f"[Code] ✅ Fixed code saved: {file_path}")

        return analysis

    except Exception as e:

        try:
            screenshot_path.unlink()
        except Exception:
            pass
        return f"Screen analysis failed: {e}"


def code_helper(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str:
    """
    Called from main.py.

    parameters:
        action      : write | edit | explain | run | build | screen_debug | optimize | auto
        description : What the code should do / what change to make / what problem to analyze
        language    : Programming language (default: python)
        output_path : Where to save — user specifies full path or filename
        file_path   : Path to existing file (edit / explain / run / build / optimize)
        code        : Raw code string (explain/optimize without a file)
        args        : CLI argument list for run/build
        timeout     : Execution timeout in seconds (default: 30)
    """
    p           = parameters or {}
    action      = p.get("action", "auto").lower().strip()
    description = p.get("description", "").strip()
    language    = p.get("language", "python").strip()
    output_path = p.get("output_path", "").strip()
    file_path   = p.get("file_path", "").strip()
    code        = p.get("code", "").strip()
    args        = p.get("args", [])
    timeout     = int(p.get("timeout", 30))

    if action == "auto":
        action = _detect_intent(description, file_path, code)
        print(f"[Code] 🤖 Auto-detected: {action}")

    open_editor = str(p.get("open_editor", "true")).lower() not in ("0", "false", "no")
    if action == "write":
        return _write_action(description, language, output_path, player, open_editor)

    elif action == "edit":
        return _edit_action(
            file_path,
            description or p.get("instruction", ""),
            player
        )

    elif action == "explain":
        return _explain_action(file_path, code, player)

    elif action == "run":
        fp = Path(file_path) if file_path else None
        if fp is not None and not fp.is_absolute():
            fp = _kaka_saved("Code") / fp
        if fp is not None and fp.suffix.lower() in (".py", ".js", ".java", ".c", ".cpp", ".cc"):
            if not fp.exists():
                return f"status=failed. File not found: {fp}"
            if isinstance(args, str):
                args = args.split()
            return _run_visible(fp, args)
        return _run_action(file_path, args, timeout, player)

    elif action == "install_tool":
        return _install_tool(p.get("tool", "") or language, player)

    elif action == "build":
        return _build(description, language, output_path, args, timeout, speak, player)

    elif action == "optimize":
        return _optimize_action(file_path, code, language, output_path, player)

    elif action == "screen_debug":
        return _screen_debug_action(description, file_path, player, speak)

    else:
        return f"Unknown action: '{action}'. Use write, edit, explain, run, build, optimize, or screen_debug."


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "code_helper",
    "description": (
        "Writes, edits, explains, runs, or builds a single code file. "
        "For the file currently OPEN IN VS CODE ('ye file run karo', 'error fix karo', "
        "'naya project banao') use vscode_helper instead. "
        "action=write generates the code, SAVES it as a real file (e.g. "
        "hello_world.c in Kaka's Saved > Code folder) and OPENS it in VS Code "
        "(Notepad if VS Code is missing) - so 'VS Code kholo aur C program likho', "
        "'C mein code likh do', 'ek python script bana do' is ONE call: "
        "action=write, language=c (or python...), description=what it should do. "
        "Do not call open_app first and do not ask for specifications on a clear "
        "request - pick a sensible simple program and write it. "
        "Do not run or compile unless the user asks (action=run with file_path). "
        "Multi-file projects are dev_agent; web pages are not this tool."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "write | edit | explain | run | build | install_tool | auto (default: auto). run opens a visible terminal and runs the file (compiles C/C++/Java first). install_tool only after the user said yes."
            },
            "description": {
                "type": "STRING",
                "description": "What the code should do or what change to make"
            },
            "language": {
                "type": "STRING",
                "description": "Programming language: c, cpp, python, javascript, java, html... (default: python). 'C code' -> c"
            },
            "tool": {
                "type": "STRING",
                "description": "install_tool only: python | node | java | gcc"
            },
            "open_editor": {
                "type": "BOOLEAN",
                "description": "write only: open the saved file in VS Code (default true)"
            },
            "output_path": {
                "type": "STRING",
                "description": "Where to save the file"
            },
            "file_path": {
                "type": "STRING",
                "description": "Path to existing file for edit/explain/run/build"
            },
            "code": {
                "type": "STRING",
                "description": "Raw code string for explain"
            },
            "args": {
                "type": "STRING",
                "description": "CLI arguments for run/build"
            },
            "timeout": {
                "type": "INTEGER",
                "description": "Execution timeout in seconds (default: 30)"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": code_helper,
}
