"""Габарит из КОМПАС: разбор ответа, заготовка по габариту, обход сбоев COM."""
from core import m3d_stock as ms
from integrations.kompas import batch as kb


def test_gabarit_parse_with_flag_and_nested():
    assert kb.dims_from_gabarit((True, 0, 0, 0, 120, 5, 230)) == (120.0, 5.0, 230.0)
    assert kb.dims_from_gabarit(((0, 0, 0), (30, 40, 50))) == (30.0, 40.0, 50.0)
    assert kb.dims_from_gabarit((1, 2)) is None
    assert kb.dims_from_gabarit((0, 0, 0, 0, 5, 5)) is None


class _Part:
    def __init__(self, ok_on):
        self.ok_on = ok_on

    def GetGabarit(self, *a):
        key = a[0] if a else "none"
        if key != self.ok_on:
            raise RuntimeError("нет такого вызова")
        return (0.0, 0.0, 0.0, 120.0, 5.0, 230.0)


def test_read_body_dims_tries_variants():
    att = []
    assert kb.read_body_dims(_Part(False), att) == (120.0, 5.0, 230.0)
    assert any("успех" in a for a in att) and any("ошибка" in a for a in att)
    assert kb.read_body_dims(_Part("никогда"), []) is None


class _Doc:
    def __init__(self, part):
        self.TopPart = part
        self.closed = False

    def Close(self, *_):
        self.closed = True


class _Docs:
    def __init__(self, doc):
        self.doc = doc

    def Open(self, *a):
        return self.doc


class _App:
    def __init__(self, doc):
        self.Documents = _Docs(doc)


def test_measure_reads_and_always_closes():
    doc = _Doc(_Part(True))
    r = kb.measure(_App(doc), "x.m3d")
    assert r.dims == (120.0, 5.0, 230.0) and doc.closed
    doc2 = _Doc(_Part("никогда"))
    r2 = kb.measure(_App(doc2), "y.m3d")
    assert r2.dims is None and r2.error and doc2.closed


def test_stock_from_body_sheet_and_linear():
    st = ms.stock_from_body((120, 5, 230), "Лист$d5 ГОСТ 19903-2015;Ст3пс ГОСТ 14637-89$", 0.972, 7850)
    assert (st.h_mm, st.l_mm) == (120.0, 230.0) and st.method == "габарит КОМПАС"
    assert abs(st.blank_mass_kg - 1.08) < 0.01
    # толщины в габарите нет (гнутая деталь): не угадываем
    assert ms.stock_from_body((120, 60, 230), "Лист$d5 ГОСТ 19903-2015;Ст3пс ГОСТ 14637-89$", 0.972, 7850) is None
    ln = ms.stock_from_body((159, 1970, 159), "Труба 159x7 ГОСТ 8732-78;Ст20", 40, 7850)
    assert ln.l_mm == 1970.0
    # заготовка меньше детали по площади: отказ
    assert ms.stock_from_body((20, 5, 20), "Лист$d5 ГОСТ 19903-2015;Ст3пс ГОСТ 14637-89$", 0.972, 7850) is None


def test_measure_falls_back_to_step(monkeypatch):
    """Прямой габарит не отдан -> берётся габарит по STEP, документ всё равно закрывается."""
    monkeypatch.setattr(kb, "_dims_via_step", lambda app, doc, att: (36.0, 36.0, 95.0))
    doc = _Doc(_Part("никогда"))
    r = kb.measure(_App(doc), "z.m3d")
    assert r.dims == (36.0, 36.0, 95.0) and not r.error and doc.closed


def test_step_failure_is_reported_not_raised():
    doc = _Doc(_Part("никогда"))
    r = kb.measure(_App(doc), "w.m3d")          # у фиктивного документа нет SaveAs
    assert r.dims is None and r.error and doc.closed
    assert any(a.startswith("STEP:") for a in r.attempts)


def test_log_result_writes_file(tmp_path, monkeypatch):
    import core.paths as cp
    monkeypatch.setattr(cp, "USER_DATA_DIR", tmp_path)
    kb.log_result("C:/x/модель.m3d", kb.BodyResult(error="нет габарита", attempts=["a -> ошибка"]))
    text = (tmp_path / "kompas_batch.log").read_text(encoding="utf-8")
    assert "модель.m3d" in text and "нет габарита" in text and "a -> ошибка" in text
