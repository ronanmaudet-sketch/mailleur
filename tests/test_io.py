"""Python file retrieval/export tests; numerical verification is delegated."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from mailleur.io import atomic_write, load_case, load_json, write_json


class FileRetrievalAndExport(unittest.TestCase):
    def test_strict_json_and_atomic_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.json"
            atomic_write(path, '{"value": 1}'); write_json(path, {"value": 2})
            self.assertEqual(load_json(path), {"value": 2})
            self.assertFalse(list(Path(folder).glob("*.tmp")))
            for text in ('{"value": NaN}', '{"value": 1e400}', '{"value": 1, "value": 2}'):
                path.write_text(text)
                with self.assertRaises(ValueError): load_json(path)
            with self.assertRaises(ValueError): write_json(path, {"value": float("inf")})

    def test_case_retrieval_preserves_coordinates_without_geometry_computation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "case.json"
            ring = [[1, 0], [0, 0], [0, 1], [1, 1]]
            write_json(path, {"points": ring, "target_size": .5})
            with patch("mailleur.geometry.prepare_polygon", side_effect=AssertionError("Geometry belongs to Rust")):
                case = load_case(path)
            self.assertEqual(case["points"], [tuple(point) for point in ring])
            self.assertEqual(load_case(path, size_override=.7)["target_size"], .7)

    def test_export_serializes_existing_dto_and_uses_native_quality(self):
        from mailleur.export import export_mesh, read_mesh, svg_text
        from mailleur.meshing import Mesh
        from tests.test_python_boundary import rust_metrics
        mesh = Mesh([(0, 0), (1, 0), (1, 1), (0, 1)], [(0, 1, 2, 3)], [(0, 1), (1, 2), (2, 3), (3, 0)])
        with tempfile.TemporaryDirectory() as folder, patch("atelier.native.NativeSession.query", return_value=rust_metrics()):
            files = export_mesh(mesh, Path(folder) / "piece", '<Pièce & contrôle>')
            restored = read_mesh(files[0])
            self.assertEqual(restored.points, mesh.points)
            self.assertEqual(restored.quads, mesh.quads)
            root = ET.fromstring(svg_text(mesh, '<Pièce & contrôle>'))
            self.assertEqual(root.find('{http://www.w3.org/2000/svg}title').text, '<Pièce & contrôle>')
            self.assertEqual(len(root.findall('.//{http://www.w3.org/2000/svg}polygon')), 1)
            self.assertTrue(files[1].read_text().startswith('$MeshFormat'))

    def test_export_stops_when_native_connectivity_is_rejected(self):
        from mailleur.export import export_mesh
        from mailleur.meshing import Mesh
        with tempfile.TemporaryDirectory() as folder, patch("atelier.native.NativeSession.query", side_effect=RuntimeError("Indice hors plage")):
            with self.assertRaisesRegex(ValueError, "Indice hors plage"):
                export_mesh(Mesh([(0, 0)], [(0, 1, 2, 3)], []), Path(folder) / "bad")
            self.assertFalse((Path(folder) / "bad.msh").exists())
