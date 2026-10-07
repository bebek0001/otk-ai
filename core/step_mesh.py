# -*- coding: utf-8 -*-
"""
STEP_MESH.PY — триангуляция STEP-модели для 3D-просмотра.

Отдельный модуль от core/step_reader.py: тот читает STEP ради габаритов/
массы/типа заготовки и наружу отдаёт только посчитанные числа (StepModel).
Для вьюпорта нужна сама геометрия — треугольная сетка (вершины + грани),
которую можно отрисовать через OpenGL. Поэтому STEP читается здесь
заново через тот же OpenCASCADE (OCP) — он уже обязательная зависимость
для 3D-инструмента, ничего нового в проект не добавляет.

Экспериментальная фича (просмотр 3D-модели во вкладке КОМПАС) — код
проверен на синтетической модели (куб через BRepPrimAPI_MakeBox) внутри
среды разработки, но НЕ проверен на реальном STEP от КОМПАС и не
проверен сам вывод на экран (OpenGL) — для этого нужен реальный дисплей.
Перед тем как полагаться на эту вкладку в работе — прогнать на нескольких
настоящих STEP-файлах и посмотреть глазами, что модель выглядит правильно.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from core import logger


@dataclass
class MeshData:
    """
    Триангулированная геометрия, готовая для OpenGL.

    triangles — плоский список float, по 3 вершины на треугольник,
    на каждую вершину — позиция (x,y,z) и нормаль грани (nx,ny,nz),
    т.е. 6 float на вершину, 18 float на треугольник. Нормаль одна на
    весь треугольник (плоское затенение) — для технического просмотра
    этого достаточно и не нужно разбираться с UV/поверхностными
    нормалями из OCP.
    """
    vertex_data: List[float] = field(default_factory=list)   # x,y,z,nx,ny,nz, ...
    triangle_count: int = 0
    bbox_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bbox_max: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    center: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    radius: float = 1.0   # половина диагонали bbox — для камеры по умолчанию


def ocp_available() -> Tuple[bool, str]:
    try:
        from OCP.STEPControl import STEPControl_Reader  # noqa: F401
        from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: F401
        return True, ""
    except Exception as e:                                    # noqa: BLE001
        return False, str(e)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _normalize(n):
    length = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (n[0] / length, n[1] / length, n[2] / length)


def tessellate_step(path: str, linear_deflection: float = 0.5,
                     angular_deflection: float = 0.5) -> Optional[MeshData]:
    """
    Читает STEP и строит треугольную сетку для просмотра.

    linear_deflection (мм) — чем меньше, тем точнее сетка повторяет
    кривые поверхности (цилиндры, скругления), но тем больше треугольников.
    0.5 мм — разумный компромисс для просмотра на экране, не для точных
    измерений (для них есть core/step_reader.py с аналитической геометрией).
    """
    if not path or not os.path.exists(path):
        logger.warn(f"step_mesh: файл не найден: {path}")
        return None

    ok, err = ocp_available()
    if not ok:
        logger.error(f"step_mesh: OpenCASCADE (cadquery-ocp) не установлен: {err}")
        return None

    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_Box

    logger.info(f"step_mesh: тесселирую {os.path.basename(path)}")

    reader = STEPControl_Reader()
    if reader.ReadFile(path) != IFSelect_RetDone:
        logger.error(f"step_mesh: не удалось прочитать файл {path}")
        return None
    reader.TransferRoots()
    shape = reader.OneShape()

    BRepMesh_IncrementalMesh(shape, linear_deflection, False, angular_deflection, True)

    bbox = Bnd_Box()
    try:
        BRepBndLib.AddOptimal_s(shape, bbox, True, False)
    except Exception:                                         # noqa: BLE001
        BRepBndLib.Add_s(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()

    vertex_data: List[float] = []
    tri_count = 0

    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            reversed_face = face.Orientation() == TopAbs_REVERSED

            nodes = []
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                nodes.append((p.X(), p.Y(), p.Z()))

            for i in range(1, tri.NbTriangles() + 1):
                i1, i2, i3 = tri.Triangle(i).Get()
                if reversed_face:
                    i1, i2, i3 = i3, i2, i1
                try:
                    v1, v2, v3 = nodes[i1 - 1], nodes[i2 - 1], nodes[i3 - 1]
                except IndexError:
                    continue
                normal = _normalize(_cross(_sub(v2, v1), _sub(v3, v1)))
                for v in (v1, v2, v3):
                    vertex_data.extend(v)
                    vertex_data.extend(normal)
                tri_count += 1
        explorer.Next()

    if tri_count == 0:
        logger.warn(f"step_mesh: в модели не нашлось треугольников: {path}")
        return None

    center = ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)
    radius = (((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2) ** 0.5) / 2.0

    logger.info(f"step_mesh: {os.path.basename(path)} → {tri_count} треугольников")

    return MeshData(
        vertex_data=vertex_data,
        triangle_count=tri_count,
        bbox_min=(xmin, ymin, zmin),
        bbox_max=(xmax, ymax, zmax),
        center=center,
        radius=radius if radius > 1e-6 else 1.0,
    )
