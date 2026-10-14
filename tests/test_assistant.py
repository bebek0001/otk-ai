import sys, tempfile, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core.assistant import model, excel_import, store


def test_stock_type_from_text():
    f = excel_import.stock_type_from_text
    assert f("Круг 130 ГОСТ2590-2006") == "Круг"
    assert f("Лист Б-ПН-10 ГОСТ19903") == "ЛистГК"
    assert f("Швеллер 10У") == "Швеллер"
    assert f("что-то") is None


def test_train_and_suggest(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "MODELS_DIR", tmp_path)
    rep = model.train()
    assert rep["ok"] and rep["holdout_acc"] is not None
    s = model.suggest("Втулка", "В20", 300)
    assert s.candidates and s.candidates[0].stock_type == "Труба"


def test_gate_keeps_better_model(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "MODELS_DIR", tmp_path)
    model.train()
    meta = model.active_meta()
    meta["holdout_acc"] = 1.0
    (tmp_path / f"{meta['version']}.json").write_text(
        __import__("json").dumps(meta), encoding="utf-8")
    rep = model.train()
    assert rep["replaced"] is False


def test_engine_forced_type():
    from core import engine
    ex = engine.Extracted(pdf_text="", part_name="Крышка", looks_rotational=False,
                          material_mark=None, material_gost=None, stamp_mass_kg=None,
                          diameter_features=[], max_d_mm=None, max_d_tol=None,
                          square_features_mm=[], max_square_mm=None, ra_values=[], length_mm=None)
    engine.FORCED_STOCK_TYPE = "Швеллер"
    try:
        assert engine.detect_stock_type(ex) == "Швеллер"
    finally:
        engine.FORCED_STOCK_TYPE = None
    assert engine.detect_stock_type(ex) != "Швеллер"


def test_decision_replaces_same_drawing(tmp_path, monkeypatch):
    from core.assistant import store as st
    monkeypatch.setattr(st, "ASSISTANT_DIR", tmp_path)
    st.add_decision("Вал", "45", 100, "Круг", "X.1")
    st.add_decision("Вал", "45", 100, "Квадрат", "X.1")
    d = st.load_decisions()
    assert len(d) == 1 and d[0]["stock_type"] == "Квадрат"


def test_client_format_rows():
    from core import client_format as cf
    res = [
        {"status": "ОК", "drawing_no": "290426.00.00.002", "part_name": "Штырь направляющий",
         "new_sortament": "Шестигранник 24 ГОСТ2879-2006", "stock_size": "L=150",
         "stock_mass_kg": "0,60", "clean_mass_kg": "0,33", "material": "Ст2пс ГОСТ 535-2005"},
        {"status": "⚠ нет длины", "drawing_no": "290426.00.00.021", "part_name": "Полоса",
         "error": "длина не определена", "clean_mass_kg": "0,14"},
    ]
    rows = cf.rows_for_client(res)
    assert rows[0][3] == "Шестигранник 24 ГОСТ 2879-2006/ Ст2пс ГОСТ 535-2005"
    assert rows[0][4] == "Шестигранник 24, L=150"
    assert rows[0][5] == "0,6" and rows[0][6] == "0,33"
    assert rows[1][3] == "" and rows[1][7] == "длина не определена"


def test_mass_check():
    from core import mass_check as mc
    assert mc.check("0,6", "0,33") == (True, "")
    assert mc.check("0,036", "0,005")[0] is True          # реальный эталон, отношение 7,2
    assert mc.check("0,2", "0,33")[0] is False            # легче детали
    assert mc.check("5", "0,3")[0] is False               # в 16 раз тяжелее
    assert mc.check("", "0,3") == (True, "")              # нет массы, проверки нет


def test_mass_check_on_real_etalons():
    import json, pathlib
    from core import mass_check as mc
    db = json.loads((pathlib.Path(__file__).resolve().parents[1] / "seed" / "drawing_db.json").read_text(encoding="utf-8"))
    for r in db:
        if r.get("mass_clean_kg"):
            assert mc.check(r["mass_kg"], r["mass_clean_kg"])[0], r["part_name"]


def test_formulas_first_does_not_use_rag(monkeypatch):
    from core import engine
    seen = {}

    def core_stub(ex, l, m, g):
        seen["rag"] = engine.USE_RAG
        return "ok"
    monkeypatch.setattr(engine, "_calculate_stock_core", core_stub)
    monkeypatch.setattr(engine, "_formulas_first_enabled", lambda: True)
    assert engine.calculate_missing_stock_line(None, 1, "", "") == "ok"
    assert seen["rag"] is False and engine.USE_RAG is True   # флаг восстановлен


def test_formulas_fail_then_ai_layer(monkeypatch):
    from core import engine
    calls = []

    def core_stub(ex, l, m, g):
        calls.append(engine.USE_RAG)
        if not engine.USE_RAG:
            raise ValueError("формулы не смогли")
        return "ai-answer"
    monkeypatch.setattr(engine, "_calculate_stock_core", core_stub)
    monkeypatch.setattr(engine, "_formulas_first_enabled", lambda: True)
    monkeypatch.setattr(engine, "_RAG_AVAILABLE", True)
    assert engine.calculate_missing_stock_line(None, 1, "", "") == "ai-answer"
    assert calls == [False, True]


def test_cdw_dims_sections_and_proposal():
    from core import cdw_dims as D
    sec = D.parse_section("Круг 40 ГОСТ 2590-2006/ 45 ГОСТ 1050-2013")
    assert sec.kind == "Круг" and sec.sizes == (40.0,)
    assert D.parse_section("Труба 108x20 ГОСТ 8732-2025").sizes == (108.0, 20.0)
    assert D.parse_section("Уголок 40х40х5 ГОСТ 8509-93").area_mm2 == 75 * 5
    assert D.parse_section("Лист 16 ГОСТ 19903-2015").kind == "Лист"
    # Штырь Ø40: деталь 95 мм, масса 0,65 кг -> заготовка 105
    p = D.propose_length(sec, [10.0, 35.0, 95.0], 0.65, 7850.0)
    assert p.part_length_mm == 95.0 and p.blank_length_mm == 105.0
    # Размеров нет или лист: предложения нет
    assert D.propose_length(sec, [], 0.65).blank_length_mm is None
    sheet = D.parse_section("Лист 4 ГОСТ 19903-2015")
    assert D.propose_length(sheet, [100.0], 1.0).blank_length_mm is None
    # Радиус не принимается за длину; нереальная масса отбрасывается
    big = D.parse_section("Круг 220 ГОСТ 2590-2006")
    p2 = D.propose_length(big, [110.0, 20.0], 5.64, 7850.0)
    assert p2.part_length_mm != 110.0


def test_atomic_json_and_corrupt_quarantine(tmp_path):
    from core import fsutil
    p = tmp_path / "db.json"
    fsutil.write_json_atomic(p, [{"a": 1}])
    assert p.read_text(encoding="utf-8").startswith("[")
    assert not (tmp_path / "db.json.tmp").exists()
    p.write_text("{битый", encoding="utf-8")
    kept = fsutil.quarantine_corrupt(p)
    assert kept and "corrupt" in kept and open(kept, encoding="utf-8").read() == "{битый"
