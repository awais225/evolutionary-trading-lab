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

LAUNCHERS = ["START.bat", "PREREQUISITES.bat",
             "scripts/run_backend.cmd", "scripts/run_frontend.cmd"]


def test_launchers_pass_static_audit():
    problems = []
    for rel in LAUNCHERS:
        problems += audit_launcher.audit(REPO / rel)
    assert not problems, "launcher audit failures:\n" + "\n".join(problems)


def test_start_bat_captures_rc_and_never_uses_errorlevel_keyword():
    import re
    text = (REPO / "START.bat").read_text(encoding="ascii")
    # no successful or failure branch may rely on the errorlevel keyword compare;
    # only %errorlevel% / !errorlevel! expansions are allowed
    assert not re.search(r"(?i)if\s+errorlevel\b", text), (
        "bare 'if errorlevel' keyword remains - negative native-crash codes defeat it; use set RC=%errorlevel% + string compare"
    )
    # every external call site stores RC explicitly
    assert text.count("set \"RC=%errorlevel%\"") >= 10


def test_start_bat_db_check_is_transparent_and_pauses_on_failure():
    text = (REPO / "START.bat").read_text(encoding="ascii")
    assert "type \"%DB_CHECK_OUT%\"" in text            # report printed to console
    assert "type \"%DB_CHECK_OUT%\" >> \"%STARTUP_LOG%\"" in text
    assert 'if not "%RC%"=="0"' in text
    import re
    m = re.search(r"(?ms)^:DB_CHECK_FAIL\r?\n(.*?)(?=^:)", text)
    assert m, ":DB_CHECK_FAIL label not found"
    db_fail = m.group(1)
    assert "FAILED_RC" in db_fail and "goto :STARTUP_FAILED" in db_fail
    startup_failed = text[text.index(":STARTUP_FAILED"):]
    assert "pause" in startup_failed
    assert "Exit code: %FAILED_RC%" in startup_failed   # the exact rc is shown


def test_start_bat_exports_data_root_and_checks_init_db_exists():
    text = (REPO / "START.bat").read_text(encoding="ascii")
    assert 'if not defined HERO1_DATA_ROOT set "HERO1_DATA_ROOT=%DATA_ROOT%"' in text
    assert 'if not exist "%ROOT%\\scripts\\init_db.py"' in text
