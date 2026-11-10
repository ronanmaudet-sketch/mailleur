"""Vue Qt de données natives ; caches réservés au rendu et à la sélection."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget


class MeshViewport(QWidget):
    entitySelected = Signal(int, int)
    statusMessage = Signal(str)
    _PICK_CELL = 64
    _PICK_MAX_CELLS = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("meshViewport")
        self.setMinimumSize(380, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.points = []
        self.faces = []
        self.face_tags = []
        self.selected = set()
        self.projected_faces = []
        self.bounds = [[0, 0, 0], [1, 1, 1]]
        self.yaw = -.58
        self.pitch = .42
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.show_edges = True
        self.show_faces = True
        self._drag = None
        self._moved = False
        self.preview_label = "Aucune géométrie chargée"
        self.lod = False
        self._revision = 0
        self._vertices = []
        self._normals = []
        self._span = 1.0
        self._rotation_key = self._projection_key = self._image_key = None
        self._background_key = None
        self._rotated = {}
        self._ordered = []
        self._colors = []
        self._render_faces = []
        self._pick_cells = None
        self._pick_large = []
        self._mesh_image = self._background = None
        self._edge_pen = QPen(QColor("#496b87"), .7)
        self._selected_pen = QPen(QColor("#a96b00"), .7)
        self._selected_color = QColor("#f8b749")

    @staticmethod
    def _tag(value):
        try:
            return int(value.get("tag", 0)) if isinstance(value, dict) else int(value)
        except (TypeError, ValueError, OverflowError):
            return 0

    def set_preview(self, preview, *, label="Maillage calculé"):
        source = preview if isinstance(preview, dict) else {}
        # Own the display snapshot: later source mutations cannot silently make
        # the projection/picking cache refer to different mesh coordinates.
        self.points = [tuple(point) for point in source.get("points", [])]
        faces = list(source.get("surface_triangles", []))
        tags = list(source.get("face_tags", []))
        if not faces:
            tags = []
            for block in source.get("cells", []):
                kind = block.get("type", "")
                tag = self._tag(block.get("entity_tag", block.get("entity", 0)))
                if kind.startswith("triangle"):
                    for row in block.get("data", []):
                        faces.append(row[:3])
                        tags.append(tag)
                elif kind.startswith("quad"):
                    for row in block.get("data", []):
                        if len(row) >= 4:
                            faces.extend(([row[0], row[1], row[2]], [row[0], row[2], row[3]]))
                            tags.extend((tag, tag))
        step = max(1, math.ceil(len(faces) / 35000))
        self.lod = step > 1 or bool(source.get("truncated"))
        self.faces = []
        self.face_tags = []
        for index in range(0, len(faces), step):
            face = faces[index]
            if len(face) >= 3 and all(type(v) is int and 0 <= v < len(self.points) for v in face[:3]):
                self.faces.append(tuple(face[:3]))
                self.face_tags.append(self._tag(tags[index] if index < len(tags) else 0))
        bounds = source.get("bounds")
        if (isinstance(bounds, (list, tuple)) and len(bounds) == 2
                and all(isinstance(row, (list, tuple)) and len(row) == 3 for row in bounds)
                and all(type(x) in (int, float) and math.isfinite(x) for row in bounds for x in row)
                and all(bounds[0][d] <= bounds[1][d] for d in range(3))):
            self.bounds = [list(row) for row in bounds]
        elif self.points:
            low = list(self.points[0])
            high = list(self.points[0])
            for point in self.points[1:]:
                for d in range(3):
                    low[d] = min(low[d], point[d])
                    high[d] = max(high[d], point[d])
            self.bounds = [low, high]
        else:
            self.bounds = [[0, 0, 0], [1, 1, 1]]
        self._prepare_display_geometry()
        self.preview_label = label
        self.selected = set()
        self.fit()

    def _prepare_display_geometry(self):
        self._revision += 1
        center = [(a + b) / 2 for a, b in zip(*self.bounds)]
        self._span = max((b - a for a, b in zip(*self.bounds)), default=1) or 1
        used = {v for face in self.faces for v in face}
        self._vertices = [(index, tuple(self.points[index][d] - center[d] for d in range(3)))
                          for index in sorted(used)]
        self._normals = []
        for first, second, third in self.faces:
            a, b, c = self.points[first], self.points[second], self.points[third]
            ux, uy, uz = (b[d] - a[d] for d in range(3))
            vx, vy, vz = (c[d] - a[d] for d in range(3))
            normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
            length = math.sqrt(sum(x * x for x in normal)) or 1
            self._normals.append(tuple(x / length for x in normal))
        self._rotation_key = self._projection_key = self._image_key = None
        self._rotated = {}
        self._ordered = []
        self._colors = []
        self._render_faces = []
        self.projected_faces = []
        self._pick_cells = None
        self._pick_large = []
        self._mesh_image = None

    def clear(self):
        self.points = []
        self.faces = []
        self.face_tags = []
        self.selected = set()
        self.bounds = [[0, 0, 0], [1, 1, 1]]
        self.lod = False
        self._prepare_display_geometry()
        self.preview_label = "Aucune géométrie chargée"
        self.update()

    def set_selected_entities(self, entities):
        selected = {(int(dim), int(tag)) for dim, tag in entities}
        if selected != self.selected:
            self.selected = selected
            self.update()

    def fit(self):
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.update()

    def view_xy(self):
        self.yaw = self.pitch = 0.0
        self.fit()

    def view_iso(self):
        self.yaw, self.pitch = -.58, .42
        self.fit()

    def _rotate(self, point):
        x, y, z = point
        cy, sy, cp, sp = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
        u, depth = cy * x + sy * z, -sy * x + cy * z
        return u, cp * y - sp * depth, sp * y + cp * depth

    def _ensure_projection(self):
        rotation_key = (self._revision, self.yaw, self.pitch)
        if self._rotation_key != rotation_key:
            cy, sy, cp, sp = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
            def rotate(point):
                x, y, z = point
                u, depth = cy * x + sy * z, -sy * x + cy * z
                return u, cp * y - sp * depth, sp * y + cp * depth
            self._rotated = {index: rotate(point) for index, point in self._vertices}
            self._ordered = sorted(range(len(self.faces)),
                                   key=lambda i: sum(self._rotated[v][2] for v in self.faces[i]))
            self._colors = []
            for normal in self._normals:
                nx, ny, nz = rotate(normal)
                light = .68 + .25 * abs(nx * .25 + ny * .4 + nz * .86)
                self._colors.append(QColor(round(104 * light), round(160 * light), round(201 * light)))
            self._rotation_key = rotation_key
        key = (rotation_key, self.width(), self.height(), self.zoom, self.pan_x, self.pan_y)
        if self._projection_key == key:
            return
        scale = min(self.width(), self.height()) * .72 * self.zoom / self._span
        center_x, center_y = self.width() / 2 + self.pan_x, self.height() / 2 + self.pan_y
        projected = {index: QPointF(center_x + r[0] * scale, center_y - r[1] * scale)
                     for index, r in self._rotated.items()}
        self._render_faces = []
        self.projected_faces = []
        self._pick_cells = None
        self._pick_large = []
        width, height = self.width(), self.height()
        for index in self._ordered:
            polygon = QPolygonF([projected[v] for v in self.faces[index]])
            bounds = polygon.boundingRect()
            if bounds.right() < 0 or bounds.bottom() < 0 or bounds.left() > width or bounds.top() > height:
                continue
            tag = self.face_tags[index]
            self._render_faces.append((polygon, tag, self._colors[index]))
            self.projected_faces.append((polygon, tag))
        self._projection_key = key
        self._image_key = None

    def _mesh_pixmap(self):
        self._ensure_projection()
        dpr = self.devicePixelRatioF()
        key = (self._projection_key, bool(self.show_edges), bool(self.show_faces), frozenset(self.selected), dpr)
        if self._image_key != key:
            image = QPixmap(max(1, math.ceil(self.width() * dpr)), max(1, math.ceil(self.height() * dpr)))
            image.setDevicePixelRatio(dpr)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            for polygon, tag, color in self._render_faces:
                selected = (2, tag) in self.selected
                painter.setBrush((self._selected_color if selected else color) if self.show_faces else Qt.NoBrush)
                painter.setPen((self._selected_pen if selected else self._edge_pen) if self.show_edges else Qt.NoPen)
                painter.drawPolygon(polygon)
            painter.end()
            self._mesh_image = image
            self._image_key = key
        return self._mesh_image

    def _background_pixmap(self):
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr)
        if self._background_key != key:
            image = QPixmap(max(1, math.ceil(self.width() * dpr)), max(1, math.ceil(self.height() * dpr)))
            image.setDevicePixelRatio(dpr)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            gradient = QLinearGradient(0, 0, 0, self.height())
            gradient.setColorAt(0, QColor("#e6edf4"))
            gradient.setColorAt(1, QColor("#f7f9fc"))
            painter.fillRect(self.rect(), gradient)
            painter.setPen(QPen(QColor("#d7e0e9"), 1))
            for x in range(0, self.width(), 32): painter.drawLine(x, 0, x, self.height())
            for y in range(0, self.height(), 32): painter.drawLine(0, y, self.width(), y)
            painter.end()
            self._background, self._background_key = image, key
        return self._background

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(0, 0, self._background_pixmap())
        painter.setFont(QFont("Sans Serif", 10))
        painter.setPen(QColor("#53667a"))
        painter.drawText(22, 29, self.preview_label)
        if self.points:
            painter.drawPixmap(0, 0, self._mesh_pixmap())
            if self.lod:
                painter.setPen(QColor("#886d38"))
                painter.drawText(22, self.height() - 42, "Affichage allégé · le fichier exporté conserve tous les éléments")
        else:
            painter.setPen(QColor("#70849a"))
            painter.setFont(QFont("Sans Serif", 17))
            painter.drawText(self.rect(), Qt.AlignCenter, "Importez une pièce ou ouvrez un exemple\npour commencer")
        self._draw_axes(painter)
        painter.setFont(QFont("Sans Serif", 9))
        painter.setPen(QColor("#7b8b9b"))
        painter.drawText(22, self.height() - 20, "Glisser : tourner  ·  Maj + glisser : déplacer  ·  Molette : zoomer  ·  Clic : sélectionner une face")
        painter.end()

    def _draw_axes(self, painter):
        origin = QPointF(self.width() - 66, self.height() - 68)
        painter.setFont(QFont("Sans Serif", 10))
        for point, name, color in (((1, 0, 0), "X", "#c25c59"), ((0, 1, 0), "Y", "#41956f"), ((0, 0, 1), "Z", "#397eb9")):
            x, y, _ = self._rotate(point)
            end = origin + QPointF(33 * x, -33 * y)
            painter.setPen(QPen(QColor(color), 2))
            painter.drawLine(origin, end)
            painter.drawText(end + QPointF(3, 3), name)

    def _ensure_pick_index(self):
        if self._pick_cells is not None:
            return
        self._pick_cells = {}
        self._pick_large = []
        cell, width, height = self._PICK_CELL, self.width(), self.height()
        for face_index, (polygon, tag) in enumerate(self.projected_faces):
            if tag <= 0:
                continue
            bounds = polygon.boundingRect()
            x0, x1 = int(max(0, bounds.left()) // cell), int(min(width, bounds.right()) // cell)
            y0, y1 = int(max(0, bounds.top()) // cell), int(min(height, bounds.bottom()) // cell)
            if (x1 - x0 + 1) * (y1 - y0 + 1) > self._PICK_MAX_CELLS:
                self._pick_large.append(face_index)
            else:
                for y in range(y0, y1 + 1):
                    for x in range(x0, x1 + 1):
                        self._pick_cells.setdefault((x, y), []).append(face_index)

    def _pick_entity(self, position):
        # Picking data is created only when requested, keeping camera movement
        # independent of screen-index construction.
        self._ensure_projection()
        self._ensure_pick_index()
        cell = (int(position.x() // self._PICK_CELL), int(position.y() // self._PICK_CELL))
        local = self._pick_cells.get(cell, ())
        # The lists contain painter-order indices. A large triangle is indexed
        # once instead of being copied into every screen cell at high zoom.
        candidates = sorted((*local, *self._pick_large), reverse=True)
        for index in candidates:
            polygon, tag = self.projected_faces[index]
            if polygon.containsPoint(position, Qt.OddEvenFill):
                return tag
        return None

    def mousePressEvent(self, event):
        self._drag = (event.position(), event.button(), event.modifiers())
        self._moved = False

    def mouseMoveEvent(self, event):
        if self._drag is None: return
        previous, button, modifiers = self._drag
        delta = event.position() - previous
        if abs(delta.x()) + abs(delta.y()) > 2: self._moved = True
        if button == Qt.RightButton or modifiers & Qt.ShiftModifier:
            self.pan_x += delta.x()
            self.pan_y += delta.y()
        else:
            self.yaw += delta.x() * .008
            self.pitch += delta.y() * .008
        self._drag = (event.position(), button, modifiers)
        self.update()

    def mouseReleaseEvent(self, event):
        if not self._moved and event.button() == Qt.LeftButton:
            tag = self._pick_entity(event.position())
            if tag is not None:
                self.entitySelected.emit(2, tag)
                self.statusMessage.emit(f"Face {tag} sélectionnée")
        self._drag = None

    def wheelEvent(self, event):
        self.zoom = min(20.0, max(.08, self.zoom * (1.15 if event.angleDelta().y() > 0 else 1 / 1.15)))
        self.update()
