"""Rendering/picking regression checks; no meshing is performed in Python."""
from copy import deepcopy
import importlib.util
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def grid_preview(size=16):
    points = [[x, y, .08 * ((x + y) % 3)] for y in range(size + 1) for x in range(size + 1)]
    faces, tags = [], []
    for y in range(size):
        for x in range(size):
            a = y * (size + 1) + x
            faces.extend(([a, a + 1, a + size + 2], [a, a + size + 2, a + size + 1]))
            tags.extend((1 + y * size + x, 1 + y * size + x))
    return {"points": points, "surface_triangles": faces, "face_tags": tags}


class ViewportCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("PySide6") is None:
            raise unittest.SkipTest("PySide6 absent")
        from PySide6.QtWidgets import QApplication
        from atelier.viewport import MeshViewport
        cls.application = QApplication.instance() or QApplication([])
        cls.viewport_type = MeshViewport

    def setUp(self):
        self.view = self.viewport_type()
        self.view.resize(800, 600)
        self.view.show()
        self.application.processEvents()
        self.addCleanup(self.view.close)

    def image(self):
        self.application.processEvents()
        return self.view.grab().toImage()

    def test_preview_fallback_preserves_source_and_owns_its_display_snapshot(self):
        preview = {"points": [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]],
                   "surface_triangles": [], "cells": [{"type": "quad", "data": [[0, 1, 2, 3]], "entity_tag": 7}]}
        before = deepcopy(preview)
        self.view.set_preview(preview)
        self.assertEqual(preview, before)
        self.assertEqual(len(self.view.faces), 2)
        self.assertEqual(self.view.face_tags, [7, 7])
        preview["points"][0][0] = 100
        preview["cells"][0]["data"][0][0] = 3
        self.assertEqual(self.view.points[0], (0, 0, 0))
        self.assertEqual(self.view.faces[0], (0, 1, 2))

    def test_repaint_selection_and_edge_changes_keep_pixels_current(self):
        self.view.set_preview(grid_preview(12))
        original = self.image()
        self.assertEqual(original, self.image())
        self.view.set_selected_entities([(2, 1), (2, 2)])
        selected = self.image()
        self.assertNotEqual(original, selected)
        self.assertEqual(selected, self.image())
        self.view.set_selected_entities([])
        self.assertEqual(original, self.image())
        self.view.show_edges = False
        self.view.update()
        self.assertNotEqual(original, self.image())
        self.view.show_edges = True
        self.view.update()
        self.assertEqual(original, self.image())
        self.view.show_faces = False
        self.view.update()
        self.assertNotEqual(original, self.image())
        self.view.show_faces = True
        self.view.update()
        self.assertEqual(original, self.image())
        self.view.zoom = 1.3
        self.view.update()
        self.assertNotEqual(original, self.image())
        self.view.fit()
        self.assertEqual(original, self.image())

    def test_picking_returns_frontmost_face_without_waiting_for_paint(self):
        from PySide6.QtCore import QPointF
        preview = {"points": [[-1, -1, 0], [1, -1, 0], [0, 1, 0],
                              [-1, -1, 1], [1, -1, 1], [0, 1, 1]],
                   "surface_triangles": [[0, 1, 2], [3, 4, 5]], "face_tags": [4, 9]}
        self.view.set_preview(preview)
        self.view.view_xy()
        self.assertEqual(self.view._pick_entity(QPointF(400, 300)), 9)
        self.view.pan_x = 120
        self.assertEqual(self.view._pick_entity(QPointF(520, 300)), 9)
        self.view.pan_x = 0
        self.view.resize(1000, 600)
        self.assertEqual(self.view._pick_entity(QPointF(500, 300)), 9)
        self.view.clear()
        self.assertIsNone(self.view._pick_entity(QPointF(520, 300)))
        self.assertEqual(self.view.projected_faces, [])
        self.view.set_preview(None)
        self.assertIsNone(self.view._pick_entity(QPointF(400, 300)))

    def test_screen_index_matches_exhaustive_depth_order_after_camera_changes(self):
        from PySide6.QtCore import QPointF, Qt
        self.view.set_preview(grid_preview(20))
        for yaw, pitch, zoom, pan_x, pan_y, width, height in [
                (0, 0, 1, 0, 0, 800, 600),
                (.2, .3, 1.4, 50, -20, 880, 650),
                (-1, .9, 5, -75, 90, 800, 600),
                (.4, -.4, 20, 15, 0, 800, 600)]:
            self.view.resize(width, height)
            self.view.yaw, self.view.pitch, self.view.zoom = yaw, pitch, zoom
            self.view.pan_x, self.view.pan_y = pan_x, pan_y
            self.view._ensure_projection()
            for y in range(17, height, 57):
                for x in range(13, width, 61):
                    point = QPointF(x, y)
                    expected = next((tag for polygon, tag in reversed(self.view.projected_faces)
                                     if tag > 0 and polygon.containsPoint(point, Qt.OddEvenFill)), None)
                    self.assertEqual(self.view._pick_entity(point), expected)
            memberships = sum(len(rows) for rows in self.view._pick_cells.values())
            self.assertLessEqual(memberships, len(self.view.projected_faces) * self.view._PICK_MAX_CELLS)

    def test_lod_is_preserved_and_source_is_not_silently_truncated(self):
        preview = grid_preview(140)
        original_faces = deepcopy(preview["surface_triangles"])
        self.view.set_preview(preview)
        self.assertTrue(self.view.lod)
        self.assertLessEqual(len(self.view.faces), 35000)
        self.assertEqual(preview["surface_triangles"], original_faces)

    def test_high_dpi_cache_keeps_the_logical_view_size(self):
        self.view.set_preview(grid_preview(4))
        first = self.view._mesh_pixmap()
        with patch.object(self.view, "devicePixelRatioF", return_value=2.0):
            retina = self.view._mesh_pixmap()
            self.assertEqual(retina.devicePixelRatioF(), 2.0)
            self.assertEqual(retina.width(), self.view.width() * 2)
            self.assertEqual(retina.height(), self.view.height() * 2)
            self.assertEqual(retina.deviceIndependentSize().width(), self.view.width())
        restored = self.view._mesh_pixmap()
        self.assertEqual(restored.devicePixelRatioF(), first.devicePixelRatioF())


if __name__ == "__main__":
    unittest.main()
