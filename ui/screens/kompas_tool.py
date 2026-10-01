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

import threading
import customtkinter as ctk

from integrations.kompas.connector import connect, is_available, ConnectionStatus
from core import logger


class KompasToolScreen(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.status: ConnectionStatus | None = None
        self._build_ui()
        self.on_refresh()

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
        ).grid(row=0, column=2, padx=16, pady=12)

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

        self._output = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Courier", size=12))
        self._output.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self._set_output(
            "Эта вкладка подключается к уже открытому КОМПАС-3D (v22 или v25) "
            "и читает его активный документ напрямую — без экспорта файлов.\n\n"
            "Пока доступна только диагностика подключения. Чтение геометрии "
            "и материалов добавится следующим шагом, когда будет откалиброван "
            "адаптер под вашу версию КОМПАСа (integrations/kompas/probe.py)."
        )

    def _set_output(self, text: str):
        self._output.configure(state="normal")
        self._output.delete("1.0", "end")
        self._output.insert("1.0", text)
        self._output.configure(state="disabled")

    def on_refresh(self):
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        status = connect() if is_available() else ConnectionStatus(
            False, error="Доступно только в Windows-сборке"
        )
        self.after(0, self._apply_status, status)

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
