import os
import re
import threading
import customtkinter as ctk
from tkinter import filedialog, messagebox

from core.engine import (
    fitz,
    Extracted,
    CalcResult,
    normalize_mark,
    normalize_gost,
    safe_float,
    extract_from_pdf,
    calculate_missing_stock_line,
    find_exact_stock_mentions,
    build_gost_sources_block,
    open_with_default_app,
    mass_str_ru,
    ai_ask,
)

from core.settings import load_settings
from core.allowances import ALLOWANCE_METHODOLOGY_TEXT
from core.drawing_db import lookup_etalon, save_etalon, db_stats, build_etalon_examples_block
from core import logger


class DrawingToolScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.pdf_path: str | None = None
        self.extracted: Extracted | None = None
        self.last_result: CalcResult | None = None
        self.missing_line: str | None = None
        self._from_db: bool = False  # флаг: результат из базы или из расчёта

        self._build_ui()

    def _build_ui(self):
        # Top bar
        top = ctk.CTkFrame(self, corner_radius=12, fg_color=("gray92", "#141416"), border_width=1, border_color=("gray80", "#222226"))
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 10))
        top.grid_columnconfigure(0, weight=1)

        self.lbl_pdf = ctk.CTkLabel(top, text="PDF не выбран", text_color=("gray40", "#A7A7A7"))
        self.lbl_pdf.grid(row=0, column=0, sticky="w", padx=16, pady=14)

        ctk.CTkButton(top, text="Выбрать PDF…", height=36, corner_radius=12, fg_color=("gray88","gray25"), hover_color=("gray80","gray30"), text_color=("gray10","gray90"),
            command=self.on_pick_pdf).grid(
            row=0, column=1, padx=16, pady=12, sticky="e"
        )

        # Inputs card
        card = ctk.CTkFrame(self, corner_radius=12, fg_color=("gray92", "#141416"), border_width=1, border_color=("gray80", "#222226"))
        card.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        card.grid_columnconfigure((0, 1, 2, 3), weight=1)

        ctk.CTkLabel(card, text="Данные из чертежа (штамп) + ввод", font=ctk.CTkFont(size=12, weight="bold"), text_color=("gray45", "#666676")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(14, 8)
        )

        ctk.CTkLabel(card, text="Марка материала (штамп):", text_color=("gray40", "#A7A7A7")).grid(
            row=1, column=0, sticky="w", padx=16, pady=(6, 6)
        )
        self.var_mark = ctk.StringVar(value="")
        self.ent_mark = ctk.CTkEntry(card, textvariable=self.var_mark, height=34, corner_radius=12)
        self.ent_mark.grid(row=1, column=1, sticky="ew", padx=8, pady=6)

        ctk.CTkLabel(card, text="ГОСТ материала (штамп):", text_color=("gray40", "#A7A7A7")).grid(
            row=1, column=2, sticky="w", padx=(16, 0), pady=6
        )
        self.var_mat_gost = ctk.StringVar(value="")
        self.ent_gost = ctk.CTkEntry(card, textvariable=self.var_mat_gost, height=34, corner_radius=12)
        self.ent_gost.grid(row=1, column=3, sticky="ew", padx=8, pady=6)

        ctk.CTkLabel(card, text="Размеры из PDF (инфо):", text_color=("gray40", "#A7A7A7")).grid(
            row=2, column=0, sticky="w", padx=16, pady=6
        )
        self.var_dim_info = ctk.StringVar(value="(не считано)")
        self.ent_dim = ctk.CTkEntry(card, textvariable=self.var_dim_info, height=34, corner_radius=12, state="readonly")
        self.ent_dim.grid(row=2, column=1, columnspan=3, sticky="ew", padx=8, pady=6)

        ctk.CTkLabel(card, text="Длина детали L, мм (ввод, можно пусто):", text_color=("gray40", "#A7A7A7")).grid(
            row=3, column=0, sticky="w", padx=16, pady=(6, 14)
        )
        self.var_l = ctk.StringVar(value="")
        self.ent_l = ctk.CTkEntry(card, textvariable=self.var_l, height=34, corner_radius=12)
        self.ent_l.grid(row=3, column=1, sticky="ew", padx=8, pady=(6, 14))

        # Buttons row
        btns = ctk.CTkFrame(self, corner_radius=12, fg_color=("gray92", "#141416"), border_width=1, border_color=("gray80", "#222226"))
        btns.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))

        self.btn_read = ctk.CTkButton(
            btns, text="Считать из PDF", height=36, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=("gray70", "#2a2a35"), hover_color=("gray82", "#1a1a1f"), text_color=("gray10", "gray90"),
            command=self.on_read_pdf, state="disabled"
        )
        self.btn_read.pack(side="left", padx=12, pady=12)

        self.btn_calc = ctk.CTkButton(
            btns, text="Найти недостающую заготовку", height=36, corner_radius=10,
            fg_color=("gray70", "gray30"), hover_color=("gray65", "gray35"), text_color=("gray10", "gray90"),
            command=self.on_calculate, state="disabled"
        )
        self.btn_calc.pack(side="left", padx=8, pady=12)

        self.btn_ai = ctk.CTkButton(
            btns, text="ИИ: подтвердить (по ГОСТ)", height=36, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=("gray70", "#2a2a35"), hover_color=("gray82", "#1a1a1f"), text_color=("gray10", "gray90"),
            command=self.on_ai, state="disabled"
        )
        self.btn_ai.pack(side="left", padx=8, pady=12)

        self.btn_open_gost = ctk.CTkButton(
            btns, text="Открыть ГОСТ сортамента", height=36, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=("gray70", "#2a2a35"), hover_color=("gray82", "#1a1a1f"), text_color=("gray10", "gray90"),
            command=self.on_open_gost, state="disabled"
        )
        self.btn_open_gost.pack(side="left", padx=8, pady=12)

        self.btn_copy = ctk.CTkButton(
            btns, text="Копировать результат", height=36, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=("gray70", "#2a2a35"), hover_color=("gray82", "#1a1a1f"), text_color=("gray10", "gray90"),
            command=self.on_copy, state="disabled"
        )
        self.btn_copy.pack(side="left", padx=8, pady=12)

        # ← НОВАЯ кнопка "Сохранить эталон"
        self.btn_save_etalon = ctk.CTkButton(
            btns, text="Сохранить эталон", height=36, corner_radius=10,
            fg_color="#16a34a", hover_color="#15803d", text_color=("gray10", "#ffffff"),
            command=self.on_save_etalon, state="disabled"
        )
        self.btn_save_etalon.pack(side="left", padx=8, pady=12)

        # Output split
        out = ctk.CTkFrame(self, corner_radius=12, fg_color=("gray92", "#141416"), border_width=1, border_color=("gray80", "#222226"))
        out.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 12))
        out.grid_rowconfigure(0, weight=1)
        out.grid_columnconfigure((0, 1), weight=1)

        left = ctk.CTkFrame(out, corner_radius=10, fg_color=("gray95", "#0f0f11"), border_width=1, border_color=("gray80", "#222226"))
        right = ctk.CTkFrame(out, corner_radius=10, fg_color=("gray95", "#0f0f11"), border_width=1, border_color=("gray80", "#222226"))
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        left.grid_rowconfigure(1, weight=1)
        right.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(left, text="Что отсутствует на чертеже", font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray45", "#666676")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 8)
        )
        self.txt_result = ctk.CTkTextbox(left, corner_radius=8, fg_color=("gray95", "#141416"), font=ctk.CTkFont(family="Courier", size=13))
        self.txt_result.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        ctk.CTkLabel(right, text="Протокол", font=ctk.CTkFont(size=11, weight="bold"), text_color=("gray45", "#666676")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 8)
        )
        self.txt_protocol = ctk.CTkTextbox(right, corner_radius=8, fg_color=("gray95", "#141416"), font=ctk.CTkFont(family="Courier", size=12))
        self.txt_protocol.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

    def _clear_output(self):
        self.txt_result.delete("1.0", "end")
        self.txt_protocol.delete("1.0", "end")
        self.btn_copy.configure(state="disabled")
        self.btn_open_gost.configure(state="disabled")
        self.btn_ai.configure(state="disabled")
        self.btn_save_etalon.configure(state="disabled")

    def on_pick_pdf(self):
        path = filedialog.askopenfilename(title="Выбери PDF чертёж", filetypes=[("PDF files", "*.pdf")])
        if not path:
            return

        self.pdf_path = path
        self.lbl_pdf.configure(text=os.path.basename(path), text_color=("gray10", "white"))
        logger.info(f"Выбран PDF: {os.path.basename(path)}")

        self.btn_read.configure(state="normal")
        self.btn_calc.configure(state="normal")

        self.extracted = None
        self.last_result = None
        self.missing_line = None
        self._from_db = False
        self._clear_output()
        self.var_dim_info.set("(не считано)")

    def on_read_pdf(self):
        if not self.pdf_path:
            return
        if fitz is None:
            messagebox.showerror("Нет PyMuPDF", "Установи: python3 -m pip install pymupdf")
            return

        try:
            self.extracted = extract_from_pdf(self.pdf_path, max_pages=2)
        except Exception as e:
            logger.error("Ошибка чтения PDF", e)
            messagebox.showerror("Ошибка чтения PDF", str(e))
            return

        if self.extracted.material_mark:
            self.var_mark.set(self.extracted.material_mark)
        if self.extracted.material_gost:
            self.var_mat_gost.set(self.extracted.material_gost)

        diams = [f.d_mm for f in self.extracted.diameter_features]
        sqs = self.extracted.square_features_mm
        l = self.extracted.length_mm
        m = self.extracted.stamp_mass_kg

        parts = []
        if diams:
            parts.append("Ø: " + ", ".join(str(int(d)) if abs(d - int(d)) < 1e-6 else str(d) for d in sorted(set(diams))))
        if sqs:
            parts.append("*: " + ", ".join(str(int(s)) if abs(s - int(s)) < 1e-6 else str(s) for s in sorted(set(sqs))))
        if l is not None:
            parts.append(f"L(из PDF)={l}")
        if m is not None:
            parts.append(f"Масса(штамп)={m}")

        self.var_dim_info.set("; ".join(parts) if parts else "(размеры не распознаны)")
        logger.info(f"PDF считан: {'; '.join(parts) if parts else 'размеры не распознаны'}")

        # ← ПРОВЕРЯЕМ БАЗУ после считывания
        etalon = lookup_etalon(self.extracted.pdf_text, self.extracted.part_name)
        if etalon:
            info = (
                f"Этот чертёж найден в базе эталонов!\n\n"
                f"Деталь: {etalon.get('part_name', '—')}\n"
                f"№ чертежа: {etalon.get('drawing_no', '—')}\n"
                f"Заготовка: {etalon.get('result_line', '—')}\n\n"
                f"Нажми «Найти недостающую заготовку» — результат будет из базы."
            )
            messagebox.showinfo("Найден в базе ✓", info)
            self.txt_protocol.delete("1.0", "end")
            self.txt_protocol.insert("1.0", f"[БАЗА] Эталон найден:\n{info}")
        else:
            messagebox.showinfo("Готово", "Данные считаны. Если L пустое — возьмём длину из PDF (если нашли).")

    def _build_search_descr(self, res: CalcResult) -> str:
        if res.stock_type == "Круг":
            return f"Круг {res.d_blank_std_mm}"
        if res.stock_type == "Квадрат":
            return f"Квадрат {int(round(res.square_mm))}" if res.square_mm is not None else "Квадрат"
        if res.stock_type == "Труба":
            if res.tube_od_mm is not None and res.tube_wall_mm is not None:
                return f"Труба {int(round(res.tube_od_mm))}x{int(round(res.tube_wall_mm))}"
            return "Труба"
        if res.stock_type in {"ЛистХК", "ЛистГК"}:
            m = re.search(r"Лист\s*(\d+)", res.result_line, flags=re.IGNORECASE)
            if m:
                return f"Лист {m.group(1)}"
            return "Лист"
        return res.stock_type

    def on_calculate(self):
        if not self.pdf_path:
            messagebox.showwarning("Нет файла", "Сначала выбери PDF.")
            return

        if self.extracted is None:
            try:
                self.extracted = extract_from_pdf(self.pdf_path, max_pages=2)
            except Exception as e:
                logger.error("Ошибка чтения PDF при расчёте", e)
                messagebox.showerror("Ошибка чтения PDF", str(e))
                return

        # ← СНАЧАЛА ПРОВЕРЯЕМ БАЗУ ЭТАЛОНОВ
        etalon = lookup_etalon(self.extracted.pdf_text, self.extracted.part_name)
        if etalon:
            logger.info(f"Результат из базы эталонов: {etalon.get('part_name')}")
            self._from_db = True
            result_line = etalon.get("result_line", "")
            self.missing_line = f"[ИЗ БАЗЫ ЭТАЛОНОВ]\nЧертёж: {etalon.get('drawing_no', '—')} / {etalon.get('part_name', '—')}\n\nНЕТ НА ЧЕРТЕЖЕ:\n\n{result_line}"

            protocol = (
                f"Протокол: ЭТАЛОН ИЗ БАЗЫ\n"
                f"- Деталь: {etalon.get('part_name', '—')}\n"
                f"- № чертежа: {etalon.get('drawing_no', '—')}\n"
                f"- Тип заготовки: {etalon.get('stock_type', '—')}\n"
                f"- ГОСТ сортамента: {etalon.get('gost_stock', '—')}\n"
                f"- Размер: Ø{etalon.get('d_blank_std_mm', '—')} мм\n"
                f"- L детали: {etalon.get('l_part_mm', '—')} мм\n"
                f"- L заготовки: {etalon.get('l_blank_mm', '—')} мм\n"
                f"- Материал: {etalon.get('material_mark', '—')} {etalon.get('material_gost', '—')}\n"
                f"- Масса: {etalon.get('mass_kg', '—')} кг\n"
                f"- Сохранён: {etalon.get('saved_at', '—')}\n"
            )

            self.txt_result.delete("1.0", "end")
            self.txt_result.insert("1.0", self.missing_line)
            self.txt_protocol.delete("1.0", "end")
            self.txt_protocol.insert("1.0", protocol)

            self.btn_copy.configure(state="normal")
            self.btn_ai.configure(state="normal")
            self.btn_save_etalon.configure(state="disabled")  # уже в базе
            return

        # ← БАЗЫ НЕТ — считаем по обычной логике
        self._from_db = False

        mark = normalize_mark(self.var_mark.get())
        mat_gost = normalize_gost(self.var_mat_gost.get())

        l_part = safe_float(self.var_l.get())
        if l_part is None:
            l_part = self.extracted.length_mm

        if not mark:
            messagebox.showwarning("Не хватает данных", "Не найдена/не введена марка материала из штампа.")
            return
        if not mat_gost:
            messagebox.showwarning("Не хватает данных", "Не найден/не введён ГОСТ материала из штампа.")
            return
        if l_part is None or l_part <= 0:
            messagebox.showwarning("Не хватает данных", "Не найдена длина L. Введи L вручную.")
            return

        logger.info(f"Запуск расчёта: марка={mark}, ГОСТ={mat_gost}, L={l_part}")

        try:
            res = calculate_missing_stock_line(self.extracted, l_part, mark, mat_gost)
        except Exception as e:
            logger.error("Ошибка подбора заготовки", e)
            messagebox.showerror("Ошибка подбора", str(e))
            return

        self.last_result = res

        descr = self._build_search_descr(res)
        mentions = find_exact_stock_mentions(self.extracted.pdf_text, descr)

        if mentions:
            self.missing_line = "ПРОВЕРЬ: на чертеже найдено упоминание похожей заготовки.\n\n" + res.result_line
        else:
            self.missing_line = "НЕТ НА ЧЕРТЕЖЕ:\n\n" + res.result_line

        self.txt_result.delete("1.0", "end")
        self.txt_result.insert("1.0", self.missing_line)

        self.txt_protocol.delete("1.0", "end")
        self.txt_protocol.insert("1.0", res.protocol)

        self.btn_copy.configure(state="normal")
        self.btn_open_gost.configure(state="normal" if res.gost_pdf_path else "disabled")
        self.btn_ai.configure(state="normal")
        self.btn_save_etalon.configure(state="normal")  # ← разблокируем сохранение

    def on_save_etalon(self):
        """Сохраняет текущий результат в базу эталонов."""
        if not self.extracted or not self.last_result:
            messagebox.showwarning("Нет данных", "Сначала выполни расчёт.")
            return

        res = self.last_result
        ex = self.extracted

        try:
            record = save_etalon(
                pdf_text=ex.pdf_text,
                part_name=ex.part_name,
                stock_type=res.stock_type,
                gost_stock=res.gost_stock,
                d_blank_std_mm=res.d_blank_std_mm,
                l_part_mm=res.l_part_mm,
                l_blank_mm=res.l_blank_mm,
                material_mark=res.material_out,
                material_gost=res.material_gost,
                mass_kg=res.mass_kg,
                result_line=res.result_line,
            )
            stats = db_stats()
            msg = (
                f"Эталон сохранён!\n\n"
                f"Деталь: {record['part_name']}\n"
                f"№ чертежа: {record['drawing_no']}\n"
                f"Заготовка: {record['result_line']}\n\n"
                f"Всего в базе: {stats['total']} чертежей"
            )
            messagebox.showinfo("Сохранено ✓", msg)
            self.btn_save_etalon.configure(state="disabled", text="Эталон сохранён", fg_color="#0f4c27")
            logger.info(f"Эталон сохранён: {record['drawing_no']} / {record['part_name']}")
        except Exception as e:
            messagebox.showerror("Ошибка сохранения", str(e))
            logger.error("Ошибка сохранения эталона", e)

    def on_ai(self):
        if not self.extracted or not self.last_result:
            return

        res = self.last_result
        sources_block = build_gost_sources_block(res, self.extracted)

        descr = self._build_search_descr(res)
        mentions = find_exact_stock_mentions(self.extracted.pdf_text, descr)
        mentions_block = "\n".join(f"- {m}" for m in mentions) if mentions else "(не найдено совпадений в тексте чертежа)"

        missing_text = self.missing_line or ("НЕТ НА ЧЕРТЕЖЕ:\n\n" + res.result_line)

        mass_str = mass_str_ru(res.mass_kg)
        l_blank = int(res.l_blank_mm)

        ctrl = [f"тип={res.stock_type}", f"L={l_blank}", f"m={mass_str} кг"]
        if res.stock_type == "Круг":
            ctrl.append(f"D={res.d_blank_std_mm} мм")
        if res.stock_type == "Труба" and res.tube_od_mm and res.tube_wall_mm:
            ctrl.append(f"ODxS={int(round(res.tube_od_mm))}x{int(round(res.tube_wall_mm))}")
        if res.stock_type == "Квадрат" and res.square_mm:
            ctrl.append(f"a={int(round(res.square_mm))} мм")

        settings = load_settings()
        base_prompt = settings.get("ai_prompt_pdf", "").strip() or "Проверь по фактам и ответь кратко."

        # Примеры из базы эталонов — обучаем ИИ на реальных случаях
        etalon_examples = build_etalon_examples_block(res.stock_type, res.material_out)

        prompt = (
            base_prompt + "\n\n"
            f"Проверяемая выдача приложения:\n{missing_text}\n\n"
            "Найденные совпадения в тексте чертежа:\n"
            f"{mentions_block}\n\n"
            f"Контрольные данные: {', '.join(ctrl)}\n\n"
            + (f"{etalon_examples}\n" if etalon_examples else "")
            + f"{sources_block}\n\n"
            f"{ALLOWANCE_METHODOLOGY_TEXT}\n"
        )

        logger.info(f"Отправка запроса ИИ (PDF), длина промпта: {len(prompt)} символов")
        self.btn_ai.configure(state="disabled")
        self.txt_protocol.insert("end", "\n\n[ИИ] Формальная проверка...\n")
        self.txt_protocol.see("end")

        def worker():
            try:
                answer = ai_ask(prompt)
                logger.info("Ответ ИИ (PDF) получен")
            except Exception as e:
                answer = f"[ИИ] Ошибка: {e}"
                logger.error("Ошибка ИИ (PDF)", e)

            def ui_update():
                self.txt_protocol.insert("end", "\n[ИИ] Подтверждение:\n" + answer + "\n")
                self.txt_protocol.see("end")
                self.btn_ai.configure(state="normal")

            self.after(0, ui_update)

        threading.Thread(target=worker, daemon=True).start()

    def on_open_gost(self):
        if self.last_result and self.last_result.gost_pdf_path:
            try:
                open_with_default_app(self.last_result.gost_pdf_path)
            except Exception as e:
                messagebox.showerror("Не открылось", str(e))

    def on_copy(self):
        if not self.missing_line:
            return
        self.clipboard_clear()
        self.clipboard_append(self.missing_line)
        messagebox.showinfo("Скопировано", "Результат скопирован в буфер обмена.")