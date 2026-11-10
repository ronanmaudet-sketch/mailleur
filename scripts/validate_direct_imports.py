"""Essais réels des imports CAO standard et des suffixes de maillage.

Les sources synthétiques sont créées par Gmsh/OpenCASCADE ou meshio ; chaque
import et remaillage passe ensuite par NativeSession et le noyau Rust réel.
Aucun logiciel natif propriétaire ni aucune version de fichier universelle
n'est certifié par ce banc.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atelier import __version__
from atelier.formats import _read
from atelier.native import NativeSession, core_path
from mailleur.io import write_json

MINIMUM = [0.007, -0.003, 0.011]
MAXIMUM = [0.009, -0.002, 0.012]


def declare_iges_metres(path):
    """Change only the synthetic fixture's IGES Global unit MM -> M.

    The supplied geometry already contains metre coordinate values. IGES
    Global parameters 14/15 are unit flag/name; flag 6 denotes metres.
    No production reader rewrites received CAD headers.
    """
    lines = path.read_text(encoding='ascii').splitlines()
    indices = [i for i, line in enumerate(lines) if len(line) > 72 and line[72] == 'G']
    data = ''.join(lines[i][:72] for i in indices)
    if data.count(',2,2HMM,') != 1:
        raise ValueError('La fixture IGES ne contient pas le marqueur mm attendu')
    data = data.replace(',2,2HMM,', ',6,1HM,').rstrip()
    chunks = [data[i:i + 72] for i in range(0, len(data), 72)]
    if len(chunks) != len(indices):
        raise ValueError('La modification changerait le nombre de lignes Global IGES')
    for number, (index, chunk) in enumerate(zip(indices, chunks), 1):
        lines[index] = chunk.ljust(72) + 'G' + f'{number:7d}'
    path.write_text('\n'.join(lines) + '\n', encoding='ascii')


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def make_fixtures(directory):
    import gmsh
    import meshio
    import numpy as np
    directory.mkdir(parents=True, exist_ok=True)
    gmsh.initialize(['fixture', '-nopopup'], readConfigFiles=False)
    try:
        gmsh.option.setNumber('General.Terminal', 0)
        gmsh.model.add('offset-box-mm')
        gmsh.model.occ.addBox(7., -3., 11., 2., 1., 1.)
        gmsh.model.occ.synchronize()
        for extension in ('.step', '.iges', '.brep'):
            gmsh.write(str(directory / ('box_mm' + extension)))
        shutil.copyfile(directory / 'box_mm.step', directory / 'box_mm.stp')
        shutil.copyfile(directory / 'box_mm.iges', directory / 'box_mm.igs')
        # BREP has no declared coordinate unit: a second source really contains
        # metre coordinates, unlike STEP/IGES whose mm header is authoritative.
        gmsh.clear()
        gmsh.model.add('offset-box-metres')
        gmsh.model.occ.addBox(.007, -.003, .011, .002, .001, .001)
        gmsh.model.occ.synchronize()
        gmsh.write(str(directory / 'box_m.brep'))
        gmsh.write(str(directory / 'box_m.iges'))
        declare_iges_metres(directory / 'box_m.iges')
        shutil.copyfile(directory / 'box_m.iges', directory / 'box_m.igs')
    finally:
        gmsh.finalize()
    points = np.array([[7., -3., 11.], [9., -3., 11.], [7., -2., 11.], [7., -3., 12.]])
    for unit, scale in [('mm', 1.), ('m', .001)]:
        for extension, writer in [('.meshb', 'medit'), ('.nas', 'nastran'), ('.fem', 'nastran')]:
            meshio.write(directory / ('tetra_' + unit + extension),
                         meshio.Mesh(points * scale, [('tetra', [[0, 1, 2, 3]])]), file_format=writer)
    return {'gmsh': gmsh.__version__, 'meshio': meshio.__version__,
            'sources': [{'file': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)}
                        for path in sorted(directory.iterdir()) if path.is_file()]}


def run_case(output, source, work_unit, order, *, mode='volume', repair=False, timeout=60.):
    import numpy as np
    name = source.stem + '_' + source.suffix[1:] + '_' + work_unit + '_p' + str(order) + '_' + mode + ('_heal' if repair else '')
    directory = output / name
    directory.mkdir(parents=True, exist_ok=True)
    work_factor = .001 if work_unit == 'mm' else 1.
    project = {'schema_version': 1, 'name': 'Validation import ' + name,
               'mode': mode, 'geometry': {'kind': 'import'},
               'units': {'input': work_unit, 'internal': 'm'},
               'mesh': {'size': .0004 / work_factor, 'min_size': .0001 / work_factor,
                        'order': order, 'threads': 1, 'max_elements': 20000},
               'repair': {'enabled': repair, 'sew': True, 'make_solids': repair,
                          'small_edges': True, 'small_faces': False,
                          'tolerance': 1e-10 / work_factor}}
    write_json(directory / 'project.json', project)
    source_before = digest(source)
    started = time.perf_counter()
    session = NativeSession(directory / 'workspace', timeout=timeout)
    result = session.generate(project, source)
    elapsed = time.perf_counter() - started
    native_dir = Path(result['directory'])
    metadata = json.loads((native_dir / 'session.json').read_text())
    mesh, _ = _read(native_dir / 'mesh.msh')
    actual_min = np.min(mesh.points, axis=0).tolist()
    actual_max = np.max(mesh.points, axis=0).tolist()
    expected_domain = ('tetra' if order == 1 else 'tetra10') if mode == 'volume' else ('triangle' if order == 1 else 'triangle6')
    counts = {}
    for block in mesh.cells:
        counts[block.type] = counts.get(block.type, 0) + len(block.data)
    measure = 0.
    for block in mesh.cells:
        if block.type != expected_domain:
            continue
        corners = mesh.points[block.data[:, :4 if mode == 'volume' else 3]]
        if mode == 'volume':
            edges = corners[:, 1:] - corners[:, :1]
            measure += float(np.sum(np.abs(np.linalg.det(edges))) / 6)
        else:
            measure += float(np.sum(np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0],
                                                            corners[:, 2] - corners[:, 0]), axis=1)) / 2)
    expected_measure = (2e-9 / (6 if source.stem.startswith('tetra_') else 1)) if mode == 'volume' else 1e-5
    checks = {
        'source_sha256_unchanged': digest(source) == source_before,
        'session_original_copy_identical': digest(native_dir / metadata['source']) == source_before,
        'accepted_quality': result['report']['quality']['accepted'] is True,
        'expected_domain_elements_present': counts.get(expected_domain, 0) > 0,
        'minimum_coordinates_and_origin': bool(np.all(np.abs(np.array(actual_min) - MINIMUM) <= 1e-11)),
        'maximum_coordinates_and_dimensions': bool(np.all(np.abs(np.array(actual_max) - MAXIMUM) <= 1e-11)),
        'internal_units_metres': result['report']['units']['internal'] == 'm',
        'requested_mesh_order': int(result['report']['mesh_settings']['order']) == order,
        'analytical_volume_or_surface_area': abs(measure - expected_measure) <= 1e-9 * expected_measure,
    }
    row = {'case': name, 'source': source.name, 'source_sha256': source_before,
           'input_unit': work_unit, 'order': order, 'mode': mode, 'repair_requested': repair,
           'elapsed_seconds': elapsed, 'native_directory': str(native_dir.relative_to(directory)),
           'nodes': len(mesh.points), 'cell_counts': counts,
           'geometric_measure': {'kind': 'volume_m3' if mode == 'volume' else 'area_m2',
                                 'actual': measure, 'expected': expected_measure, 'relative_tolerance': 1e-9},
           'coordinates_metres': {'minimum': actual_min, 'maximum': actual_max,
                                  'expected_minimum': MINIMUM, 'expected_maximum': MAXIMUM,
                                  'absolute_tolerance': 1e-11},
           'warnings': result['report'].get('warnings', []),
           'geometry_diagnostics': result['report']['geometry_diagnostics'],
           'quality': result['report']['quality'], 'checks': checks, 'passed': all(checks.values())}
    write_json(directory / 'result.json', row)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/extended_20261107/direct_imports')
    parser.add_argument('--timeout', type=float, default=60.)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixture_dir = output / 'fixtures'
    summary = {'schema': 'atelier.validation.direct_imports.v1',
               'generated_at_utc': datetime.now(timezone.utc).isoformat(),
               'atelier_version': __version__, 'python': platform.python_version(),
               'machine': {'architecture': platform.machine(), 'macos': platform.mac_ver()[0]},
               'native_kernel_sha256': digest(core_path()),
               'native_kernel_version': subprocess.run([str(core_path()), '--version'], check=True,
                    capture_output=True, text=True).stdout.strip(),
               'fixtures': make_fixtures(fixture_dir), 'cases': []}
    cases = []
    for extension in ('.step', '.stp', '.iges', '.igs', '.brep', '.meshb', '.nas', '.fem'):
        for unit in ('mm', 'm'):
            if extension in ('.step', '.stp', '.iges', '.igs'):
                source = fixture_dir / ('box_mm' + extension)
            else:
                source = fixture_dir / (('box_' if extension == '.brep' else 'tetra_') + unit + extension)
            for order in (1, 2):
                cases.append((source, unit, order, 'surface' if extension in ('.iges', '.igs') else 'volume', False))
    # IGES exports generally contain trimmed surfaces. Explicit sewing/solid
    # construction is requested separately, never silently enabled for a user.
    for extension in ('.iges', '.igs'):
        for order in (1, 2):
            cases.append((fixture_dir / ('box_mm' + extension), 'mm', order, 'volume', True))
        for unit in ('mm', 'm'):
            for order in (1, 2):
                cases.append((fixture_dir / ('box_m' + extension), unit, order, 'surface', False))
        for order in (1, 2):
            cases.append((fixture_dir / ('box_m' + extension), 'mm', order, 'volume', True))
    for source, unit, order, mode, repair in cases:
        try:
            row = run_case(output, source, unit, order, mode=mode, repair=repair, timeout=args.timeout)
        except Exception as error:
            row = {'source': source.name, 'input_unit': unit, 'order': order, 'mode': mode,
                   'repair_requested': repair, 'error': str(error),
                   'error_type': type(error).__name__, 'passed': False}
        summary['cases'].append(row)
        write_json(output / 'summary.json', summary)
        print(json.dumps({key: row[key] for key in ('source', 'input_unit', 'order', 'mode', 'passed')} |
                         {'error': row.get('error')}, ensure_ascii=False), flush=True)
    summary['passed'] = all(row['passed'] for row in summary['cases'])
    summary['scope'] = ('Imports/remaillages exécutés par le noyau Rust réel de géométries synthétiques décentrées ; '
        'STEP/STP à en-tête mm, IGES/IGS à en-tête mm/m, BREP et maillages sans unité explicite en mm/m, ordres 1/2. '
        'Ne certifie pas les CAO propriétaires, toutes versions ni les géométries industrielles arbitraires.')
    write_json(output / 'summary.json', summary)
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
