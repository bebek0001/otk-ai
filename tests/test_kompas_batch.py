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
    assert kb.read_body_dims(_Part(True), att) == (120.0, 5.0, 230.0)
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


def test_log_result_writes_file(tmp_path, monkeypatch):
    import core.paths as cp
    monkeypatch.setattr(cp, "USER_DATA_DIR", tmp_path)
    kb.log_result("C:/x/модель.m3d", kb.BodyResult(error="нет габарита", attempts=["a -> ошибка"]))
    text = (tmp_path / "kompas_batch.log").read_text(encoding="utf-8")
    assert "модель.m3d" in text and "нет габарита" in text and "a -> ошибка" in text


def _fake_win32(monkeypatch, known):
    """Подмена pywin32: IID знает только перечисленные ProgID."""
    import sys
    import types
    pc = types.ModuleType("pythoncom")
    pc.CoInitialize = lambda: None
    pt = types.ModuleType("pywintypes")

    def IID(name):
        if name in known:
            return "{FAKE-CLSID}"
        raise RuntimeError("Недопустимая строка класса")
    pt.IID = IID
    wcl = types.ModuleType("win32com.client")

    def GetActiveObject(name):
        raise RuntimeError("Операция недоступна")
    wcl.GetActiveObject = GetActiveObject
    w32 = types.ModuleType("win32com")
    w32.client = wcl
    for n, m in (("pythoncom", pc), ("pywintypes", pt), ("win32com", w32), ("win32com.client", wcl)):
        monkeypatch.setitem(sys.modules, n, m)
    monkeypatch.setattr(kb, "is_available", lambda: True)


def test_detect_finds_registered_kompas_even_when_not_running(monkeypatch):
    _fake_win32(monkeypatch, {"KOMPAS.Application.7"})
    info = kb.detect()
    assert info.installed and info.api == "API7" and info.prog_id == "KOMPAS.Application.7"
    assert info.running is False


def test_detect_reports_reason_when_not_registered(monkeypatch):
    _fake_win32(monkeypatch, set())
    info = kb.detect()
    assert not info.installed and "KOMPAS.Application.7" in info.note


class _PartBodies:
    """Как КОМПАС v25: False даёт тела (80x8x80), True добавляет плоскости (80x50x80)."""

    def GetGabarit(self, *a):
        if not a or len(a) < 7:
            raise RuntimeError("Несовпадение типов")
        if a[0] is False:
            return (-40.0, -4.0, -40.0, 40.0, 4.0, 40.0)
        return (-40.0, -25.0, -40.0, 40.0, 25.0, 40.0)


def test_bodies_only_gabarit_is_chosen_and_exact():
    att = []
    dims, exact = kb.read_body(_PartBodies(), att)
    assert dims == (80.0, 8.0, 80.0) and exact
    assert att[0].startswith("GetGabarit(False, 0 x6) -> успех")


def test_gabarit_with_planes_is_not_exact():
    doc = _Doc(_Part(True))                  # отвечает только вариант True
    r = kb.measure(_App(doc), "q.m3d")
    assert r.dims == (120.0, 5.0, 230.0) and not r.exact and doc.closed


def test_measure_marks_bodies_only_exact():
    r = kb.measure(_App(_Doc(_PartBodies())), "w.m3d")
    assert r.dims == (80.0, 8.0, 80.0) and r.exact


# ---------- исполнения: габарит каждого ----------

def test_match_bodies_by_marking():
    from core import m3d_stock
    embs = [{"drawing_no": "201647.03.00.022"}, {"drawing_no": "201647.03.00.022-01"},
            {"drawing_no": "201647.03.00.022-02"}]
    bodies = [{"marking": "201647.03.00.022-01", "dims": (100.0, 555.0, 4.0)},
              {"marking": "201647.03.00.022", "dims": (350.0, 570.0, 4.0)}]
    got = m3d_stock.match_bodies(embs, bodies)
    assert got == {0: (350.0, 570.0, 4.0), 1: (100.0, 555.0, 4.0)}    # -02 без габарита: читается по файлу


def test_match_bodies_by_order_when_marking_differs():
    from core import m3d_stock
    embs = [{"drawing_no": "A"}, {"drawing_no": "A-01"}]
    bodies = [{"marking": "X", "dims": (1.0, 2.0, 3.0)}, {"marking": "Y", "dims": (4.0, 5.0, 6.0)}]
    assert m3d_stock.match_bodies(embs, bodies) == {0: (1.0, 2.0, 3.0), 1: (4.0, 5.0, 6.0)}


def test_match_bodies_empty():
    from core import m3d_stock
    assert m3d_stock.match_bodies([{"drawing_no": "A"}], None) == {}
    assert m3d_stock.match_bodies([{"drawing_no": "A"}, {"drawing_no": "B"}], [{"marking": "Z", "dims": (1, 2, 3)}]) == {}


def test_read_embodiments_refetches_top_part(monkeypatch):
    """После SetCurrentEmbodiment деталь берётся у документа заново: именно так КОМПАС отдаёт нужное исполнение."""
    import sys
    import types
    from integrations.kompas import batch as kb

    state = {"cur": 3}
    data = {0: ("A", 1000.0, (-1, -1, 0, 1, 1, 4)), 1: ("A-01", 2000.0, (-2, -1, 0, 2, 1, 4)),
            2: ("A-02", 3000.0, (-3, -1, 0, 3, 1, 4))}

    class Part:
        def __init__(self, i):
            self.Marking, self.Mass, self._g = data[i]

        def GetGabarit(self, exact, *a):
            return (True,) + self._g if exact is False else (True,) + (0,) * 6

    class Doc3D:
        @property
        def TopPart(self):
            return Part(state["cur"] if state["cur"] in data else 0)

    class Mgr:
        EmbodimentCount = 3

        @property
        def CurrentEmbodimentIndex(self):
            return state["cur"]

        def SetCurrentEmbodiment(self, i):
            state["cur"] = i

    fake = types.ModuleType("win32com.client")
    fake.CastTo = lambda obj, name: Mgr() if name == "IEmbodimentsManager" else Doc3D()
    monkeypatch.setitem(sys.modules, "win32com", types.ModuleType("win32com"))
    monkeypatch.setitem(sys.modules, "win32com.client", fake)
    monkeypatch.setattr(kb, "read_body", lambda part, att: ((abs(part._g[3] - part._g[0]), abs(part._g[4] - part._g[1]), 4.0), True))
    out = kb.read_embodiments(object(), [])
    assert [e["marking"] for e in out] == ["A", "A-01", "A-02"]
    assert out[1]["dims"] == (4.0, 2.0, 4.0) and all(e["exact"] for e in out)
    assert state["cur"] == 3                                    # исходное исполнение возвращено


# ---------- гнутые детали, трубы ГОСТ 3262, округление ----------

def test_dn_pipe_straight_from_body():
    from core import m3d_stock as ms
    st = ms.stock_from_body((391.0, 33.5, 33.5), "Труба 25х3,2 ГОСТ 3262-75", 0.923, 7850)
    assert st and st.l_mm == 391 and abs(st.blank_mass_kg - 0.935) < 0.01     # 2,39 кг/м


def test_bent_pipe_estimated_by_mass():
    from core import m3d_stock as ms
    assert ms.stock_from_body((983.5, 33.5, 533.5), "Труба 25х3,2 ГОСТ 3262-75", 4.713, 7850) is None
    st = ms.estimate_from_mass((983.5, 33.5, 533.5), "Труба 25х3,2 ГОСТ 3262-75", 4.713, 7850)
    assert st and 1960 <= st.l_mm <= 1990 and "оценка" in st.method


def test_bent_sheet_estimate():
    from core import m3d_stock as ms
    assert ms.stock_from_body((655.0, 180.0, 20.0), "Лист 4 ГОСТ 19903-2015", 3.952, 7850) is None
    st = ms.estimate_from_mass((655.0, 180.0, 20.0), "Лист 4 ГОСТ 19903-2015", 3.952, 7850)
    assert st and st.l_mm == 655 and 190 <= st.h_mm <= 200


def test_bent_round_bar_estimate():
    from core import m3d_stock as ms
    st = ms.estimate_from_mass((56.0, 56.0, 88.0), "Круг 8 ГОСТ 2590-2006", 0.16, 7850)
    assert st and 395 <= st.l_mm <= 420


def test_angle_30x30x4_has_mass():
    from core import m3d_stock as ms
    st = ms.stock_from_body((30.0, 30.0, 517.0), "Уголок 30х30х4 ГОСТ 8509-93", 0.71, 7850)
    assert st and abs(st.blank_mass_kg - 0.92) < 0.01


def test_sheet_dimensions_rounded_up():
    from core import m3d_stock as ms
    st = ms.stock_from_body((426.3635, 24.0, 697.3333), "Лист 24 ГОСТ 19903-2015", 34.4, 7850)
    assert (st.h_mm, st.l_mm) == (427, 698)
    st = ms.stock_from_body((560.0335, 8.442, 355.0), "Лист 8 ГОСТ 19903-2015", 6.2, 7850)
    assert st is None or st.l_mm == 560 or st.h_mm == 355


def test_plausible():
    from core import m3d_stock as ms
    s = ms.Stock("sheet", blank_mass_kg=10.0)
    assert ms.plausible(s, 9.0) and not ms.plausible(s, 12.0) and not ms.plausible(s, 3.0)
    assert not ms.plausible(None, 1.0)


def test_match_kompas_carries_mass():
    from core import m3d_stock as ms
    out = ms.match_kompas([{"drawing_no": "A"}, {"drawing_no": "A-01"}],
                          [{"marking": "A-01", "dims": (1, 2, 3), "mass_g": 2500.0}])
    assert out == {1: {"dims": (1, 2, 3), "mass_kg": 2.5}}
