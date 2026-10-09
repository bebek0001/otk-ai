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
