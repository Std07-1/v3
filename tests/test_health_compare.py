"""compare_reports — регресійний гейт активації символу (ADR-0054 §3.4 п.2/п.5)."""
from __future__ import annotations

from core.health import compare_reports


def _tf(grade="GREEN", bars=1000, holes=0, age=0, dup=0, mismatched=0):
    return {
        "grade": grade,
        "reasons": [],
        "bars": bars,
        "age_buckets": age,
        "holes": {"missing": holes, "expected": 1440},
        "geometry": {"exact_dup": 0, "dup_conflicting": dup, "unsorted": 0,
                     "align_bad": 0, "close_bad": 0, "ohlc_bad": 0},
        "cascade": {"checked": 100, "mismatched": mismatched,
                    "declared_partial": 0, "skipped_incomplete": 0},
    }


def _report(symbols):
    return {"generated_at": "2026-09-09 17:00", "window_days": 1, "symbols": symbols}


def _sym(grade="GREEN", tfs=None):
    return {"symbol": "X", "grade": grade, "tfs": tfs or {"60": _tf()}}


def test_ідентичні_звіти_без_регресій():
    r = _report({"XAU/USD": _sym()})
    res = compare_reports(r, r)
    assert res.ok is True
    assert res.regressions == []
    assert res.compared_symbols == ["XAU/USD"]


def test_нових_дірок_більше_регресія():
    before = _report({"XAU/USD": _sym(tfs={"60": _tf(holes=2)})})
    after = _report({"XAU/USD": _sym(tfs={"60": _tf(holes=9)})})
    res = compare_reports(before, after)
    assert res.ok is False
    assert any(x.measure == "дірок" and x.before == 2 and x.after == 9 for x in res.regressions)


def test_менше_дірок_це_покращення_а_не_регресія():
    before = _report({"XAU/USD": _sym(tfs={"60": _tf(holes=9)})})
    after = _report({"XAU/USD": _sym(tfs={"60": _tf(holes=2)})})
    res = compare_reports(before, after)
    assert res.ok is True
    assert any(x.measure == "дірок" for x in res.improvements)


def test_втрата_барів_регресія():
    before = _report({"XAU/USD": _sym(tfs={"60": _tf(bars=1000)})})
    after = _report({"XAU/USD": _sym(tfs={"60": _tf(bars=940)})})
    res = compare_reports(before, after)
    assert res.ok is False
    assert any(x.measure == "барів" for x in res.regressions)


def test_приріст_барів_не_регресія():
    before = _report({"XAU/USD": _sym(tfs={"60": _tf(bars=1000)})})
    after = _report({"XAU/USD": _sym(tfs={"60": _tf(bars=1440)})})
    assert compare_reports(before, after).ok is True


def test_погіршення_вердикту_регресія():
    before = _report({"XAU/USD": _sym(grade="YELLOW", tfs={"60": _tf(grade="YELLOW")})})
    after = _report({"XAU/USD": _sym(grade="RED", tfs={"60": _tf(grade="RED")})})
    res = compare_reports(before, after)
    assert res.ok is False
    assert any(x.measure == "вердикт символу" for x in res.regressions)


def test_red_який_був_red_не_регресія():
    """Baseline у нас RED через відомі дублікати — це не має блокувати активацію."""
    before = _report({"XAU/USD": _sym(grade="RED", tfs={"60": _tf(grade="RED", dup=7)})})
    after = _report({"XAU/USD": _sym(grade="RED", tfs={"60": _tf(grade="RED", dup=7)})})
    assert compare_reports(before, after).ok is True


def test_новий_символ_ігнорується():
    before = _report({"XAU/USD": _sym()})
    after = _report({"XAU/USD": _sym(), "SPX500": _sym(tfs={"60": _tf(holes=500, bars=10)})})
    res = compare_reports(before, after)
    assert res.ok is True
    assert res.new_symbols == ["SPX500"]


def test_зниклий_символ_провал():
    before = _report({"XAU/USD": _sym(), "XAG/USD": _sym()})
    after = _report({"XAU/USD": _sym()})
    res = compare_reports(before, after)
    assert res.ok is False
    assert res.missing_symbols == ["XAG/USD"]


def test_gate_symbols_звужує_перевірку():
    before = _report({"XAU/USD": _sym(tfs={"60": _tf(holes=1)}),
                      "XAG/USD": _sym(tfs={"60": _tf(holes=1)})})
    after = _report({"XAU/USD": _sym(tfs={"60": _tf(holes=1)}),
                     "XAG/USD": _sym(tfs={"60": _tf(holes=99)})})
    assert compare_reports(before, after).ok is False
    assert compare_reports(before, after, only_symbols=["XAU/USD"]).ok is True


def test_зниклий_tf_регресія():
    before = _report({"XAU/USD": _sym(tfs={"60": _tf(), "300": _tf()})})
    after = _report({"XAU/USD": _sym(tfs={"60": _tf()})})
    res = compare_reports(before, after)
    assert res.ok is False
    assert any(x.tf == "300" for x in res.regressions)


def test_каскад_і_дублікати_ловляться():
    before = _report({"XAU/USD": _sym(tfs={"60": _tf(dup=1, mismatched=0)})})
    after = _report({"XAU/USD": _sym(tfs={"60": _tf(dup=4, mismatched=3)})})
    res = compare_reports(before, after)
    measures = {x.measure for x in res.regressions}
    assert "конфліктних дублікатів" in measures
    assert "мовчазних розбіжностей каскаду" in measures


def test_відсутній_вимір_не_падає():
    """cascade=None для M1 (немає source-TF) — не має ламати порівняння."""
    tf = _tf(); tf["cascade"] = None
    before = _report({"XAU/USD": _sym(tfs={"60": tf})})
    after = _report({"XAU/USD": _sym(tfs={"60": dict(tf)})})
    assert compare_reports(before, after).ok is True
