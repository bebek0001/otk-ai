# -*- coding: utf-8 -*-
"""
ui/screens/kompas_tool.py — вкладка подключения к живому КОМПАС-3D.

В отличие от вкладок PDF/3D, здесь не выбирается файл: приложение
подключается к УЖЕ ЗАПУЩЕННОМУ КОМПАСу и читает его активный документ
напрямую, через COM — без экспорта файлов.

ТЕКУЩИЙ СТАТУС: показывает подключение, версию КОМПАСа и диагностику.
Полноценное чтение геометрии и материалов появится следующим шагом — после
того как integrations/kompas/probe.py прогонят на реальном КОМПАС v22/v25
и станут известны точные имена свойств для этой версии. До этого момента
вкладка намеренно не выдаёт результат расчёта, чтобы не показывать
неоткалиброванные/случайные значения.
"""

from __future__ import annotations

import os
import tempfile
import threading
import customtkinter as ctk
from ui import thread_bridge
from tkinter import filedialog, messagebox

from integrations.kompas.connector import connect, is_available, ConnectionStatus
from integrations.kompas.probe import generate_report
from integrations.kompas.adapter import read_active_part
from integrations.kompas.step_export import export_active_to_step
from core.step_mesh import tessellate_step
from ui.widgets.vtk_viewport import VtkViewport
from core import logger

# Куда экспортируем временный STEP при подтягивании модели из живого КОМПАСа.
_LIVE_STEP_PATH = os.path.join(tempfile.gettempdir(), "otk_ai_live_kompas.step")

# Период автообновления (мс) — для демонстрации: держим низким достаточно,
# чтобы изменения в КОМПАСе были видны почти сразу, но не настолько низким,
# чтобы экспорт STEP (не самая быстрая операция) накладывался сам на себя.
_LIVE_POLL_MS = 4000


class KompasToolScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.status: ConnectionStatus | None = None
        self.step_path: str | None = None
        self._live_tracking_var = ctk.BooleanVar(value=False)
        self._live_poll_job = None
        self._live_pull_busy = False
        self._build_ui()
        self.on_refresh()

    def destroy(self):
        if self._live_poll_job is not None:
            try:
                self.after_cancel(self._live_poll_job)
            except Exception:                                 # noqa: BLE001
                pass
            self._live_poll_job = None
        super().destroy()

    def _build_ui(self):
        top = ctk.CTkFrame(
            self, corner_radius=12, fg_color=("gray92", "#141416"),
            border_width=1, border_color=("gray80", "#222226"),
        )
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 10))
        top.grid_columnconfigure(1, weight=1)

        self._dot = ctk.CTkFrame(top, width=10, height=10, corner_radius=5)
        self._dot.grid(row=0, column=0, padx=(16, 8), pady=14)
        self._dot.grid_propagate(False)

        self._status_label = ctk.CTkLabel(
            top, text="Проверка…", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self._status_label.grid(row=0, column=1, sticky="w", pady=14)

        ctk.CTkButton(
            top, text="Подключиться / обновить", height=36, corner_radius=12,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"), command=self.on_refresh,
        ).grid(row=0, column=2, padx=(16, 8), pady=12)

        self._probe_btn = ctk.CTkButton(
            top, text="Считать диагностику", height=36, corner_radius=12,
            command=self.on_probe,
        )
        self._probe_btn.grid(row=0, column=3, padx=(0, 8), pady=12)

        self._read_btn = ctk.CTkButton(
            top, text="Прочитать активную деталь", height=36, corner_radius=12,
            fg_color="#2E7D32", hover_color="#1B5E20",
            command=self.on_read_part,
        )
        self._read_btn.grid(row=0, column=4, padx=(0, 16), pady=12)

        card = ctk.CTkFrame(
            self, corner_radius=12, fg_color=("gray92", "#141416"),
            border_width=1, border_color=("gray80", "#222226"),
        )
        card.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)

        self._detail_label = ctk.CTkLabel(
            card, text="", anchor="w", justify="left",
            text_color=("gray40", "#A7A7A7"), wraplength=900,
        )
        self._detail_label.grid(row=0, column=0, sticky="ew", padx=16, pady=14)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        self._output = ctk.CTkTextbox(body, font=ctk.CTkFont(family="Courier", size=12))
        self._output.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        viewer_card = ctk.CTkFrame(
            body, corner_radius=12, fg_color=("gray92", "#141416"),
            border_width=1, border_color=("gray80", "#222226"),
        )
        viewer_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        viewer_card.grid_rowconfigure(1, weight=1)
        viewer_card.grid_columnconfigure(0, weight=1)

        viewer_head = ctk.CTkFrame(viewer_card, fg_color="transparent")
        viewer_head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        ctk.CTkLabel(
            viewer_head, text="3D просмотр (STEP) — эксперимент",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            viewer_head, text="Открыть STEP", height=28, corner_radius=8, width=110,
            fg_color=("gray88", "gray25"), hover_color=("gray80", "gray30"),
            text_color=("gray10", "gray90"),
            command=self.on_pick_step,
        ).pack(side="right")
        self._live_check = ctk.CTkCheckBox(
            viewer_head, text="Автообновление", variable=self._live_tracking_var,
            command=self._on_toggle_live_tracking,
            onvalue=True, offvalue=False, checkbox_width=18, checkbox_height=18,
        )
        self._live_check.pack(side="right", padx=(0, 10))
        self._live_btn = ctk.CTkButton(
            viewer_head, text="Подтянуть модель из КОМПАСа", height=28, corner_radius=8,
            fg_color="#2E7D32", hover_color="#1B5E20",
            command=self.on_live_pull,
        )
        self._live_btn.pack(side="right", padx=(0, 10))

        self._viewport = VtkViewport(viewer_card)
        self._viewport.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        self._set_output(
            "Эта вкладка подключается к уже открытому КОМПАС-3D (v22 или v25) "
            "и читает его активный документ напрямую — без экспорта файлов.\n\n"
            "Пока доступно только чтение диагностики. Нажмите «Считать "
            "диагностику», когда в КОМПАСе открыта деталь или сборка — здесь "
            "появится полный отчёт о том, что приложение видит в модели. Этот "
            "отчёт нужно будет прислать для настройки чтения геометрии и "
            "материалов под вашу версию КОМПАСа."
        )

    def _set_output(self, text: str):
        self._output.configure(state="normal")
        self._output.delete("1.0", "end")
        self._output.insert("1.0", text)
        self._output.configure(state="disabled")

    def on_refresh(self):
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def on_probe(self):
        self._probe_btn.configure(state="disabled", text="Считаю…")
        self._set_output("Считываю диагностику… подождите несколько секунд.")
        threading.Thread(target=self._probe_worker, daemon=True).start()

    def _probe_worker(self):
        try:
            report = generate_report()
        except Exception as e:                             # noqa: BLE001
            report = f"Не удалось получить диагностику: {e}"
        thread_bridge.post(self._apply_probe_result, report)

    def _apply_probe_result(self, report: str):
        self._probe_btn.configure(state="normal", text="Считать диагностику")
        self._set_output(report)
        logger.info("Диагностика КОМПАС считана и показана на вкладке")

    def on_read_part(self):
        self._read_btn.configure(state="disabled", text="Читаю…")
        self._set_output("Читаю активную деталь из КОМПАСа… подождите несколько секунд.")
        threading.Thread(target=self._read_part_worker, daemon=True).start()

    def _read_part_worker(self):
        try:
            result = read_active_part()
        except Exception as e:                             # noqa: BLE001
            result = None
            error_text = f"Непредвиденная ошибка: {e}"
        else:
            error_text = ""
        thread_bridge.post(self._apply_read_result, result, error_text)

    def _apply_read_result(self, result, error_text: str):
        self._read_btn.configure(state="normal", text="Прочитать активную деталь")

        if result is None:
            self._set_output(error_text)
            logger.error(f"КОМПАС(live): {error_text}")
            return

        lines = [
            "=== OTK AI: чтение активной детали из КОМПАС-3D ===",
            f"Статус: {result.status}",
        ]
        if result.message:
            lines.append(result.message)
        lines.append("")
        lines.append(f"Обозначение: {result.drawing_no or '—'}")
        lines.append(f"Наименование: {result.part_name or '—'}")
        lines.append(f"Материал: {result.material_mark or '—'}")
        lines.append(f"Масса, кг: {result.mass_kg if result.mass_kg is not None else '—'}")
        if result.volume_mm3 is not None:
            lines.append(f"Объём, мм³: {result.volume_mm3}")
        if result.density_kg_m3 is not None:
            lines.append(f"Плотность, кг/м³: {result.density_kg_m3}")
        lines.append(f"Источник: {result.source or '—'}")
        if result.result_line:
            lines.append("")
            lines.append("Результат из базы эталонов:")
            lines.append(result.result_line)
        if result.raw_debug:
            lines.append("")
            lines.append("--- Технические детали (для разбора, если что-то не так) ---")
            lines.append(result.raw_debug)

        self._set_output("\n".join(lines))
        logger.info(f"КОМПАС(live): прочитана деталь {result.drawing_no or result.part_name}")

    # ── 3D просмотр (STEP) ────────────────────────────────────

    def on_pick_step(self):
        path = filedialog.askopenfilename(
            title="Выбери STEP-модель",
            filetypes=[("STEP", "*.stp *.step *.STP *.STEP"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        self.step_path = path
        threading.Thread(target=self._load_step_worker, args=(path,), daemon=True).start()

    def _load_step_worker(self, path: str):
        try:
            mesh = tessellate_step(path)
        except Exception as e:                                # noqa: BLE001
            logger.error(f"3D просмотр: ошибка тесселяции {os.path.basename(path)}", e)
            mesh = None
        thread_bridge.post(self._apply_step_mesh, path, mesh)

    def _apply_step_mesh(self, path: str, mesh):
        if mesh is None:
            messagebox.showerror(
                "Не удалось открыть модель",
                f"Не получилось построить сетку для просмотра:\n{os.path.basename(path)}\n\n"
                "Подробности — в панели разработчика (логи)."
            )
            return
        ok = self._viewport.load_mesh(mesh)
        if not ok:
            messagebox.showwarning(
                "3D-вьюпорт недоступен",
                "Модель прочитана, но показать её не получилось — не хватает "
                "зависимостей (vtk/numpy/Pillow). Подробности в панели вьюпорта слева."
            )
            return
        logger.info(f"3D просмотр: показана модель {os.path.basename(path)} "
                    f"({mesh.triangle_count} треугольников)")

    # ── 3D просмотр (живой КОМПАС) ────────────────────────────

    def on_live_pull(self):
        """Подтягивает модель прямо из уже открытого в КОМПАСе документа
        (экспорт в STEP + тесселяция), без ручного выбора файла."""
        if self._live_pull_busy:
            return
        self._live_pull_busy = True
        self._live_btn.configure(state="disabled", text="Подтягиваю из КОМПАСа…")
        threading.Thread(target=self._live_pull_worker, args=(True,), daemon=True).start()

    def _on_toggle_live_tracking(self):
        if self._live_tracking_var.get():
            logger.info("3D просмотр: включено автообновление из живого КОМПАСа")
            self._schedule_live_poll()
        else:
            logger.info("3D просмотр: автообновление выключено")
            if self._live_poll_job is not None:
                self.after_cancel(self._live_poll_job)
                self._live_poll_job = None

    def _schedule_live_poll(self):
        self._live_poll_job = self.after(_LIVE_POLL_MS, self._run_live_poll)

    def _run_live_poll(self):
        self._live_poll_job = None
        if not self._live_tracking_var.get():
            return
        if self._live_pull_busy:
            # Предыдущий экспорт ещё не закончился — пропускаем такт,
            # пробуем снова на следующем.
            self._schedule_live_poll()
            return
        self._live_pull_busy = True
        threading.Thread(target=self._live_pull_worker, args=(False,), daemon=True).start()

    def _live_pull_worker(self, manual: bool):
        export_result = export_active_to_step(_LIVE_STEP_PATH)
        if not export_result.ok:
            thread_bridge.post(self._apply_live_pull_result, None, export_result.error, manual)
            return
        try:
            mesh = tessellate_step(_LIVE_STEP_PATH)
        except Exception as e:                                # noqa: BLE001
            thread_bridge.post(self._apply_live_pull_result, None,
                       f"Модель экспортирована, но не удалось построить сетку: {e}", manual)
            return
        if mesh is None:
            thread_bridge.post(self._apply_live_pull_result, None,
                       "Модель экспортирована, но в ней не нашлось геометрии для показа.",
                       manual)
            return
        thread_bridge.post(self._apply_live_pull_result, mesh, "", manual)

    def _apply_live_pull_result(self, mesh, error: str, manual: bool):
        self._live_pull_busy = False
        self._live_btn.configure(state="normal", text="Подтянуть модель из КОМПАСа")

        if mesh is not None:
            self.step_path = None
            ok = self._viewport.load_mesh(mesh)
            if ok:
                logger.info(f"3D просмотр: модель подтянута из живого КОМПАСа "
                            f"({mesh.triangle_count} треугольников)")
            elif manual:
                messagebox.showwarning(
                    "3D-вьюпорт недоступен",
                    "Модель прочитана из КОМПАСа, но показать её не получилось — не "
                    "хватает зависимостей (vtk/numpy/Pillow)."
                )
        elif manual:
            messagebox.showerror("Не удалось подтянуть модель из КОМПАСа", error)
        elif error:
            logger.warn(f"Автообновление из КОМПАСа: {error}")

        # Автообновление продолжаем планировать, даже если этот такт не удался —
        # КОМПАС может просто быть временно занят (например, идёт пересчёт).
        if self._live_tracking_var.get():
            self._schedule_live_poll()

    def _refresh_worker(self):
        status = connect() if is_available() else ConnectionStatus(
            False, error="Доступно только в Windows-сборке"
        )
        thread_bridge.post(self._apply_status, status)

    def _apply_status(self, status: ConnectionStatus):
        self.status = status
        if status.connected:
            self._dot.configure(fg_color="#4CAF50")
            self._status_label.configure(
                text=f"Подключено: КОМПАС {status.kompas_version} ({status.api})"
            )
            self._detail_label.configure(
                text="КОМПАС найден и отвечает. Откройте деталь или сборку в "
                     "КОМПАСе, затем нажмите «Подключиться / обновить» ещё раз."
            )
            logger.info(f"КОМПАС подключён: {status.api} / {status.kompas_version}")
        else:
            self._dot.configure(fg_color="#EF5350")
            self._status_label.configure(text="КОМПАС не подключён")
            self._detail_label.configure(
                text=status.error or "Запустите КОМПАС-3D и откройте документ."
            )
            logger.warn(f"КОМПАС не подключён: {status.error}")
