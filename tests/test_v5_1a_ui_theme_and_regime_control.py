"""V5.1a §2/§4/§5 — the shared theme and the regime control, checked against the source.

These are *static* contracts between `frontend/src` and `frontend/src/styles.css`,
so they hold for every page and every future table without a browser:

1. Every class the JSX uses is either themed or explicitly dynamic — the
   generator (`frontend/tools/gen_theme_layer.py --check`) fails otherwise, so a
   new screen can never be added with unstyled class names.
2. One table theme: `table.tbl` (kit pages) and `table.table` / `.compact`
   (research + live pages) resolve to the same header, row, padding, hover and
   numeric-alignment rules.
3. The four MT5 screens carry a themed table, not a bare `<table>`.
4. No emoji icons were introduced on the screens this iteration touched.
5. The schedule editor's regime control is multi-select, labelled from the
   backend options, and states the empty-selection semantics.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SRC = FRONTEND / "src"
CSS = SRC / "styles.css"

TOUCHED = ["pages/LiveTesting.jsx", "pages/LiveTestResults.jsx",
           "pages/Mt5Backtest.jsx", "pages/Mt5DemoTrading.jsx"]


def _css() -> str:
    return CSS.read_text(encoding="utf-8")


# ===========================================================================
# 1. the generator proves the whole app is themed
# ===========================================================================
def test_01_every_class_used_in_the_frontend_is_mapped_or_explicitly_dynamic():
    tool = FRONTEND / "tools" / "gen_theme_layer.py"
    assert tool.exists(), "the utility-layer generator is missing"
    proc = subprocess.run([sys.executable, str(tool), "--check"], capture_output=True, text=True,
                          cwd=str(FRONTEND), timeout=120)
    assert proc.returncode == 0, (
        "some class names used by the frontend have no theme rule and are not marked as "
        f"dynamic:\n{proc.stdout}\n{proc.stderr}")
    assert "unmapped: 0" in proc.stdout, proc.stdout


def test_02_the_generator_is_idempotent_and_writes_the_same_bytes():
    """Re-running it must not churn the file: the layer is generated, not appended
    again on every edit."""
    tool = FRONTEND / "tools" / "gen_theme_layer.py"
    before = CSS.read_bytes()
    subprocess.run([sys.executable, str(tool)], capture_output=True, text=True,
                   cwd=str(FRONTEND), timeout=120, check=True)
    assert CSS.read_bytes() == before, "the generator is not idempotent"


def test_03_the_shared_table_theme_covers_both_class_names():
    css = _css()
    # one rule set naming both spellings
    joined = re.sub(r"\s+", " ", css)
    assert "table.tbl, table.table {" in joined
    assert "table.tbl thead th, table.table thead th {" in joined
    assert "table.tbl tbody td, table.table tbody td {" in joined
    # compact rows, numeric columns, hover and selected states exist for both
    for fragment in ("table.tbl.compact tbody td, table.table.compact tbody td",
                     "table.tbl td.num, table.table td.num",
                     "table.tbl tbody tr.selected td, table.table tbody tr.selected td",
                     "table.tbl tbody tr:hover td, table.table tbody tr:hover td"):
        assert fragment in joined, fragment


def test_04_the_component_classes_the_pages_use_are_defined():
    """`.input`, `.btn-sm`, `.btn-xs`, `.field`, `.table-wrap` … had no CSS at all
    before this iteration; each one must now resolve."""
    css = _css()
    for selector in (".input {", ".input:focus", "input.input", ".table-wrap {",
                     ".btn-sm", ".btn-xs", ".btn-subtle", ".field {", ".stack {",
                     ".row-bar {", ".spacer {", ".status-pill {", ".panel-title"):
        assert selector in css, f"{selector} is not themed"


# ===========================================================================
# 2/3. the four screens really use the shared table
# ===========================================================================
@pytest.mark.parametrize("page", TOUCHED)
def test_05_the_mt5_screens_use_a_themed_table(page):
    text = (SRC / page).read_text(encoding="utf-8")
    tables = re.findall(r"<table className=\"([^\"]*)\"", text)
    assert tables, f"{page} renders no table"
    for classes in tables:
        tokens = set(classes.split())
        assert tokens & {"table", "tbl"}, (
            f"{page} has an unthemed table (className={classes!r}): it would render without the "
            "V5 header/row/hover/numeric styling")


@pytest.mark.parametrize("page", TOUCHED)
def test_06_row_cells_do_not_reintroduce_ad_hoc_padding(page):
    """The theme owns table padding; a cell that sets its own would break the
    column alignment of the shared table."""
    text = (SRC / page).read_text(encoding="utf-8")
    for m in re.finditer(r"<t[hd] className=\"([^\"]*)\"", text):
        classes = m.group(1)
        assert not re.search(r"\b[pm][xy]?-\d", classes), (
            f"{page}: cell className={classes!r} hand-rolls spacing instead of using the theme")


@pytest.mark.parametrize("page", TOUCHED)
def test_07_no_pictographic_emoji_icons(page):
    """§2 — the existing icon system only. Pictographs (shield/gear/…) are out;
    the established text conventions already in the kit (★/☆ star toggle, ✓ in a
    status message) are not emoji and stay."""
    text = (SRC / page).read_text(encoding="utf-8")
    pictographs = [ch for ch in text if 0x1F300 <= ord(ch) <= 0x1FAFF or 0x2699 == ord(ch)]
    assert not pictographs, f"{page} contains emoji icons: {sorted(set(pictographs))}"


# ===========================================================================
# 4. the regime control in the schedule editor
# ===========================================================================
def test_08_the_regime_group_is_multi_select_from_backend_options():
    text = (SRC / "components" / "LiveTestingPanels.jsx").read_text(encoding="utf-8")
    m = re.search(r'group\("(Market regimes[^"]*)",\s*"regimes",\s*regimeOpts', text)
    assert m, "the regimes group is not rendered from the backend's options"
    assert "regimeOpts" in text and "options?.regimes" in text.replace("state?.options?.regimes",
                                                                       "options?.regimes")
    # the option shape () and the toggle helper make it a checkbox multi-select
    assert "toggleIn(key, v)" in text
    assert 'key === "timeframes"' in text          # the same helper serves every group


def test_09_the_empty_selection_is_explained_in_the_ui():
    """§6 — an empty selection keeps the existing meaning, and the editor says so
    instead of leaving the operator to guess."""
    text = (SRC / "components" / "LiveTestingPanels.jsx").read_text(encoding="utf-8")
    assert "No regime restriction" in text
    assert "never blocks every trade" in text
    assert "engine checks this rule before every order" in text


def test_10_the_regime_values_shown_are_the_engine_vocabulary():
    """The rendered option list is the schedule module's; the module's is the
    engine's. Nothing is hard-coded in the component."""
    text = (SRC / "components" / "LiveTestingPanels.jsx").read_text(encoding="utf-8")
    assert "options?.regimes" in text, "the component must read options.regimes"
    for invented in ('"trending", "ranging"' , "['trending'"):
        assert invented not in text, f"the component hard-codes regimes: {invented}"
