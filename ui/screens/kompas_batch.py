# -*- coding: utf-8 -*-
"""
ui/screens/kompas_batch.py — блок пакетной обработки M3D на вкладке «КОМПАС».

КОМПАС запускается скрыто, на экране ничего не показывается: только счётчик
«обработано N из M». Если КОМПАС на компьютере нет (или модель не отдала
габарит), файл читается по своему содержимому, так что пакет работает и там,
и там; в таблице у каждой строки виден источник («КОМПАС» или «M3D»).
"""

from __future__ import annotations

import datetime
import os
import threading
import customtkinter as ctk
from tkinter import filedialog, messagebox

from ui import thread_bridge
from core import logger


class KompasBatchPanel(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(
            master, corner_radius=12, fg_color=("gray92", "#141416"),
            border_width=1, border_color=("gray80", "#222226"),
        )
        self.grid_columnconfigure(0, weight=1)
        self._files: list = []
        self._results: list = []
        self._busy = False
        self._build()

    # ---------- интерфейс ----------

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        head.grid_columnconfigure(1, weight=1)

        self._dot = ctk.CTkFrame(head, width=10, height=10, corner_radius=5,
                                 fg_color="#9E9E9E")
        self._dot.grid(row=0, column=0, padx=(0, 8))
        self._dot.grid_propagate(False)
        self._mode = ctk.CTkLabel(
            head, text="Пакетная обработка M3D: проверка КОМПАС…", anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"))
        self._mode.grid(row=0, column=1, sticky="w")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 4))
        self._btn_add = ctk.CTkButton(bar, text="Добавить M3D", width=130, height=32,
                                      corner_radius=10, command=self.on_add)
        self._btn_add.pack(side="left", padx=(0, 8))
        self._btn_clear = ctk.CTkButton(
            bar, text="Очистить", width=90, height=32, corner_radius=10,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), command=self.on_clear)
        self._btn_clear.pack(side="left", padx=(0, 8))
        self._btn_run = ctk.CTkButton(
            bar, text="Обработать", width=130, height=32, corner_radius=10,
            fg_color="#2E7D32", hover_color="#1B5E20", command=self.on_run)
        self._btn_run.pack(side="left", padx=(0, 8))
        self._btn_xlsx = ctk.CTkButton(
            bar, text="Сохранить в Excel", width=150, height=32, corner_radius=10,
            state="disabled", command=self.on_export)
        self._btn_xlsx.pack(side="left")
        self._count = ctk.CTkLabel(bar, text="Файлов: 0", anchor="e",
                                   text_color=("gray40", "#A7A7A7"))
        self._count.pack(side="right")

        self._progress = ctk.CTkProgressBar(self, height=6)
        self._progress.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 2))
        self._progress.set(0)
        self._state = ctk.CTkLabel(self, text="", anchor="w",
                                   text_color=("gray40", "#A7A7A7"))
        self._state.grid(row=3, column=0, sticky="ew", padx=16)

        self._table = ctk.CTkTextbox(self, height=170,
                                     font=ctk.CTkFont(family="Courier", size=12))
        self._table.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 12))
        self._table.configure(state="disabled")

    def _write(self, text: str):
        self._table.configure(state="normal")
        self._table.delete("1.0", "end")
        self._table.insert("1.0", text)
        self._table.configure(state="disabled")

    # ---------- статус КОМПАС ----------

    def set_status(self, connected: bool, api: str = "", error: str = ""):
        """Статус приходит от вкладки (она проверяет КОМПАС сама). Панель к COM не обращается."""
        if connected:
            self._dot.configure(fg_color="#4CAF50")
            self._mode.configure(
                text=f"КОМПАС виден ({api}). Пакет откроет свой экземпляр скрыто "
                     f"и возьмёт габариты из КОМПАС")
        else:
            self._dot.configure(fg_color="#F9A825")
            self._mode.configure(
                text="КОМПАС сейчас не запущен: пакет попробует запустить его скрыто, "
                     "а если не получится, прочитает файлы по содержимому")

    # ---------- файлы ----------

    def on_add(self):
        paths = filedialog.askopenfilenames(
            title="Выберите модели",
            filetypes=[("Модели КОМПАС", "*.m3d *.a3d"), ("Все файлы", "*.*")])
        for p in paths:
            if p not in self._files:
                self._files.append(p)
        self._count.configure(text=f"Файлов: {len(self._files)}")

    def on_clear(self):
        if self._busy:
            return
        self._files, self._results = [], []
        self._count.configure(text="Файлов: 0")
        self._btn_xlsx.configure(state="disabled")
        self._progress.set(0)
        self._state.configure(text="")
        self._write("")

    # ---------- обработка ----------

    def on_run(self):
        if self._busy:
            return
        if not self._files:
            messagebox.showwarning("Нет файлов", "Сначала добавьте модели M3D.")
            return
        self._busy = True
        self._results = []
        for b in (self._btn_add, self._btn_clear, self._btn_run, self._btn_xlsx):
            b.configure(state="disabled")
        self._progress.set(0)
        threading.Thread(target=self._worker, args=(list(self._files),),
                         daemon=True).start()

    def _worker(self, files: list):
        from ui.screens import batch_tool as bt
        from integrations.kompas import batch as kb
        from integrations.kompas.client import KompasWorker
        total = len(files)
        used = {"КОМПАС": 0, "файл": 0}
        try:
            kc = KompasWorker()
            kompas_ok = False
            try:
                info = kc.detect()
                kompas_ok = bool(info.get("installed"))
                if not kompas_ok:
                    thread_bridge.post(
                        self._state.configure,
                        {"text": info.get("error") or info.get("note")
                         or "КОМПАС не найден: читаю по содержимому файлов"})
                for i, path in enumerate(files):
                    thread_bridge.post(
                        self._state.configure,
                        {"text": f"Обработано {i} из {total}"})
                    thread_bridge.post(self._progress.set, i / total)
                    body = None
                    if kompas_ok:
                        resp = kc.body(path)
                        dims = resp.get("dims")
                        body = tuple(dims) if dims else None
                        kb.log_result(path, kb.BodyResult(
                            dims=body, error=resp.get("error", ""),
                            attempts=resp.get("attempts") or []))
                    try:
                        rows = bt.process_files(path, use_ai=False, body=body)
                    except Exception as e:                     # noqa: BLE001
                        logger.error(f"Пакет КОМПАС: {path}: {e}")
                        r = bt._blank_result(path)
                        r["status"], r["error"] = "❌ ошибка", str(e)[:120]
                        rows = [r]
                    for r in rows:
                        used["КОМПАС" if r.get("source") == "КОМПАС" else "файл"] += 1
                    self._results.extend(rows)
            finally:
                kc.close()
        except Exception as e:                                 # noqa: BLE001
            logger.error(f"Пакет КОМПАС: {e}")
        thread_bridge.post(self._finish, used, total)

    def _finish(self, used: dict, total: int):
        from ui.screens import batch_tool as bt
        self._results.sort(key=lambda r: bt._drawing_sort_key(r.get("drawing_no", "")))
        lines = [f"{'№':>3}  {'Обозначение':<20} {'Наименование':<18} "
                 f"{'Размер':<26} {'Заг.':>8} {'Чист.':>8}  Источник / замечание"]
        for n, r in enumerate(self._results, 1):
            note = r.get("source", "")
            if r.get("error"):
                note += f" | {r['error']}"
            lines.append(
                f"{n:>3}  {r.get('drawing_no', ''):<20} {r.get('part_name', '')[:18]:<18} "
                f"{r.get('stock_size', '')[:26]:<26} {r.get('stock_mass_kg', ''):>8} "
                f"{r.get('clean_mass_kg', ''):>8}  {note}")
        self._write("\n".join(lines))
        self._progress.set(1.0)
        ok = sum(1 for r in self._results if r.get("status") == "ОК")
        self._state.configure(
            text=f"Готово: файлов {total}, строк {len(self._results)}, без замечаний {ok}. "
                 f"Габарит из КОМПАС: {used['КОМПАС']}, по содержимому файла: {used['файл']}")
        for b in (self._btn_add, self._btn_clear, self._btn_run):
            b.configure(state="normal")
        if self._results:
            self._btn_xlsx.configure(state="normal")
        self._busy = False
        logger.info(f"Пакет КОМПАС завершён: {ok}/{len(self._results)} без замечаний")

    # ---------- Excel ----------

    def on_export(self):
        from ui.screens import batch_tool as bt
        if not self._results:
            return
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        path = filedialog.asksaveasfilename(
            title="Сохранить результаты", defaultextension=".xlsx",
            filetypes=[("Excel файл", "*.xlsx")], initialfile=f"OTK_заготовки_{now}.xlsx")
        if not path:
            return
        try:
            bt._export_client(self._results, path)
            messagebox.showinfo("Сохранено", f"Файл сохранён:\n{path}")
            logger.info(f"Экспорт в Excel (КОМПАС): {path}")
        except Exception as e:                                 # noqa: BLE001
            messagebox.showerror("Ошибка экспорта", str(e))
            logger.error(f"Ошибка экспорта: {e}")
