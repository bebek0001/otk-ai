import os
import re
import threading
import customtkinter as ctk
from tkinter import filedialog, messagebox

from core.settings import load_settings
from core.step_reader import read_step, density_for_material, StepModel, ocp_available
from core.m3d_reader import read_m3d, find_m3d_for, M3DInfo
from core.engine import (
    normalize_mark,
    normalize_gost,
    compute_blank_from_step_model,
)
from core import logger


def _find_step_for(m3d_path: str):
    """Ищет STEP рядом с .m3d (по совпадению имени)."""
    if not m3d_path:
        return None
    base = os.path.splitext(m3d_path)[0]
    for ext in (".stp", ".step", ".STP", ".STEP"):
        if os.path.exists(base + ext):
            return base + ext
    return None


class Model3DToolScreen(ctk.CTkFrame):
    """
    3D инструмент. Три режима работы:

      1) Только STEP (.stp)  — геометрия есть, материал вводится вручную
      2) Только КОМПАС (.m3d) — материал/масса есть, геометрии нет
      3) Оба файла вместе     — полный режим, всё автоматически

    STEP несёт геометрию (габариты, объём, тип проката),
    .m3d несёт свойства (материал, плотность, масса, литера).
    """

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.step_path: str | None = None
        self.m3d_path: str | None = None
        self.model: StepModel | None = None
        self.m3d_info: M3DInfo | None = None

        self._build_ui()

    # ── UI ──────────────────────────────────────────────────

    def _build_ui(self):
        # ═══ Файлы ═══
        top = ctk.CTkFrame(self, corner_radius=18)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            top, text="3D инструмент",
            font=ctk.CTkFont(size=20, weight="bold")
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(10, 6))

        # Слот 1: STEP
        ctk.CTkLabel(top, text="STEP (.stp) — геометрия:", anchor="w", width=190).grid(
            row=1, column=0, sticky="w", padx=(16, 8), pady=4)
        self.lbl_step = ctk.CTkLabel(
            top, text="не выбран", anchor="w", text_color=("gray50", "#777777"))
        self.lbl_step.grid(row=1, column=1, sticky="ew", pady=2)
        ctk.CTkButton(
            top, text="Выбрать STEP", height=30, corner_radius=10, width=130,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), command=self.on_pick_step
        ).grid(row=1, column=2, padx=16, pady=2)

        # Слот 2: m3d
        ctk.CTkLabel(top, text="КОМПАС (.m3d) — материал:", anchor="w", width=190).grid(
            row=2, column=0, sticky="w", padx=(16, 8), pady=2)
        self.lbl_m3d = ctk.CTkLabel(
            top, text="не выбран", anchor="w", text_color=("gray50", "#777777"))
        self.lbl_m3d.grid(row=2, column=1, sticky="ew", pady=2)
        ctk.CTkButton(
            top, text="Выбрать .m3d", height=30, corner_radius=10, width=130,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), command=self.on_pick_m3d
        ).grid(row=2, column=2, padx=16, pady=2)

        # Индикатор режима
        self.lbl_mode = ctk.CTkLabel(
            top, text="Режим: файлы не выбраны",
            anchor="w", font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("gray50", "#777777"))
        self.lbl_mode.grid(row=3, column=0, columnspan=3, sticky="w", padx=16, pady=(6, 2))

        ctk.CTkLabel(
            top,
            text="Файлы с одинаковым именем подхватываются автоматически. "
                 "Второй файл можно не выбирать — режим подстроится.",
            text_color=("gray50", "#777777"), font=ctk.CTkFont(size=11), anchor="w"
        ).grid(row=4, column=0, columnspan=3, sticky="w", padx=16, pady=(0, 8))

        # ═══ Данные ═══
        data = ctk.CTkFrame(self, corner_radius=18)
        data.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
        data.grid_columnconfigure(1, weight=1)
        data.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(
            data, text="Данные из модели + ввод",
            text_color=("gray40", "#A7A7A7"), font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(8, 4))

        ctk.CTkLabel(data, text="Обозначение:", anchor="w").grid(
            row=1, column=0, sticky="w", padx=(16, 8), pady=2)
        self.var_dno = ctk.StringVar()
        ctk.CTkEntry(data, textvariable=self.var_dno, height=32, corner_radius=8).grid(
            row=1, column=1, sticky="ew", pady=2, padx=(0, 16))

        ctk.CTkLabel(data, text="Наименование:", anchor="w").grid(
            row=1, column=2, sticky="w", padx=(0, 8), pady=2)
        self.var_pname = ctk.StringVar()
        ctk.CTkEntry(data, textvariable=self.var_pname, height=32, corner_radius=8).grid(
            row=1, column=3, sticky="ew", pady=2, padx=(0, 16))

        ctk.CTkLabel(data, text="Габариты X×Y×Z:", anchor="w").grid(
            row=2, column=0, sticky="w", padx=(16, 8), pady=2)
        self.lbl_dims = ctk.CTkLabel(data, text="—", anchor="w",
                                     text_color=("gray30", "#CCCCCC"))
        self.lbl_dims.grid(row=2, column=1, columnspan=3, sticky="w", pady=2)

        ctk.CTkLabel(data, text="Тип по геометрии:", anchor="w").grid(
            row=3, column=0, sticky="w", padx=(16, 8), pady=2)
        self.lbl_shape = ctk.CTkLabel(
            data, text="—", anchor="w", text_color=("green4", "#4CAF50"),
            font=ctk.CTkFont(size=13, weight="bold"))
        self.lbl_shape.grid(row=3, column=1, columnspan=3, sticky="w", pady=2)

        ctk.CTkLabel(data, text="Марка материала:", anchor="w").grid(
            row=4, column=0, sticky="w", padx=(16, 8), pady=2)
        self.var_mark = ctk.StringVar()
        ctk.CTkEntry(data, textvariable=self.var_mark, height=32, corner_radius=8,
                     placeholder_text="напр. Сталь 45").grid(
            row=4, column=1, sticky="ew", pady=2, padx=(0, 16))
        self.var_mark.trace_add("write", lambda *a: self._on_material_change())

        ctk.CTkLabel(data, text="ГОСТ материала:", anchor="w").grid(
            row=4, column=2, sticky="w", padx=(0, 8), pady=2)
        self.var_mgost = ctk.StringVar()
        ctk.CTkEntry(data, textvariable=self.var_mgost, height=32, corner_radius=8,
                     placeholder_text="напр. ГОСТ 1050-2013").grid(
            row=4, column=3, sticky="ew", pady=2, padx=(0, 16))

        ctk.CTkLabel(data, text="Плотность, кг/м³:", anchor="w").grid(
            row=5, column=0, sticky="w", padx=(16, 8), pady=2)
        self.var_dens = ctk.StringVar(value="7850")
        ctk.CTkEntry(data, textvariable=self.var_dens, height=32, corner_radius=8,
                     width=110).grid(row=5, column=1, sticky="w", pady=2)

        ctk.CTkLabel(data, text="Припуск, мм:", anchor="w").grid(
            row=5, column=2, sticky="w", padx=(0, 8), pady=2)
        self.var_allow = ctk.StringVar(value="0")
        ctk.CTkEntry(data, textvariable=self.var_allow, height=32, corner_radius=8,
                     width=110).grid(row=5, column=3, sticky="w", pady=(2, 6))

        self.lbl_hint = ctk.CTkLabel(
            data, text="", text_color=("gray50", "#777777"),
            font=ctk.CTkFont(size=11), anchor="w")
        self.lbl_hint.grid(row=6, column=0, columnspan=4, sticky="w", padx=16, pady=(0, 8))

        # ═══ Кнопки ═══
        btns = ctk.CTkFrame(self, corner_radius=18)
        btns.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 6))

        self.btn_read = ctk.CTkButton(
            btns, text="Прочитать модель", height=36, corner_radius=12, width=160,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), command=self.on_read, state="disabled")
        self.btn_read.pack(side="left", padx=(12, 4), pady=12)

        self.btn_calc = ctk.CTkButton(
            btns, text="Определить заготовку", height=36, corner_radius=12, width=180,
            fg_color="#16a34a", hover_color="#15803d", text_color="white",
            command=self.on_calc, state="disabled")
        self.btn_calc.pack(side="left", padx=4, pady=8)

        self.btn_copy = ctk.CTkButton(
            btns, text="Копировать результат", height=36, corner_radius=12, width=170,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), command=self.on_copy, state="disabled")
        self.btn_copy.pack(side="left", padx=4, pady=8)

        self.btn_clear = ctk.CTkButton(
            btns, text="Сбросить", height=36, corner_radius=12, width=110,
            fg_color="transparent", border_width=1,
            text_color=("gray10", "gray90"), command=self.on_clear)
        self.btn_clear.pack(side="left", padx=4, pady=8)

        # ═══ Результат / Протокол ═══
        out = ctk.CTkFrame(self, corner_radius=18)
        out.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 10))
        out.grid_columnconfigure(0, weight=1)
        out.grid_columnconfigure(1, weight=1)
        out.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(out, text="Заготовка (результат)",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 8))
        self.txt_res = ctk.CTkTextbox(out, corner_radius=14,
                                      font=ctk.CTkFont(size=13), wrap="word")
        self.txt_res.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=(0, 16))

        ctk.CTkLabel(out, text="Протокол",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=1, sticky="w", padx=16, pady=(14, 8))
        self.txt_prot = ctk.CTkTextbox(out, corner_radius=14,
                                       font=ctk.CTkFont(size=12), wrap="word")
        self.txt_prot.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=(0, 16))

    # ── Режимы ──────────────────────────────────────────────

    def _mode(self) -> str:
        if self.step_path and self.m3d_path:
            return "full"
        if self.step_path:
            return "step"
        if self.m3d_path:
            return "m3d"
        return "none"

    def _update_mode(self):
        mode = self._mode()
        cfg = {
            "full": ("Режим 3: ПОЛНЫЙ — геометрия из STEP + материал из .m3d",
                     ("green4", "#4CAF50"),
                     "Всё определяется автоматически, вводить ничего не нужно."),
            "step": ("Режим 1: ТОЛЬКО STEP — геометрия есть, материала нет",
                     ("#b45309", "#FFB74D"),
                     "Материал в STEP отсутствует — введите марку вручную "
                     "(или добавьте .m3d с тем же именем)."),
            "m3d":  ("Режим 2: ТОЛЬКО .m3d — материал есть, геометрии нет",
                     ("#b45309", "#FFB74D"),
                     "В .m3d геометрия закрыта (формат C3D): тип и размеры заготовки "
                     "определить нельзя. Добавьте STEP этой же детали."),
            "none": ("Режим: файлы не выбраны", ("gray50", "#777777"), ""),
        }[mode]
        self.lbl_mode.configure(text=cfg[0], text_color=cfg[1])
        self.lbl_hint.configure(text=cfg[2])
        self.btn_read.configure(state="normal" if mode != "none" else "disabled")

    # ── Выбор файлов ────────────────────────────────────────

    def on_pick_step(self):
        path = filedialog.askopenfilename(
            title="Выбери STEP-модель",
            filetypes=[("STEP", "*.stp *.step"), ("All files", "*.*")])
        if not path:
            return
        self.step_path = path
        self.lbl_step.configure(text=os.path.basename(path), text_color=("gray10", "white"))
        # автопоиск .m3d рядом
        if not self.m3d_path:
            sib = find_m3d_for(path)
            if sib:
                self.m3d_path = sib
                self.lbl_m3d.configure(text=os.path.basename(sib) + "  (найден автоматически)",
                                       text_color=("green4", "#4CAF50"))
        self._update_mode()
        self.on_read()

    def on_pick_m3d(self):
        path = filedialog.askopenfilename(
            title="Выбери модель КОМПАС",
            filetypes=[("КОМПАС", "*.m3d *.a3d"), ("All files", "*.*")])
        if not path:
            return
        self.m3d_path = path
        self.lbl_m3d.configure(text=os.path.basename(path), text_color=("gray10", "white"))
        # автопоиск STEP рядом
        if not self.step_path:
            sib = _find_step_for(path)
            if sib:
                self.step_path = sib
                self.lbl_step.configure(text=os.path.basename(sib) + "  (найден автоматически)",
                                        text_color=("green4", "#4CAF50"))
        self._update_mode()
        self.on_read()

    def on_clear(self):
        self.step_path = self.m3d_path = None
        self.model = self.m3d_info = None
        self.lbl_step.configure(text="не выбран", text_color=("gray50", "#777777"))
        self.lbl_m3d.configure(text="не выбран", text_color=("gray50", "#777777"))
        self.lbl_dims.configure(text="—")
        self.lbl_shape.configure(text="—")
        for v in (self.var_dno, self.var_pname, self.var_mark, self.var_mgost):
            v.set("")
        self.var_dens.set("7850")
        self.var_allow.set("0")
        self.txt_res.delete("1.0", "end")
        self.txt_prot.delete("1.0", "end")
        self.btn_calc.configure(state="disabled")
        self.btn_copy.configure(state="disabled")
        self._update_mode()

    def _on_material_change(self):
        # плотность из .m3d приоритетнее — не перетираем её
        if self.m3d_info and self.m3d_info.density:
            return
        mark = self.var_mark.get().strip()
        if mark:
            self.var_dens.set(str(int(density_for_material(mark))))

    # ── Чтение ──────────────────────────────────────────────

    def on_read(self):
        if self._mode() == "none":
            return
        self.btn_read.configure(state="disabled", text="Чтение…")
        self.txt_prot.delete("1.0", "end")
        self.txt_prot.insert("1.0", "Читаю модель…\n")

        def worker():
            mi = None
            if self.m3d_path:
                try:
                    mi = read_m3d(self.m3d_path)
                except Exception as e:
                    logger.error(f"3D: ошибка чтения .m3d: {e}")

            dens = 7850.0
            if mi and mi.density:
                dens = mi.density
            else:
                try:
                    dens = float(self.var_dens.get().strip().replace(",", ".") or 7850)
                except ValueError:
                    dens = 7850.0

            m = None
            if self.step_path:
                try:
                    m = read_step(self.step_path, density=dens)
                except Exception as e:
                    logger.error(f"3D: ошибка чтения STEP: {e}")

            def ui():
                self.btn_read.configure(state="normal", text="Прочитать модель")
                self.m3d_info = mi
                self.model = m

                # Свойства из .m3d приоритетнее (они точнее)
                if mi:
                    if mi.drawing_no:
                        self.var_dno.set(mi.drawing_no)
                    if mi.part_name:
                        self.var_pname.set(mi.part_name)
                    if mi.material_mark:
                        self.var_mark.set(mi.material_mark)
                    if mi.material_gost:
                        self.var_mgost.set(mi.material_gost)
                    if mi.density:
                        self.var_dens.set(str(int(mi.density)))
                if m:
                    if not self.var_dno.get():
                        self.var_dno.set(m.drawing_no or "")
                    if not self.var_pname.get():
                        self.var_pname.set(m.part_name or "")
                    self.lbl_dims.configure(
                        text=f"{m.dx:.1f} × {m.dy:.1f} × {m.dz:.1f} мм    "
                             f"(объём детали {m.volume_mm3/1000:.2f} см³)")
                    self.lbl_shape.configure(text=f"{m.shape_type}   →   {m.stock_size_hint}")
                    self.btn_calc.configure(state="normal")
                else:
                    if self.step_path:
                        ok, _ = ocp_available()
                        txt = ("— (STEP не прочитан: нет библиотеки cadquery-ocp)"
                               if not ok else "— (STEP не прочитан: проверь файл)")
                        self.lbl_dims.configure(text=txt)
                    else:
                        self.lbl_dims.configure(text="— (нет STEP: геометрия недоступна)")
                    self.lbl_shape.configure(text="— (нужен STEP)")
                    self.btn_calc.configure(state="disabled")

                # Протокол
                parts = []
                if mi:
                    parts.append(mi.protocol)
                if m:
                    parts.append(m.protocol)
                if not parts:
                    ok, err = ocp_available()
                    if not ok and self.step_path:
                        parts.append(
                            "❌ Не установлена библиотека для чтения STEP.\n\n"
                            "Выполни в терминале:\n"
                            "    pip install cadquery-ocp\n\n"
                            "Это ~200 МБ, ставится несколько минут.\n"
                            "Файлы .m3d работают и без неё (там только zip+xml).\n\n"
                            f"Техническая ошибка: {err}")
                    else:
                        parts.append("Ничего не прочитано. Проверь файлы.")
                # сверка масс, если есть обе
                if mi and m and mi.mass_kg:
                    ours = m.volume_mm3 / 1e9 * (mi.density or 7850)
                    diff = abs(ours - mi.mass_kg) / mi.mass_kg * 100 if mi.mass_kg else 0
                    parts.append(
                        "Сверка масс:\n"
                        f"- Масса детали (наш расчёт по STEP): {ours:.4f} кг\n"
                        f"- Масса из КОМПАС (.m3d):            {mi.mass_kg:.4f} кг\n"
                        f"- Расхождение: {diff:.3f} %"
                        + ("   ✓ геометрия и плотность сходятся" if diff < 1 else
                           "   ⚠ проверь: файлы могут быть от разных версий детали"))
                self.txt_prot.delete("1.0", "end")
                self.txt_prot.insert("1.0", "\n\n".join(parts))
                self.txt_prot.yview_moveto(0.0)

            self.after(0, ui)

        threading.Thread(target=worker, daemon=True).start()

    # ── Расчёт заготовки ────────────────────────────────────

    def on_calc(self):
        m = self.model
        if not m:
            self.txt_res.delete("1.0", "end")
            self.txt_res.insert("1.0",
                "Нет геометрии — нужен STEP-файл.\n\n"
                "В .m3d геометрия хранится в закрытом формате C3D и не читается.\n"
                "Экспортируй деталь в STEP (.stp) и положи рядом с .m3d.")
            return

        try:
            dens = float(self.var_dens.get().strip().replace(",", ".") or 7850)
        except ValueError:
            dens = 7850.0
        try:
            allow = float(self.var_allow.get().strip().replace(",", ".") or 0)
        except ValueError:
            allow = 0.0

        mark = normalize_mark(self.var_mark.get().strip())
        mgost = normalize_gost(self.var_mgost.get().strip())

        src = {"full": "STEP (геометрия) + .m3d (материал)",
               "step": "только STEP (материал введён вручную)",
               "m3d": "только .m3d"}[self._mode()]

        calc = compute_blank_from_step_model(m, mark, mgost, dens, allow, src)

        if not calc["ok"]:
            self.txt_res.delete("1.0", "end")
            self.txt_res.insert("1.0", calc["message"] + "\n\nЗадай тип и размер вручную.")
            return

        self.txt_res.delete("1.0", "end")
        self.txt_res.insert("1.0", calc["result"])
        self.txt_res.yview_moveto(0.0)

        prot_extra = (" (из .m3d)" if self.m3d_info and self.m3d_info.density else
                      (f" (по марке {mark})" if mark else " (по умолчанию)"))
        protocol = calc["protocol"].replace(
            f"- Плотность: {dens:.0f} кг/м³", f"- Плотность: {dens:.0f} кг/м³{prot_extra}")
        if not mark:
            protocol += "\n- ⚠ Марка материала не задана: добавьте .m3d или введите вручную"

        head_prot = []
        if self.m3d_info:
            head_prot.append(self.m3d_info.protocol)
        head_prot.append(m.protocol)
        head_prot.append(protocol)
        self.txt_prot.delete("1.0", "end")
        self.txt_prot.insert("1.0", "\n\n".join(head_prot))
        self.txt_prot.yview_moveto(0.0)
        self.btn_copy.configure(state="normal")
        logger.info(f"3D: заготовка определена — {calc['stock_desc']}, {calc['size_str']}")

    def on_copy(self):
        txt = self.txt_res.get("1.0", "end").strip()
        if not txt:
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        messagebox.showinfo("Скопировано", "Результат скопирован в буфер обмена.")