"""ИИ-проверка размера: ответ принимается только после проверки по массе."""
from core import m3d_ai, m3d_preview

SHEET = "Лист 4 ГОСТ 19903-2015;Ст3пс ГОСТ 14637-89"


class _Msg:
    def __init__(self, text):
        self.content = [type("B", (), {"text": text})()]


class _Client:
    def __init__(self, text):
        self.messages = self
        self._t = text

    def create(self, **kw):
        assert kw["model"] and kw["system"]
        return _Msg(self._t)


def test_valid_sheet_answer_is_accepted():
    ans = {"kind": "sheet", "h": 570, "l": 350, "confidence": "high", "reason": "по массе"}
    st = m3d_ai.validate(ans, SHEET, 6.264, 7850)
    assert (st.h_mm, st.l_mm) == (350.0, 570.0) and abs(st.blank_mass_kg - 6.26) < 0.01


def test_absurd_or_light_answers_rejected():
    light = {"kind": "sheet", "h": 100, "l": 100}      # заготовка легче детали
    assert m3d_ai.validate(light, SHEET, 6.264, 7850) is None
    huge = {"kind": "sheet", "h": 3000, "l": 6000}      # нелепо тяжёлая
    assert m3d_ai.validate(huge, SHEET, 6.264, 7850) is None
    assert m3d_ai.validate({"kind": "unknown"}, SHEET, 6.264, 7850) is None
    assert m3d_ai.validate("мусор", SHEET, 6.264, 7850) is None


def test_suggest_with_fake_client_and_json_in_text():
    c = _Client('Вот ответ: {"kind":"lin","l":958,"confidence":"high","reason":"частый размер"}')
    got = m3d_ai.suggest("x.m3d", {"drawing_no": "1"}, "Швеллер 14П ГОСТ 8240-97;Ст3",
                         11.712, 7850, client=c, facts_fn=lambda p, r: {"a": 1})
    assert got and got[0].l_mm == 958.0
    bad = _Client("не JSON")
    assert m3d_ai.suggest("x.m3d", {}, SHEET, 6.2, 7850, client=bad,
                          facts_fn=lambda p, r: {}) is None


def test_preview_missing_file_is_none(tmp_path):
    p = tmp_path / "a.m3d"
    p.write_bytes(b"not a zip")
    assert m3d_preview.read_preview(str(p)) is None


def test_m3d_tab_formats_rows_like_the_table():
    import sys
    from unittest.mock import MagicMock
    for m in ("customtkinter", "tkinter", "tkinter.filedialog", "tkinter.messagebox",
              "tkinter.ttk", "PIL", "PIL.Image", "PIL.ImageTk", "tkinterdnd2", "tkinter.font"):
        sys.modules.setdefault(m, MagicMock())
    from ui.screens.m3d_only_tool import format_rows
    txt = format_rows([{
        "drawing_no": "235608.12.01.004", "part_name": "Пластина",
        "zagotovka_full": "Лист 5 ГОСТ 19903-2015/ Ст3пс ГОСТ 14637-89",
        "stock_size": "HxL = 120x230", "stock_mass_kg": "1,08", "clean_mass_kg": "0,972",
        "source": "M3D", "error": ""}])
    assert "120x230" in txt and "1,08" in txt and "0,972" in txt and "Источник: M3D" in txt
    assert format_rows([]) == "Нет данных"
