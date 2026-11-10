"""Régression réelle : premier import IGES, en-tête et unités de travail."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from atelier.formats import _read
from atelier.native import NativeSession, core_path, gmsh_library
from scripts.validate_direct_imports import declare_iges_metres


class IgesUnitIntegration(unittest.TestCase):
    def test_first_iges_import_preserves_declared_units_for_both_orders(self):
        import gmsh
        import numpy as np
        core_path()
        gmsh_library()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Fixture generation happens in this Python process. Each tested
            # NativeSession call starts a fresh Rust process with an unprimed
            # OCC IGES unit registry, where the original defect occurred.
            gmsh.initialize(['fixture', '-nopopup'], readConfigFiles=False)
            try:
                gmsh.option.setNumber('General.Terminal', 0)
                for unit, scale in [('mm', 1.), ('m', .001)]:
                    gmsh.clear()
                    gmsh.model.add('offset-' + unit)
                    gmsh.model.occ.addBox(7 * scale, -3 * scale, 11 * scale,
                                         2 * scale, scale, scale)
                    gmsh.model.occ.synchronize()
                    gmsh.write(str(root / ('box_' + unit + '.iges')))
            finally:
                gmsh.finalize()
            declare_iges_metres(root / 'box_m.iges')
            session = NativeSession(root / 'workspace', timeout=30)
            for source_unit in ('mm', 'm'):
                original = root / ('box_' + source_unit + '.iges')
                for work_unit, work_scale in [('mm', .001), ('m', 1.)]:
                    for order in (1, 2):
                        with self.subTest(source_unit=source_unit, work_unit=work_unit, order=order):
                            source = original
                            if order == 2:
                                source = original.with_suffix('.igs')
                                source.write_bytes(original.read_bytes())
                            before = hashlib.sha256(source.read_bytes()).hexdigest()
                            project = {'schema_version': 1, 'name': 'IGES units regression',
                                'mode': 'surface', 'geometry': {'kind': 'import'},
                                'units': {'input': work_unit, 'internal': 'm'},
                                'mesh': {'size': .0004 / work_scale, 'min_size': .0001 / work_scale,
                                         'order': order, 'threads': 1, 'max_elements': 20000}}
                            result = session.generate(project, source)
                            mesh, _ = _read(Path(result['directory']) / 'mesh.msh')
                            np.testing.assert_allclose(np.min(mesh.points, axis=0), [.007, -.003, .011],
                                                       rtol=0., atol=1e-11)
                            np.testing.assert_allclose(np.max(mesh.points, axis=0), [.009, -.002, .012],
                                                       rtol=0., atol=1e-11)
                            self.assertTrue(result['report']['quality']['accepted'])
                            self.assertGreater(sum(len(c.data) for c in mesh.cells
                                if c.type == ('triangle' if order == 1 else 'triangle6')), 0)
                            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
