"""Launcher invariant tests - the enforceable proxy for Windows .bat safety.

The Windows launcher (START.bat) cannot be executed on Linux. These tests fail
the moment a launcher-critical invariant regresses (undefined goto label = the
cmd "The system cannot find the batch label specified" silent-exit class;
non-ASCII/bare-LF = codepage/line-boundary corruption; missing pause on failure
= window closes with no chance to read the diagnostic).
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("audit_launcher", REPO / "scripts" / "audit_launcher.py")
audit_launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_launcher)

LAUNCHERS = ["START.bat", "START_DIAGNOSTIC.bat",
             "scripts/START_INTERNAL.bat", "PREREQUISITES.bat",
             "scripts/run_backend.cmd", "scripts/run_frontend.cmd"]


def test_launchers_pass_static_audit():
    problems = []
    for rel in LAUNCHERS:
        problems += audit_launcher.audit(REPO / rel)
    assert not problems, "launcher audit failures:\n" + "\n".join(problems)


def test_internal_captures_rc_and_never_uses_errorlevel_keyword():
    import re
    text = (REPO / "scripts" / "START_INTERNAL.bat").read_text(encoding="ascii")
    # no successful or failure branch may rely on the errorlevel keyword compare;
    # only %errorlevel% / !errorlevel! expansions are allowed
    assert not re.search(r"(?i)if\s+errorlevel\b", text), (
        "bare 'if errorlevel' keyword remains - negative native-crash codes defeat it; use set RC=%errorlevel% + string compare"
    )
    # every external call site stores RC explicitly
    assert text.count("set \"RC=%errorlevel%\"") >= 9


def test_internal_db_check_is_transparent_and_pauses_on_failure():
    text = (REPO / "scripts" / "START_INTERNAL.bat").read_text(encoding="ascii")
    assert "type \"%DB_CHECK_OUT%\"" in text            # report printed to console
    assert "type \"%DB_CHECK_OUT%\" >> \"%STARTUP_LOG%\"" in text
    assert 'if not "%RC%"=="0"' in text
    assert ":STAGE_FAILED" in text
    stage_failed = text[text.index(":STAGE_FAILED"):]
    assert "pause" in stage_failed             # window can never close on failure
    assert "Exit code" in stage_failed         # the exact rc is shown
    assert "last 30 lines" in stage_failed     # log tail is printed


def test_outer_start_bat_is_a_persistent_shell():
    text = (REPO / "START.bat").read_text(encoding="ascii")
    assert "cmd /k call" in text               # internal runs inside a persistent session
    assert "START_INTERNAL.bat" in text
    assert "certutil" in text.lower()          # self SHA-256 printed for identification
    assert text.lower().count("pause") >= 2    # both the missing-file path and the final exit pause


def test_diagnostic_launcher_is_read_only_and_stays_open():
    text = (REPO / "START_DIAGNOSTIC.bat").read_text(encoding="ascii")
    assert "PRAGMA integrity_check" in text
    assert "--create" not in text              # never creates/migrates databases
    assert "diagnostic.log" in text
    assert text.lower().rstrip().endswith("exit /b 0")
    assert "pause" in text.lower()


def test_internal_marks_all_ten_stages_and_failure_stages():
    text = (REPO / "scripts" / "START_INTERNAL.bat").read_text(encoding="ascii")
    for i in range(1, 11):
        assert f"[STAGE {i}/10]" in text
        assert f'set "FAILED_STAGE={i}"' in text


def test_stage10_browser_autopen_captures_rc_warns_never_fails():
    text = (REPO / "scripts" / "START_INTERNAL.bat").read_text(encoding="ascii")
    stage10 = text[text.index("[STAGE 10/10]"):text.index(":MONITOR_LOOP")]
    assert 'start "" "http://127.0.0.1:5173/' in stage10          # the auto-open itself
    assert 'set "RC=%errorlevel%"' in stage10                     # rc captured...
    assert (stage10.index('start ""') < stage10.index('set "RC=%errorlevel%"')
            < stage10.index("[PASS] stage 10"))                   # ...before claiming PASS
    assert "manually" in stage10                                  # manual-open fallback printed
    assert "goto :STAGE_FAILED" not in stage10                    # browser failure never fails startup
    handler = text[text.index("\n:STAGE_FAILED"):]  # label definition at line start (goto targets are mid-line)
    assert "TROUBLESHOOTING.txt" in handler                       # search-first pointer on real failures


def test_troubleshooting_txt_is_search_first_and_verified_only():
    p = REPO / "TROUBLESHOOTING.txt"
    assert p.exists(), "TROUBLESHOOTING.txt knowledge base missing at repository root"
    raw = p.read_bytes()
    assert all(b < 128 for b in raw), "TROUBLESHOOTING.txt must stay pure ASCII (Windows consoles)"
    text = raw.decode("ascii")
    assert "SEARCH THIS FILE FIRST" in text
    entries = text.split("\nSYMPTOM:")[1:]
    assert len(entries) >= 8, "knowledge base too thin to be useful"
    for entry in entries:
        assert "Verified cause:" in entry, "entry without a verified cause (speculation is not allowed)"
        assert "Verified fix:" in entry, "entry without a verified fix"
        assert "Source:" in entry, "entry without a traceable verification source"


def test_internal_exports_data_root_and_checks_init_db_exists():
    text = (REPO / "scripts" / "START_INTERNAL.bat").read_text(encoding="ascii")
    assert 'if not defined HERO1_DATA_ROOT set "HERO1_DATA_ROOT=%DATA_ROOT%"' in text
    assert 'scripts\\init_db.py' in text
    assert 'scripts\\START_INTERNAL.bat' not in text   # never self-invokes
