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


def test_import_excel_writes_etalon_and_decision(tmp_path, monkeypatch):
    from core import drawing_db
    from core.assistant import store as st
    from openpyxl import Workbook
    monkeypatch.setattr(st, "ASSISTANT_DIR", tmp_path / "a")
    monkeypatch.setattr(drawing_db, "DB_PATH", str(tmp_path / "db.json"))
    monkeypatch.setattr(drawing_db, "SEED_DB_PATH", tmp_path / "no_seed.json")
    wb = Workbook(); ws = wb.active
    ws.append(["№", "№ чертежа", "Наименование", "Заготовка", "Размер заготовки",
               "Масса заг., кг", "Масса чист., кг"])
    ws.append([1, "140826.00.00.001", "Штырь",
               "Шестигранник 55 ГОСТ 2879-2006/ 12Х18Н10Т ГОСТ 5949-2018",
               "Шестигранник 55, L=170", "3,5", "2,5"])
    ws.append([2, "140826.00.00.099", "Прочее", "", "", "", "1,0"])     # без ответа: пропуск
    p = tmp_path / "t.xlsx"; wb.save(p)
    added, skipped, etalons = excel_import.import_corrected_excel(str(p))
    assert (added, skipped, etalons) == (1, 1, 1)
    r = drawing_db.lookup_by_drawing_no("140826.00.00.001")
    assert r["stock_type"] == "Шестигранник" and r["l_blank_mm"] == 170.0
    assert r["mass_kg"] == 3.5 and r["mass_clean_kg"] == 2.5
    assert r["result_line"].splitlines()[0].rstrip(";") == "Шестигранник 55 ГОСТ 2879-2006"
    # повторный импорт заменяет запись, а не дублирует
    excel_import.import_corrected_excel(str(p))
    assert len([x for x in drawing_db.load_db() if x["drawing_no"] == "140826.00.00.001"]) == 1


def test_thread_bridge_runs_posted_calls():
    from ui import thread_bridge
    import threading
    out = []
    t = threading.Thread(target=lambda: thread_bridge.post(out.append, 7))
    t.start(); t.join()
    assert out == []                                  # из потока ничего не выполнилось
    assert thread_bridge.drain_for_tests() == 1 and out == [7]


def test_forced_type_is_thread_local():
    import threading
    from core import engine
    seen = {}
    engine.set_forced_stock_type("Швеллер")
    try:
        t = threading.Thread(target=lambda: seen.update(v=getattr(engine._forced_tls, "value", None)))
        t.start(); t.join()
        assert seen["v"] is None                      # другой поток тип не видит
        assert getattr(engine._forced_tls, "value") == "Швеллер"
    finally:
        engine.set_forced_stock_type(None)


def test_seed_merge_adds_new_records_only(tmp_path, monkeypatch):
    import json
    from core import drawing_db as db
    seed = tmp_path / "seed.json"
    user = tmp_path / "db.json"
    monkeypatch.setattr(db, "SEED_DB_PATH", seed)
    monkeypatch.setattr(db, "DB_PATH", str(user))
    monkeypatch.setattr(db, "_seed_checked", False)
    rec = lambda n, h: {"drawing_no": n, "pdf_text_hash": h, "part_name": n}
    seed.write_text(json.dumps([rec("A.1", "h1")]), encoding="utf-8")
    assert [r["drawing_no"] for r in db.load_db()] == ["A.1"]          # первый запуск
    # пользователь удалил A.1 и добавил свою запись; в обновлении seed пришла A.2
    user.write_text(json.dumps([rec("U.1", "hu")]), encoding="utf-8")
    seed.write_text(json.dumps([rec("A.1", "h1"), rec("A.2", "h2")]), encoding="utf-8")
    monkeypatch.setattr(db, "_seed_checked", False)
    nums = sorted(r["drawing_no"] for r in db.load_db())
    assert nums == ["A.2", "U.1"]                                      # A.1 не возвращается


def test_agent_autonomy_is_earned(tmp_path, monkeypatch):
    from core import agent
    monkeypatch.setattr(agent, "AGENT_DIR", tmp_path)
    assert agent.autonomy("formula_stamp") == "проверка"
    for _ in range(30):
        agent.record_outcome("formula_stamp", True)
    assert agent.autonomy("formula_stamp") == "проверка"   # 30/30: нижняя граница ~0.89
    for _ in range(50):
        agent.record_outcome("formula_stamp", True)
    assert agent.autonomy("formula_stamp") == "авто"
    for _ in range(5):
        agent.record_outcome("formula_stamp", False)
    assert agent.autonomy("formula_stamp") == "проверка"
    for _ in range(100):
        agent.record_outcome("ai_guess", True)
    assert agent.autonomy("ai_guess") == "проверка"      # ИИ никогда не авто


def test_agent_learns_from_corrected_row(tmp_path, monkeypatch):
    import json
    from core import agent
    monkeypatch.setattr(agent, "AGENT_DIR", tmp_path)
    (tmp_path / "log").mkdir()
    rec = {"drawing_no": "АБВ 1.2", "source": "CDW+расчёт",
           "new_sortament": "Круг 40", "stock_size": "L=100"}
    (tmp_path / "log" / "d.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    ok = agent.learn_from_row("абв1.2", {"sortament": "Круг 40", "size": "L=100"}, tmp_path / "log")
    assert ok is True
    bad = agent.learn_from_row("АБВ 1.2", {"sortament": "Круг 45", "size": "L=100"}, tmp_path / "log")
    assert bad is False
    assert agent.load_stats()["formula_stamp"] == {"n": 2, "correct": 1}


def test_export_stamp_sortament_without_size():
    from core import client_format as cf
    r = {"drawing_no": "1.1", "part_name": "Нож", "status": "⚠ нет длины", "source": "CDW",
         "new_sortament": "Лист 6 ГОСТ 19903-2015",
         "material": "Лист 6 ГОСТ 19903-2015;12Х18Н10Т ГОСТ 7350-77", "clean_mass_kg": "5,04",
         "error": "габариты не заданы"}
    row = cf.rows_for_client([r])[0]
    assert row[3] == "Лист 6 ГОСТ 19903-2015/ 12Х18Н10Т ГОСТ 7350-77"
    assert row[4] == "" and row[7] == "габариты не заданы"


def test_import_takes_technologist_block_when_headers_repeat(tmp_path, monkeypatch):
    """В таблице технолога колонки программы (D-E) и его ответы (K-L) названы одинаково:
    берётся правый блок. Строка «Убрать» пропускается."""
    from core import drawing_db
    from core.assistant import store as st
    from openpyxl import Workbook
    monkeypatch.setattr(st, "ASSISTANT_DIR", tmp_path / "a")
    monkeypatch.setattr(drawing_db, "DB_PATH", str(tmp_path / "db.json"))
    monkeypatch.setattr(drawing_db, "SEED_DB_PATH", tmp_path / "no_seed.json")
    wb = Workbook(); ws = wb.active
    ws.append(["№", "№ чертежа", "Наименование", "Сортамент", "Размер заготовки",
               "Масса заг., кг", "Масса чист., кг", "", "Сортамент", "Размеры Заг", "Вес Заг"])
    ws.append([1, "1.1", "Нож", "Лист 2000x1000 ГОСТ 19903-74", "BхL=2000х1000", "196,00", "5,04", "",
               "Лист 6 ГОСТ 19903-2015/ 12Х18Н10Т ГОСТ 7350-77", "HxL=140x765", 5.07654])
    ws.append([2, "1.2", "Корпус СБ", "Лист 1", "x", "1", "1", "", "Убрать", None, None])
    p = tmp_path / "t.xlsx"; wb.save(p)
    added, skipped, etalons = excel_import.import_corrected_excel(str(p))
    assert (added, skipped, etalons) == (1, 1, 1)
    r = drawing_db.lookup_by_drawing_no("1.1")
    assert r["result_line"].splitlines()[1].rstrip(";") == "HxL=140x765" and r["d_blank_std_mm"] == 6
