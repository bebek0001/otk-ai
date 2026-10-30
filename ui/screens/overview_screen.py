# -*- coding: utf-8 -*-
"""
ui/screens/overview_screen.py — вкладка «Обзор».

Три части для показа заказчику и для разбора:
  * Разбор файла: что программа прочитала из CDW / M3D / PDF и по каким шагам
    пришла к ответу. Отчёт можно сохранить в текстовый файл.
  * База эталонов: какие чертежи лежат в базе, поиск, выгрузка в Excel.
  * Алгоритмы: порядок решения с действующими параметрами, припуски, статистика агента.
"""

from __future__ import annotations

import os
import threading
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from core import logger, overview
from ui import thread_bridge

_FILETYPES = [("Чертежи и модели", "*.cdw *.CDW *.m3d *.M3D *.a3d *.A3D *.pdf *.PDF"),
              ("Все файлы", "*.*")]


def _is_dark() -> bool:
    try:
        return ctk.get_appearance_mode().lower() == "dark"
    except Exception:                                            # noqa: BLE001
        return True


class OverviewScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Обзор", font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self._tab_file = self.tabs.add("Разбор файла")
        self._tab_base = self.tabs.add("База эталонов")
        self._tab_algo = self.tabs.add("Алгоритмы")

        self._report = ""
        self._build_file_tab()
        self._build_base_tab()
        self._build_algo_tab()
        self._refresh_base()
        self._refresh_algo()

    # ------------------------------------------------------------------ #
    #  Разбор файла
    # ------------------------------------------------------------------ #
    def _build_file_tab(self):
        t = self._tab_file
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(t, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        ctk.CTkButton(bar, text="Выбрать файл…", width=140, command=self._pick_file).pack(side="left")
        self.btn_save = ctk.CTkButton(bar, text="Сохранить отчёт", width=150, state="disabled",
                                      command=self._save_report)
        self.btn_save.pack(side="left", padx=8)
        self.lbl_file = ctk.CTkLabel(bar, text="Выберите CDW, M3D или PDF: программа покажет, "
                                               "что она видит и как приходит к ответу",
                                     text_color=("gray40", "#A7A7A7"), anchor="w")
        self.lbl_file.pack(side="left", padx=8)

        self.txt_file = ctk.CTkTextbox(t, font=ctk.CTkFont(family="Courier", size=12), wrap="word",
                                       state="disabled")
        self.txt_file.grid(row=1, column=0, sticky="nsew")
        self.txt_file.tag_config("h", foreground="#7aa2f7")
        self.txt_file.tag_config("warn", foreground="#ffd93d")

    def _pick_file(self):
        path = filedialog.askopenfilename(title="Файл для разбора", filetypes=_FILETYPES)
        if not path:
            return
        self.lbl_file.configure(text=f"Разбор: {os.path.basename(path)} …")
        self.btn_save.configure(state="disabled")
        threading.Thread(target=self._work_file, args=(path,), daemon=True).start()

    def _work_file(self, path: str):
        try:
            from ui.screens.batch_tool import process_one_file
            info = overview.read_file(path)
            tr = overview.run_trace(path, lambda p: process_one_file(p, use_ai=False))
            thread_bridge.post(self._show_file, path, info, tr)
        except Exception as e:                                   # noqa: BLE001
            logger.error("Обзор: разбор файла не удался", e)
            thread_bridge.post(self.lbl_file.configure, text=f"Ошибка: {e}")

    def _show_file(self, path, info, tr):
        name = os.path.basename(path)
        self._report = overview.report_text(info, tr, name)
        self.lbl_file.configure(text=f"{name}  ·  {info.get('kind', '')}")
        self.btn_save.configure(state="normal")
        tb = self.txt_file
        tb.configure(state="normal")
        tb.delete("1.0", "end")

        def head(s):
            tb.insert("end", s + "\n", "h")

        if info.get("error"):
            tb.insert("end", f"Ошибка чтения: {info['error']}\n\n", "warn")
        head("1. Что программа прочитала")
        w = max((len(k) for k, _ in info.get("fields", [])), default=10)
        for k, v in info.get("fields", []):
            tb.insert("end", f"  {k.ljust(w)}  {v or '-'}\n")
        if info.get("geometry"):
            tb.insert("end", "\n  Геометрия\n", "h")
            for g in info["geometry"]:
                tb.insert("end", f"  {g}\n")
        head("\n2. Как принято решение")
        for i, (st, msg) in enumerate(tr["steps"], 1):
            tb.insert("end", f"  {i}. {st}: {msg}\n")
        head("\n3. Итог")
        w2 = max(len(k) for k, _ in overview.result_lines(tr["result"]))
        for k, v in overview.result_lines(tr["result"]):
            tb.insert("end", f"  {k.ljust(w2)}  {v or '-'}\n")
        tb.configure(state="disabled")

    def _save_report(self):
        if not self._report:
            return
        path = filedialog.asksaveasfilename(title="Сохранить отчёт", defaultextension=".txt",
                                            initialfile="otk_ai_razbor.txt", filetypes=[("Текст", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._report)
            logger.info(f"Отчёт по файлу сохранён: {path}")
        except Exception as e:                                   # noqa: BLE001
            messagebox.showerror("Отчёт", f"Не удалось сохранить: {e}")

    # ------------------------------------------------------------------ #
    #  База эталонов
    # ------------------------------------------------------------------ #
    def _build_base_tab(self):
        t = self._tab_base
        t.grid_rowconfigure(2, weight=1)
        t.grid_columnconfigure(0, weight=1)

        self.lbl_base = ctk.CTkLabel(t, text="", anchor="w", justify="left",
                                     text_color=("gray40", "#A7A7A7"))
        self.lbl_base.grid(row=0, column=0, sticky="ew", pady=(4, 6))

        bar = ctk.CTkFrame(t, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(bar, text="Поиск:").pack(side="left")
        self.var_q = ctk.StringVar()
        self.var_q.trace_add("write", lambda *_: self._fill_tree())
        ctk.CTkEntry(bar, textvariable=self.var_q, width=240).pack(side="left", padx=(6, 10))
        self.var_type = ctk.StringVar(value="Все")
        self.opt_type = ctk.CTkOptionMenu(bar, values=["Все"], variable=self.var_type, width=150,
                                          command=lambda _: self._fill_tree())
        self.opt_type.pack(side="left")
        ctk.CTkButton(bar, text="Обновить", width=100, command=self._refresh_base).pack(side="left", padx=10)
        ctk.CTkButton(bar, text="Выгрузить в Excel", width=150, command=self._export_base).pack(side="left")

        frame = ctk.CTkFrame(t, fg_color="transparent")
        frame.grid(row=2, column=0, sticky="nsew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        dark = _is_dark()
        bg, fg, hd = ("#141416", "#e6e6e6", "#1f1f22") if dark else ("#ffffff", "#111111", "#e8e8e8")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:                                        # noqa: BLE001
            pass
        style.configure("Otk.Treeview", background=bg, fieldbackground=bg, foreground=fg,
                        rowheight=24, borderwidth=0)
        style.configure("Otk.Treeview.Heading", background=hd, foreground=fg, relief="flat")
        style.map("Otk.Treeview", background=[("selected", "#2b4c8c")], foreground=[("selected", "#ffffff")])

        cols = [k for k, _ in overview.BASE_COLUMNS]
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", style="Otk.Treeview")
        widths = {"drawing_no": 150, "part_name": 170, "stock_type": 90, "sortament": 260,
                  "size": 170, "mass_blank": 90, "mass_clean": 90, "origin": 130, "saved_at": 90}
        for k, title in overview.BASE_COLUMNS:
            self.tree.heading(k, text=title)
            self.tree.column(k, width=widths.get(k, 100), anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self._all_rows: list = []
        self._shown_rows: list = []

    def _refresh_base(self):
        try:
            self._all_rows = overview.base_rows()
        except Exception as e:                                   # noqa: BLE001
            logger.error("Обзор: база не прочитана", e)
            self._all_rows = []
        s = overview.base_summary(self._all_rows)
        types = ", ".join(f"{k} {v}" for k, v in list(s["by_type"].items())[:8])
        origin = ", ".join(f"{k}: {v}" for k, v in s["by_origin"].items())
        self.lbl_base.configure(
            text=f"В базе {s['total']} чертежей ({origin}). С чистой массой: {s['with_clean_mass']}.\n"
                 f"По типам проката: {types or '-'}")
        self.opt_type.configure(values=["Все"] + list(s["by_type"].keys()))
        self._fill_tree()

    def _fill_tree(self):
        self._shown_rows = overview.filter_rows(self._all_rows, self.var_q.get(), self.var_type.get())
        self.tree.delete(*self.tree.get_children())
        keys = [k for k, _ in overview.BASE_COLUMNS]
        for r in self._shown_rows:
            self.tree.insert("", "end", values=[("" if r[k] in (None, "") else r[k]) for k in keys])

    def _export_base(self):
        path = filedialog.asksaveasfilename(title="Выгрузить базу", defaultextension=".xlsx",
                                            initialfile="otk_ai_baza.xlsx", filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            overview.export_base_xlsx(path, self._shown_rows)
            logger.info(f"База выгружена: {path} ({len(self._shown_rows)} строк)")
        except Exception as e:                                   # noqa: BLE001
            messagebox.showerror("База", f"Не удалось сохранить: {e}")

    # ------------------------------------------------------------------ #
    #  Алгоритмы
    # ------------------------------------------------------------------ #
    def _build_algo_tab(self):
        t = self._tab_algo
        t.grid_rowconfigure(1, weight=1)
        t.grid_columnconfigure(0, weight=1)
        bar = ctk.CTkFrame(t, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        ctk.CTkButton(bar, text="Обновить", width=100, command=self._refresh_algo).pack(side="left")
        self.txt_algo = ctk.CTkTextbox(t, font=ctk.CTkFont(family="Courier", size=12), wrap="word",
                                       state="disabled")
        self.txt_algo.grid(row=1, column=0, sticky="nsew")
        self.txt_algo.tag_config("h", foreground="#7aa2f7")

    def _refresh_algo(self):
        tb = self.txt_algo
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        try:
            for line in overview.pipeline_text():
                tb.insert("end", line + "\n\n")
            tb.insert("end", "Припуск по длине, мм (по типу проката)\n", "h")
            tb.insert("end", f"  {'Тип':<14}{'технолог':<16}{'база':<16}{'действует':<12}источник\n")
            for r in overview.allowance_table():
                tb.insert("end", f"  {r['kind']:<14}{r['tech_n']} шт, {r['tech_med']:<8}"
                                 f"{r['base_n']} шт, {r['base_med']:<8}{r['value']:<12}{r['source']}\n")
            tb.insert("end", "\nУровни доверия агента\n", "h")
            tb.insert("end", f"  {'Правило':<16}{'ответов':<10}{'принято':<10}{'нижняя гр.':<12}уровень\n")
            for r in overview.agent_table():
                tb.insert("end", f"  {r['rule']:<16}{r['n']:<10}{r['correct']:<10}{r['lower']:<12}{r['level']}\n")
        except Exception as e:                                   # noqa: BLE001
            tb.insert("end", f"Не удалось собрать данные: {e}\n")
        tb.configure(state="disabled")
