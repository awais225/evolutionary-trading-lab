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
DEFAULT_FILES = ["START.bat", "PREREQUISITES.bat",
                 "scripts/run_backend.cmd", "scripts/run_frontend.cmd"]

FAILURE_HINTS = ("FAIL", "FOREIGN", "TIMEOUT", "NOT_OURS", "NO_VENV", "MISSING",
                 "NOT_READY", "ABORT")

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
        if "pause" not in body.lower() and not re.search(r"(?i)\bgoto\s+:STARTUP_FAILED\b", body):
            errors.append(f"{name}: failure label '{label}' neither pauses nor routes to :STARTUP_FAILED")
    if ":STARTUP_FAILED" in defined or any(h in path.name for h in ()):  # only for START.bat
        pass
    if "STARTUP_FAILED" in defined:
        body = text[text.index(":STARTUP_FAILED"):]
        if "pause" not in body.lower():
            errors.append(f"{name}: :STARTUP_FAILED does not pause - window would close silently")

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
