# -*- coding: utf-8 -*-
"""
ui/widgets/vtk_viewport.py — встраиваемый 3D-вьюпорт на VTK.

Экспериментальная фича: показать STEP-модель прямо в приложении,
крутить/масштабировать мышью.

ПОЧЕМУ НЕ vtkTkRenderWindowInteractor (стандартный способ встраивания
VTK в Tkinter). Он требует отдельно скомпилированную библиотеку
(libvtkRenderingTk / vtkRenderingPythonTkWidgets), которой в
пакете `vtk`, устанавливаемом из PyPI, просто нет — проверено прямо
в этом проекте:

    _tkinter.TclError: couldn't load file "libvtkRenderingTk.so":
    libvtkRenderingTk.so: cannot open shared object file

Это известное ограничение pip-пакета vtk, не баг конкретной машины —
столкнулись бы с ним что на Windows, что на macOS, что здесь.

ПОЭТОМУ: рендерим VTK в offscreen-окно (SetOffScreenRendering(1) —
не требует той же библиотеки, работает на обычном GL-контексте),
на каждом кадре снимаем картинку (vtkWindowToImageFilter) и
показываем её как обычную картинку на холсте customtkinter/Tkinter.
Вращение мышью — вручную: тащим мышь → двигаем vtkCamera (Azimuth/
Elevation) → перерисовываем кадр. Не «живой» OpenGL-виджет, а
покадровая картинка, но для осмотра детали (не анимации) разницы
пользователь не увидит, а работает одинаково на всех платформах.

Статус проверки: вся цепочка (OCP-тесселяция → vtkPolyData → offscreen
рендер → снятие кадра → реальные ненулевые пиксели с силуэтом детали)
прогнана и работает headless в этой же среде разработки (на синтетических
STEP-кубе и цилиндре). Что НЕ проверено — поведение мыши и производительность
на реальном экране с живыми Tk-событиями и на настоящих, более сложных
STEP-файлах клиента. Это нужно проверить на вашей машине перед тем, как
полагаться на вкладку в работе.
"""

from __future__ import annotations

import math
import customtkinter as ctk

from core import logger
from core.step_mesh import MeshData

try:
    # Берём классы напрямую из конкретных подмодулей vtkmodules, а НЕ через
    # зонтичный пакет `import vtk` — тот на импорте тянет вообще все модули
    # VTK (включая совершенно ненужные здесь, вроде vtkIOInfovis), что на
    # некоторых машинах превращается в очень долгий (от минуты и дольше)
    # старт приложения. Прицельный импорт — те же классы, без этого.
    from vtkmodules.vtkCommonCore import vtkFloatArray, vtkPoints
    from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData
    from vtkmodules.vtkRenderingCore import (
        vtkActor,
        vtkLight,
        vtkPolyDataMapper,
        vtkRenderWindow,
        vtkRenderer,
        vtkWindowToImageFilter,
    )
    # Побочный эффект импорта: регистрирует конкретную OpenGL2-реализацию
    # рендеринга. Без этой строки рендер-окно создаётся, но не может
    # реально рисовать (нет зарегистрированной фабрики рендер-бэкенда).
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401

    import numpy as np
    from PIL import Image, ImageTk
    VTK_AVAILABLE = True
    VTK_IMPORT_ERROR = ""
except Exception as e:                                        # noqa: BLE001
    VTK_AVAILABLE = False
    VTK_IMPORT_ERROR = str(e)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalize(v):
    length = math.sqrt(sum(c * c for c in v))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(c / length for c in v)


class VtkViewport(ctk.CTkFrame):
    """
    Tk-фрейм с 3D-просмотром VTK (offscreen-рендер + картинка на канвасе,
    см. объяснение в шапке файла). Если VTK/numpy/Pillow не установлены —
    вместо вьюпорта показывает текст с ошибкой, ничего не ломает.

    Использование:
        vp = VtkViewport(parent)
        vp.load_mesh(mesh_data)   # core.step_mesh.MeshData
        vp.clear()
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=("gray85", "#0d0d0f"), **kwargs)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._ok = VTK_AVAILABLE
        self._renderer = None
        self._renwin = None
        self._w2i = None
        self._camera = None
        self._canvas = None
        self._photo = None          # держим ссылку — иначе Tk соберёт мусор
        self._actor = None
        self._has_model = False
        self._drag_last = None      # (x, y) последней точки при вращении
        self._pan_last = None

        if not self._ok:
            ctk.CTkLabel(
                self,
                text=("3D-вьюпорт недоступен: не хватает зависимостей "
                      "(vtk / numpy / Pillow).\n"
                      f"Ошибка импорта: {VTK_IMPORT_ERROR}\n\n"
                      "Установите: pip install vtk pillow numpy"),
                justify="left", text_color=("gray40", "#A7A7A7"),
            ).grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
            return

        self._build_vtk()

    # ── Построение offscreen-сцены + canvas ──────────────────

    def _build_vtk(self):
        import tkinter as tk

        self._renderer = vtkRenderer()
        self._renderer.SetBackground(0.09, 0.09, 0.11)

        self._renwin = vtkRenderWindow()
        self._renwin.SetOffScreenRendering(1)
        self._renwin.AddRenderer(self._renderer)
        self._renwin.SetSize(640, 480)

        light = vtkLight()
        light.SetLightTypeToHeadlight()
        self._renderer.AddLight(light)

        self._camera = self._renderer.GetActiveCamera()

        self._w2i = vtkWindowToImageFilter()
        self._w2i.SetInput(self._renwin)
        self._w2i.ReadFrontBufferOff()

        self._canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self._canvas.grid(row=0, column=0, sticky="nsew")

        self._canvas.bind("<Configure>", self._on_resize)
        self._canvas.bind("<ButtonPress-1>", self._on_rotate_start)
        self._canvas.bind("<B1-Motion>", self._on_rotate_move)
        self._canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self._canvas.bind("<B3-Motion>", self._on_pan_move)
        # Колесо мыши: Windows/macOS шлют <MouseWheel>, Linux — Button-4/5
        self._canvas.bind("<MouseWheel>", self._on_zoom_wheel)
        self._canvas.bind("<Button-4>", lambda e: self._zoom(1.1))
        self._canvas.bind("<Button-5>", lambda e: self._zoom(1 / 1.1))

        self._canvas.create_text(
            10, 10, anchor="nw", fill="#777777",
            text="Загрузите STEP, чтобы увидеть модель. ЛКМ — вращение, "
                 "ПКМ — сдвиг, колесо — масштаб.",
            tags=("hint",),
        )

    # ── API ─────────────────────────────────────────────────

    def load_mesh(self, mesh: MeshData) -> bool:
        """Показывает сетку. Возвращает False, если вьюпорт недоступен."""
        if not self._ok:
            return False
        try:
            points = vtkPoints()
            normals = vtkFloatArray()
            normals.SetNumberOfComponents(3)
            polys = vtkCellArray()

            vd = mesh.vertex_data
            n_verts = len(vd) // 6
            for i in range(n_verts):
                off = i * 6
                points.InsertNextPoint(vd[off], vd[off + 1], vd[off + 2])
                normals.InsertNextTuple3(vd[off + 3], vd[off + 4], vd[off + 5])

            for t in range(mesh.triangle_count):
                base = t * 3
                polys.InsertNextCell(3, (base, base + 1, base + 2))

            poly = vtkPolyData()
            poly.SetPoints(points)
            poly.SetPolys(polys)
            poly.GetPointData().SetNormals(normals)

            mapper = vtkPolyDataMapper()
            mapper.SetInputData(poly)

            if self._actor is not None:
                self._renderer.RemoveActor(self._actor)

            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetInterpolationToFlat()
            actor.GetProperty().SetColor(0.55, 0.62, 0.70)
            actor.GetProperty().SetSpecular(0.2)
            self._renderer.AddActor(actor)
            self._actor = actor
            self._has_model = True

            self._renderer.ResetCamera()
            self._render_frame()
            return True
        except Exception as e:                                # noqa: BLE001
            logger.error("VTK-вьюпорт: не удалось показать модель", e)
            return False

    def clear(self):
        if not self._ok or self._actor is None:
            return
        self._renderer.RemoveActor(self._actor)
        self._actor = None
        self._has_model = False
        self._render_frame()

    # ── Рендер кадра и показ на канвасе ──────────────────────

    def _render_frame(self):
        if not self._ok or self._canvas is None:
            return
        w = max(int(self._canvas.winfo_width()), 10)
        h = max(int(self._canvas.winfo_height()), 10)
        if (self._renwin.GetSize()[0], self._renwin.GetSize()[1]) != (w, h):
            self._renwin.SetSize(w, h)

        self._renwin.Render()
        self._w2i.Modified()
        self._w2i.Update()

        img = self._w2i.GetOutput()
        dims = img.GetDimensions()
        scalars = img.GetPointData().GetScalars()
        if scalars is None or dims[0] <= 0 or dims[1] <= 0:
            return

        arr = np.frombuffer(scalars, dtype=np.uint8)
        n_comp = scalars.GetNumberOfComponents()
        arr = arr.reshape(dims[1], dims[0], n_comp)
        arr = np.flipud(arr)   # VTK: начало координат внизу, Tk: вверху

        pil_img = Image.fromarray(arr[:, :, :3], "RGB")
        self._photo = ImageTk.PhotoImage(pil_img)

        self._canvas.delete("frame")
        self._canvas.create_image(0, 0, anchor="nw", image=self._photo, tags=("frame",))
        if not self._has_model:
            self._canvas.tag_raise("hint")
        else:
            self._canvas.delete("hint")

    def _on_resize(self, _evt):
        self._render_frame()

    # ── Мышь: вращение ────────────────────────────────────────

    def _on_rotate_start(self, evt):
        self._drag_last = (evt.x, evt.y)

    def _on_rotate_move(self, evt):
        if self._drag_last is None or not self._has_model:
            return
        dx = evt.x - self._drag_last[0]
        dy = evt.y - self._drag_last[1]
        self._drag_last = (evt.x, evt.y)
        self._camera.Azimuth(-dx * 0.5)
        self._camera.Elevation(dy * 0.5)
        self._camera.OrthogonalizeViewUp()
        self._render_frame()

    # ── Мышь: сдвиг (пан) ─────────────────────────────────────

    def _on_pan_start(self, evt):
        self._pan_last = (evt.x, evt.y)

    def _on_pan_move(self, evt):
        if self._pan_last is None or not self._has_model:
            return
        dx = evt.x - self._pan_last[0]
        dy = evt.y - self._pan_last[1]
        self._pan_last = (evt.x, evt.y)

        h = max(self._renwin.GetSize()[1], 1)
        fp = self._camera.GetFocalPoint()
        pos = self._camera.GetPosition()
        up = _normalize(self._camera.GetViewUp())
        view_dir = _normalize(tuple(fp[i] - pos[i] for i in range(3)))
        right = _normalize(_cross(view_dir, up))

        if self._camera.GetParallelProjection():
            scale = self._camera.GetParallelScale() * 2.0 / h
        else:
            dist = self._camera.GetDistance()
            angle = math.radians(self._camera.GetViewAngle())
            scale = 2.0 * dist * math.tan(angle / 2.0) / h

        move = tuple(
            -dx * scale * right[i] + dy * scale * up[i] for i in range(3)
        )
        self._camera.SetFocalPoint(*(fp[i] + move[i] for i in range(3)))
        self._camera.SetPosition(*(pos[i] + move[i] for i in range(3)))
        self._render_frame()

    # ── Мышь: масштаб ─────────────────────────────────────────

    def _on_zoom_wheel(self, evt):
        factor = 1.1 if evt.delta > 0 else 1 / 1.1
        self._zoom(factor)

    def _zoom(self, factor: float):
        if not self._has_model:
            return
        self._camera.Dolly(factor)
        self._renderer.ResetCameraClippingRange()
        self._render_frame()
