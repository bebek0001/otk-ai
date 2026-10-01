import os
import customtkinter as ctk
from ui.screens.drawing_tool import DrawingToolScreen
from ui.screens.settings_screen import SettingsScreen
from ui.screens.model3d_tool import Model3DToolScreen
from ui.screens.ai_assistant import AIAssistantScreen
from ui.screens.batch_tool import BatchToolScreen
from ui.screens.kompas_tool import KompasToolScreen
from ui.screens.m3d_only_tool import M3DOnlyToolScreen
from core import logger


# ============================================================
# Панель разработчика
# ============================================================

class DevPanel(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("OTK AI — Панель разработчика")
        self.geometry("860x520")
        self.minsize(600, 360)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(header, text="Логи приложения",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="Очистить", width=90, height=28,
                      command=self._on_clear).pack(side="right")

        filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        filter_frame.pack(fill="x", padx=14, pady=(6, 4))
        ctk.CTkLabel(filter_frame, text="Фильтр:").pack(side="left", padx=(0, 6))
        self._filter_var = ctk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._refresh_filter())
        ctk.CTkEntry(filter_frame, textvariable=self._filter_var,
                     width=260, height=28).pack(side="left")
        self._level_var = ctk.StringVar(value="ВСЕ")
        ctk.CTkOptionMenu(
            filter_frame,
            values=["ВСЕ", "INFO", "WARN", "ERROR", "DEBUG"],
            variable=self._level_var, width=100, height=28,
            command=lambda _: self._refresh_filter()
        ).pack(side="left", padx=8)

        self._text = ctk.CTkTextbox(
            self, font=ctk.CTkFont(family="Courier", size=12),
            wrap="none", state="disabled"
        )
        self._text.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self._text.tag_config("ERROR", foreground="#ff6b6b")
        self._text.tag_config("WARN",  foreground="#ffd93d")
        self._text.tag_config("DEBUG", foreground="#888888")
        self._text.tag_config("INFO",  foreground="#a8d8a8")

        for line in logger.get_history():
            self._append(line)

        logger.subscribe(self._on_new_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("Панель разработчика открыта")

    def _on_new_log(self, line):
        self.after(0, self._append, line)

    def _append(self, line):
        level = "INFO"
        for lvl in ("ERROR", "WARN", "DEBUG", "INFO"):
            if f"[{lvl}]" in line:
                level = lvl
                break
        filt = self._filter_var.get().strip().lower()
        sel = self._level_var.get()
        if sel != "ВСЕ" and level != sel:
            return
        if filt and filt not in line.lower():
            return
        self._text.configure(state="normal")
        self._text.insert("end", line + "\n", level)
        self._text.see("end")
        self._text.configure(state="disabled")

    def _refresh_filter(self):
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
        for line in logger.get_history():
            self._append(line)

    def _on_clear(self):
        logger.clear()
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
        logger.info("Логи очищены")

    def _on_close(self):
        logger.unsubscribe(self._on_new_log)
        logger.info("Панель разработчика закрыта")
        self.destroy()


# ============================================================
# Sidebar
# ============================================================

class Sidebar(ctk.CTkFrame):
    def __init__(self, master, on_nav):
        super().__init__(master, corner_radius=18)
        self.on_nav = on_nav
        self.grid_rowconfigure(50, weight=1)
        self._build()

    def _build(self):
        # Логотип
        ctk.CTkLabel(
            self, text="OTK.AI",
            font=ctk.CTkFont(size=18, weight="bold")
        ).grid(row=0, column=0, padx=18, pady=(18, 10), sticky="w")

        # Навигация
        items = [
            ("PDF инструмент",  "pdf"),
            ("3D инструмент",   "model3d"),
            ("М3D инструмент",  "m3d_only"),
            ("ИИ Ассистент",    "ai_assistant"),
            ("Пакетная обработка", "batch"),
            ("КОМПАС",          "kompas"),
            ("Настройки",       "settings"),
        ]

        for i, (label, key) in enumerate(items, start=1):
            ctk.CTkButton(
                self,
                text=label,
                anchor="w",
                height=38,
                corner_radius=12,
                fg_color="transparent",
                # Адаптивные цвета: (светлая тема, тёмная тема)
                hover_color=("gray85", "#1f1f1f"),
                text_color=("gray10", "gray90"),
                command=lambda k=key: self.on_nav(k),
            ).grid(row=i, column=0, padx=12, pady=4, sticky="ew")

        # API статус
        api_key = os.environ.get("OPENAI_API_KEY", "")
        api_ok = bool(api_key and len(api_key) > 10)

        api_frame = ctk.CTkFrame(self, fg_color="transparent")
        api_frame.grid(row=51, column=0, padx=14, pady=(8, 2), sticky="w")

        dot_color = "#4CAF50" if api_ok else "#EF5350"
        dot = ctk.CTkFrame(api_frame, width=8, height=8,
                            corner_radius=4, fg_color=dot_color)
        dot.pack(side="left", padx=(0, 6))
        dot.pack_propagate(False)

        ctk.CTkLabel(
            api_frame,
            text="API активен" if api_ok else "API не найден",
            font=ctk.CTkFont(size=11),
            text_color=dot_color,
        ).pack(side="left")

        # Панель разработчика
        ctk.CTkButton(
            self,
            text="Панель разработчика",
            anchor="w",
            height=34,
            corner_radius=12,
            fg_color="transparent",
            hover_color=("gray85", "#2a2a2a"),
            text_color=("gray50", "#888888"),
            font=ctk.CTkFont(size=12),
            command=lambda: self.on_nav("devpanel"),
        ).grid(row=52, column=0, padx=12, pady=(2, 14), sticky="ew")


# ============================================================
# App
# ============================================================

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("OTK.AI")
        self.geometry("1280x780")
        self.minsize(1100, 680)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, self.on_nav)
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=12, pady=12)

        self.content = ctk.CTkFrame(self, corner_radius=18)
        self.content.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=12)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self._dev_panel = None
        self.current = None
        self.show_screen("pdf")
        logger.info("OTK.AI запущен")

    def show_screen(self, key: str):
        if self.current is not None:
            self.current.destroy()

        if key == "pdf":
            self.current = DrawingToolScreen(self.content)
        elif key == "model3d":
            self.current = Model3DToolScreen(self.content)
        elif key == "m3d_only":
            self.current = M3DOnlyToolScreen(self.content)
        elif key == "ai_assistant":
            self.current = AIAssistantScreen(self.content)
        elif key == "batch":
            self.current = BatchToolScreen(self.content)
        elif key == "kompas":
            self.current = KompasToolScreen(self.content)
        elif key == "settings":
            self.current = SettingsScreen(self.content)
        else:
            self.current = ctk.CTkFrame(self.content, fg_color="transparent")
            ctk.CTkLabel(
                self.current, text="Экран недоступен",
                font=ctk.CTkFont(size=22, weight="bold")
            ).pack(padx=18, pady=18, anchor="w")

        self.current.grid(row=0, column=0, sticky="nsew")
        logger.debug(f"Переход на экран: {key}")

    def on_nav(self, key: str):
        if key == "devpanel":
            self._open_dev_panel()
        else:
            self.show_screen(key)

    def _open_dev_panel(self):
        if self._dev_panel is not None and self._dev_panel.winfo_exists():
            self._dev_panel.focus()
            return
        self._dev_panel = DevPanel(self)
        self._dev_panel.focus()