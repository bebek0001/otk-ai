# -*- coding: utf-8 -*-
"""
ui/screens/upload_tool.py — вкладка «Загрузка чертежей» (упрощённая).

Для технолога заказчика, который просто хочет закинуть свои чертежи и
получить ответ — без настроек режима папок, форматов экспорта и прочих
деталей «Пакетной обработки». Логика обработки та же (core/stock_ext,
core/cdw_bridge, core.confirm_queue) — здесь только упрощённый интерфейс:

    выбрать файлы/папку → обработка идёт сама → простой список результатов
    → при необходимости экспорт в Excel.

Прямого drag-and-drop из Finder/Проводника нет — Tkinter его не умеет
без отдельной библиотеки (tkinterdnd2), которой пока нет в проекте
(кросс-платформенная сборка с ней не проверена). Вместо этого — большая
кнопка «Выбрать файлы» и «Выбрать папку», что для пользователя
функционально то же самое (один клик — и файлы обрабатываются).

Ответы, угаданные RAG/Vision, автоматически уходят в очередь
подтверждения (core/confirm_queue.py) — эта вкладка сама в базу
эталонов ничего не пишет, как и «Пакетная обработка».
"""

from __future__ import annotations

import os
import threading
import customtkinter as ctk
from ui import thread_bridge
from tkinter import filedialog, messagebox

from ui.screens.batch_tool import (
    process_one_file,
    scan_folder,
    export_to_excel,
    SUPPORTED_PATTERNS,
)
from core import confirm_queue
from core import logger


class UploadToolScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._results: list[dict] = []
        self._busy = False
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────

    def _build_ui(self):
        top = ctk.CTkFrame(
            self, corner_radius=12, fg_color=("gray92", "#141416"),
            border_width=1, border_color=("gray80", "#222226"),
        )
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 10))
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top, text="Загрузка чертежей",
            font=ctk.CTkFont(size=20, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 2))

        ctk.CTkLabel(
            top,
            text="Выберите файлы или папку с чертежами (.pdf, .cdw, .m3d/.a3d) — "
                 "остальное приложение сделает само. Если ответ по какому-то "
                 "чертежу не посчитан по ГОСТ, а угадан — он попадёт во вкладку "
                 "«Очередь подтверждения» и не станет эталоном без проверки.",
            text_color=("gray40", "#A7A7A7"), font=ctk.CTkFont(size=12),
            anchor="w", justify="left", wraplength=900,
        ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))

        drop = ctk.CTkFrame(
            top, corner_radius=14, fg_color=("gray88", "#1b1b1f"),
            border_width=2, border_color=("gray75", "#333339"),
        )
        drop.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        drop.grid_columnconfigure(0, weight=1)

        self.lbl_drop = ctk.CTkLabel(
            drop, text="Чертежи ещё не выбраны",
            font=ctk.CTkFont(size=14), text_color=("gray40", "#A7A7A7"),
        )
        self.lbl_drop.grid(row=0, column=0, sticky="w", padx=20, pady=20)

        btns = ctk.CTkFrame(drop, fg_color="transparent")
        btns.grid(row=0, column=1, padx=16, pady=16)

        ctk.CTkButton(
            btns, text="Выбрать файлы", height=38, corner_radius=10, width=150,
            command=self.on_pick_files,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="Выбрать папку", height=38, corner_radius=10, width=150,
            fg_color=("gray80", "gray30"), hover_color=("gray72", "gray35"),
            text_color=("gray10", "gray90"),
            command=self.on_pick_folder,
        ).pack(side="left")

        # ── Сводка + экспорт ──
        bar = ctk.CTkFrame(self, corner_radius=12, fg_color=("gray92", "#141416"),
                            border_width=1, border_color=("gray80", "#222226"))
        bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        bar.grid_columnconfigure(0, weight=1)

        self.lbl_summary = ctk.CTkLabel(
            bar, text="Нет обработанных чертежей", anchor="w",
            font=ctk.CTkFont(size=13),
        )
        self.lbl_summary.grid(row=0, column=0, sticky="w", padx=16, pady=12)

        self.btn_export = ctk.CTkButton(
            bar, text="Сохранить таблицу (Excel)", height=34, corner_radius=10,
            width=200, state="disabled", command=self.on_export,
        )
        self.btn_export.grid(row=0, column=1, padx=16, pady=10)

        # ── Список результатов ──
        self.scroll = ctk.CTkScrollableFrame(self, corner_radius=18)
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.scroll.grid_columnconfigure(0, weight=1)

    # ── Выбор файлов ────────────────────────────────────────

    def on_pick_files(self):
        if self._busy:
            return
        paths = filedialog.askopenfilenames(
            title="Выберите чертежи",
            filetypes=[("Чертежи КОМПАС/PDF", "*.cdw *.pdf *.m3d *.a3d"),
                       ("Все файлы", "*.*")],
        )
        if paths:
            self._run(list(paths))

    def on_pick_folder(self):
        if self._busy:
            return
        folder = filedialog.askdirectory(title="Выберите папку с чертежами")
        if not folder:
            return
        paths = scan_folder(folder)
        if not paths:
            messagebox.showinfo(
                "Ничего не найдено",
                "В выбранной папке нет файлов .pdf/.cdw/.m3d/.a3d."
            )
            return
        self._run(paths)

    # ── Обработка ───────────────────────────────────────────

    def _run(self, paths: list[str]):
        self._busy = True
        self.lbl_drop.configure(
            text=f"Обрабатываю {len(paths)} файл(ов)…", text_color=("gray10", "white")
        )
        self.btn_export.configure(state="disabled")
        for w in self.scroll.winfo_children():
            w.destroy()
        threading.Thread(target=self._worker, args=(paths,), daemon=True).start()

    def _worker(self, paths: list[str]):
        results = []
        for i, p in enumerate(paths, 1):
            try:
                r = process_one_file(p, use_ai=False)
            except Exception as e:                            # noqa: BLE001
                logger.error(f"Загрузка чертежей: ошибка {os.path.basename(p)}: {e}")
                r = {"file": os.path.basename(p), "status": "❌ ошибка",
                     "error": str(e)[:120], "part_name": "", "drawing_no": "",
                     "source": "", "zagotovka_full": ""}
            results.append(r)
            thread_bridge.post(self._progress, i, len(paths))
        thread_bridge.post(self._done, results)

    def _progress(self, done: int, total: int):
        self.lbl_drop.configure(text=f"Обрабатываю… {done}/{total}")

    def _done(self, results: list[dict]):
        self._results = results
        self._busy = False

        pending_now = sum(1 for r in results if "RAG" in r.get("source", ""))
        ok = sum(1 for r in results if r["status"] == "ОК")
        bad = len(results) - ok

        self.lbl_drop.configure(
            text=f"Готово: {len(results)} файл(ов) обработано",
            text_color=("gray10", "white"),
        )
        summary = f"Обработано: {len(results)}   ·   Готово: {ok}   ·   Проблемы: {bad}"
        if pending_now:
            summary += f"   ·   Ждут проверки технолога (RAG/ИИ): {pending_now} — см. «Очередь подтверждения»"
        self.lbl_summary.configure(text=summary)
        self.btn_export.configure(state="normal" if results else "disabled")

        for i, r in enumerate(results):
            self._build_row(r, i)

        logger.info(
            f"Загрузка чертежей: обработано {len(results)}, "
            f"ОК {ok}, проблем {bad}, в очередь {pending_now}"
        )

    def _build_row(self, r: dict, idx: int):
        status = r.get("status", "")
        src = r.get("source", "")
        if "RAG" in src:
            color = ("#7c3aed", "#c084fc")
            badge = "Ждёт проверки"
        elif status == "ОК":
            color = ("#2e7d32", "#4ade80")
            badge = "Готово"
        elif "⚠" in status:
            color = ("#f57f17", "#ffd54f")
            badge = status
        else:
            color = ("#c62828", "#ef9a9a")
            badge = status or "Ошибка"

        row = ctk.CTkFrame(
            self.scroll, corner_radius=12, fg_color=("gray95", "#1b1b1f"),
            border_width=1, border_color=("gray85", "#2a2a30"),
        )
        row.grid(row=idx, column=0, sticky="ew", padx=4, pady=4)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            row, text=badge, width=120, corner_radius=8,
            fg_color=color, text_color="#0b0b0d",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=0, column=0, padx=(12, 10), pady=10)

        title = r.get("part_name") or r.get("file", "")
        dno = r.get("drawing_no", "")
        detail = (r.get("zagotovka_full") or r.get("error") or "").strip()

        text_frame = ctk.CTkFrame(row, fg_color="transparent")
        text_frame.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=8)
        text_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            text_frame, text=f"{title}" + (f"  ·  № {dno}" if dno else ""),
            anchor="w", font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        if detail:
            ctk.CTkLabel(
                text_frame, text=detail, anchor="w",
                text_color=("gray40", "#A7A7A7"), font=ctk.CTkFont(size=11),
                wraplength=760, justify="left",
            ).grid(row=1, column=0, sticky="w")

    # ── Экспорт ─────────────────────────────────────────────

    def on_export(self):
        if not self._results:
            return
        save_path = filedialog.asksaveasfilename(
            title="Сохранить таблицу заготовок",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile="OTK_AI_заготовки.xlsx",
        )
        if not save_path:
            return
        try:
            export_to_excel(self._results, save_path)
            messagebox.showinfo("Готово", f"Таблица сохранена:\n{save_path}")
        except Exception as e:                                # noqa: BLE001
            messagebox.showerror("Ошибка сохранения", str(e))
            logger.error("Загрузка чертежей: ошибка экспорта в Excel", e)
