#!/usr/bin/env python3
"""Static safety audit for the Windows launcher files.

The Windows .bat/.cmd files in this repository cannot be executed by the CI or
the Linux preview environment; this audit is the enforceable proxy. It fails
(exit 1) when any launcher-critical invariant regresses:

  1. pure ASCII (cmd.exe decodes batch files in the OEM codepage; non-ASCII
     bytes can land inside strings/labels and corrupt parsing silently)
  2. CRLF line endings, no bare LF (cmd misreads line boundaries otherwise)
  3. every `goto :LABEL` / `goto LABEL` / `call :LABEL` target is defined
     (cmd prints "The system cannot find the batch label specified" and EXITS
     the script with no pause - a silent disappearance)
  4. no label defined twice (a second definition shadows the first)
  5. every failure-style label path ends in `pause` (via :STARTUP_FAILED or
     directly) - the window can never close without a keypress
  6. no `exit` without `/b` outside the monitor-loss path (plain exit kills
     the whole console hosting the launcher)
  7. no powershell pipelines / Tee-Object (external dependency + exit-code
     semantics lost - the v10.4 class of bugs)
  8. file ends with a newline; @echo off present

Usage: python scripts/audit_launcher.py [files...]   (default: the four launchers)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_FILES = ["START.bat", "START_DIAGNOSTIC.bat",
                 "scripts/START_INTERNAL.bat", "PREREQUISITES.bat",
                 "scripts/run_backend.cmd", "scripts/run_frontend.cmd"]

FAILURE_HINTS = ("FAIL", "FOREIGN", "TIMEOUT", "NOT_OURS", "NO_VENV", "MISSING",
                 "NOT_READY", "ABORT")
FAILURE_EXIT_LABELS = {"STARTUP_FAILED", "STAGE_FAILED", "DIAG_END"}

def audit(path: Path) -> list[str]:
    errors: list[str] = []
    raw = path.read_bytes()
    name = path.name

    # 1) ASCII
    non_ascii = [b for b in raw if b > 127]
    if non_ascii:
        errors.append(f"{name}: {len(non_ascii)} non-ASCII byte(s) (codepage corruption risk)")

    # 2) CRLF, no bare LF
    bare_lf = raw.count(b"\n") - raw.count(b"\r\n")
    if bare_lf:
        errors.append(f"{name}: {bare_lf} bare LF line ending(s) (cmd line-boundary corruption)")

    text = raw.decode("ascii", errors="replace")
    lines = text.splitlines()

    # 3) labels defined + goto targets resolve
    label_defs = [m.group(1) for m in re.finditer(r"(?im)^\s*:([A-Za-z_][\w]*)\b", text)]
    defined = set(label_defs)
    dup = {x for x in defined if label_defs.count(x) > 1}
    if dup:
        errors.append(f"{name}: duplicate label(s): {sorted(dup)}")
    targets = set(re.findall(r"(?i)\bgoto\s+:?([A-Za-z_][\w]*)", text))
    targets |= set(re.findall(r"(?i)\bcall\s+:([A-Za-z_][\w]*)", text))
    for t in sorted(targets):
        if t.lower() not in {d.lower() for d in defined}:
            errors.append(f"{name}: goto/call target not defined: {t}")
    # 'goto :EOF' is a cmd builtin, allow it
    targets.discard("EOF")

    # 5) failure labels must reach pause
    #    every failure label either contains 'pause' before the next label, or goto's :STARTUP_FAILED
    failure_labels = [l for l in defined if any(h in l.upper() for h in FAILURE_HINTS)]
    for m in re.finditer(r"(?im)^\s*:([A-Za-z_][\w]*)\b", text):
        label = m.group(1)
        if not any(h in label.upper() for h in FAILURE_HINTS):
            continue
        body = text[m.end():]
        nxt = re.search(r"(?im)^\s*:([A-Za-z_][\w]*)\b", body)
        body = body[:nxt.start()] if nxt else body
        routes = any(re.search(r"(?i)\bgoto\s+:(" + h + r")\b", body) for h in FAILURE_EXIT_LABELS)
        if "pause" not in body.lower() and not routes:
            errors.append(f"{name}: failure label '{label}' neither pauses nor routes to {sorted(FAILURE_EXIT_LABELS)}")
    if "STARTUP_FAILED" in defined:
        body = text[text.index(":STARTUP_FAILED"):]
        if "pause" not in body.lower():
            errors.append(f"{name}: :STARTUP_FAILED does not pause - window would close silently")
    if "STAGE_FAILED" in defined:
        body = text[text.index(":STAGE_FAILED"):]
        if "pause" not in body.lower():
            errors.append(f"{name}: :STAGE_FAILED does not pause - window would close silently")

    # 6) plain 'exit' without /b
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if re.match(r"(?i)^exit\b", s) and not re.match(r"(?i)^exit\s+/b\b", s):
            errors.append(f"{name}:L{i}: plain 'exit' without /b -> {s}")

    # 7) no powershell pipelines / Tee-Object
    for i, line in enumerate(lines, 1):
        s = line.lower()
        if "tee-object" in s or ("powershell" in s and not s.strip().startswith("rem")):
            errors.append(f"{name}:L{i}: powershell/tee dependency -> {line.strip()}")

    # 8) trailing newline + @echo off in .bat
    if not raw.endswith(b"\n"):
        errors.append(f"{name}: missing trailing newline")
    if path.suffix.lower() == ".bat" and not re.search(r"(?im)^@echo\s+off\b", text):
        errors.append(f"{name}: missing '@echo off'")

    # 9) architecture-specific contracts
    if name == "START.bat":  # outer shell must host the internal procedure in a persistent session
        if not re.search(r"(?i)cmd\s+/k\s+call\s+[^\r\n]*START_INTERNAL\.bat", text):
            errors.append("START.bat: must launch scripts\\START_INTERNAL.bat via 'cmd /k call' - the persistent-session guarantee is gone")
        if "pause" not in text.lower():
            errors.append("START.bat: must end the outer session with pause")
        if "certutil" not in text.lower():
            errors.append("START.bat: must print its own SHA-256 (certutil) so the executed file is identifiable")
    if name == "START_DIAGNOSTIC.bat":
        if "PRAGMA integrity_check" not in text:
            errors.append("START_DIAGNOSTIC.bat: missing read-only PRAGMA integrity_check")
        if re.search(r"(?i)init_db\.py\s+--create", text):
            errors.append("START_DIAGNOSTIC.bat: must never create/migrate databases")
        if not text.lower().rstrip().endswith("exit /b 0"):
            errors.append("START_DIAGNOSTIC.bat: must end with 'exit /b 0' after its pause")
        if "pause" not in text.lower():
            errors.append("START_DIAGNOSTIC.bat: must keep the window open with pause")
    if name == "START_INTERNAL.bat":
        for i in range(1, 11):
            if f"[STAGE {i}/10]" not in text:
                errors.append(f"START_INTERNAL.bat: missing [STAGE {i}/10] marker")
        if ":STAGE_FAILED" not in text:
            errors.append("START_INTERNAL.bat: missing :STAGE_FAILED handler")
        if "exit /b 1" not in text:
            errors.append("START_INTERNAL.bat: failure path must exit /b 1")
        # stage 10 browser auto-open: the launch rc must be captured BEFORE any
        # PASS is claimed, and a failed auto-open must warn with a manual-open
        # URL - it must never route to :STAGE_FAILED (the services are healthy;
        # only the browser convenience failed)
        i_url = text.find('set "DASHBOARD_URL=http://127.0.0.1:5173/')
        i_start = text.find('start "" "%DASHBOARD_URL%"')
        i_pass10 = text.find("[PASS] stage 10")
        if i_url < 0 or i_start < 0 or i_url > i_start:
            errors.append("START_INTERNAL.bat: stage 10 dashboard URL must be assigned before browser auto-open")
        if "FRONTEND_REUSE" not in text or "duplicate tab" not in text:
            errors.append("START_INTERNAL.bat: stage 10 must avoid duplicate tabs when reusing the frontend")
        if "rundll32 url.dll,FileProtocolHandler" not in text:
            errors.append("START_INTERNAL.bat: stage 10 must try the Windows URL-handler fallback")
        if i_start < 0 or i_pass10 < 0 or i_start > i_pass10:
            errors.append("START_INTERNAL.bat: stage 10 browser auto-open missing or misplaced")
        else:
            i_rc = text.find('set "RC=%errorlevel%"', i_start)
            if not (i_start < i_rc < i_pass10):
                errors.append("START_INTERNAL.bat: stage 10 must capture the browser-launch rc before claiming PASS")
            if "goto :STAGE_FAILED" in text[i_start:i_pass10]:
                errors.append("START_INTERNAL.bat: browser auto-open failure must warn, never fail startup")
            if "manually" not in text[i_start:i_pass10]:
                errors.append("START_INTERNAL.bat: stage 10 must print the manual-open URL when auto-open fails")
        handler_at = text.find("\r\n:STAGE_FAILED")
        if handler_at >= 0 and "TROUBLESHOOTING.txt" not in text[handler_at:]:
            errors.append("START_INTERNAL.bat: :STAGE_FAILED must point to TROUBLESHOOTING.txt (search-first)")

    return errors


def main() -> int:
    args = sys.argv[1:]
    files = [REPO / a for a in args] if args else [REPO / f for f in DEFAULT_FILES]
    total = 0
    for f in files:
        if not f.exists():
            print(f"MISSING: {f}")
            total += 1
            continue
        errs = audit(f)
        status = "OK " if not errs else "FAIL"
        print(f"[{status}] {f.relative_to(REPO)}")
        for e in errs:
            print(f"       - {e}")
        total += len(errs)
    print(f"\n{'launcher audit: PASS' if total == 0 else f'launcher audit: {total} problem(s)'}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
