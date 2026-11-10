"""Catalogue des entrées, sans charger de bibliothèque numérique ou de CAO."""
from pathlib import Path
import re

CAD_EXTENSIONS = frozenset({".step", ".stp", ".iges", ".igs", ".brep"})
NATIVE_GMSH_EXTENSIONS = frozenset({".stl", ".unv"})
MESH_READERS = {
    ".obj": "obj", ".ply": "ply", ".off": "off", ".vtu": "vtu",
    ".vtk": "vtk", ".msh": "gmsh", ".inp": "abaqus", ".med": "med",
    ".bdf": "nastran", ".nas": "nastran", ".fem": "nastran",
    ".mesh": "medit", ".meshb": "medit", ".vol": "netgen",
}
CONVERTER_EXTENSIONS = frozenset({
    ".sldprt", ".sldasm", ".catpart", ".catproduct", ".ipt", ".iam",
    ".prt", ".asm", ".x_t", ".x_b", ".sat", ".sab", ".jt", ".3dxml",
    ".fcstd", ".3dm", ".dwg", ".dxf", ".fbx", ".gltf", ".glb", ".dae", ".3ds",
})
SURFACE_CONTAINER_EXTENSIONS = frozenset({".3mf"})
PACKAGE_EXTENSIONS = frozenset({".cadpack"})
SOURCE_EXTENSIONS = (CAD_EXTENSIONS | NATIVE_GMSH_EXTENSIONS | set(MESH_READERS)
                     | CONVERTER_EXTENSIONS | SURFACE_CONTAINER_EXTENSIONS | PACKAGE_EXTENSIONS)


def source_extension(name):
    """Les versions Creo piece.prt.12 gardent le rôle .prt."""
    value = str(name).lower()
    versioned = re.search(r"\.(prt|asm)\.\d+$", value)
    return "." + versioned[1] if versioned else Path(value).suffix


def file_dialog_filter():
    def patterns(extensions):
        return " ".join("*" + extension for extension in sorted(extensions))
    return ";;".join((
        "Formats reconnus (" + patterns(SOURCE_EXTENSIONS) + ")",
        "CAO standard (" + patterns(CAD_EXTENSIONS) + ")",
        "Surfaces et maillages (" + patterns(set(MESH_READERS) | NATIVE_GMSH_EXTENSIONS | SURFACE_CONTAINER_EXTENSIONS) + ")",
        "Formats avec convertisseur local (" + patterns(CONVERTER_EXTENSIONS) + ")",
        "Assemblages portables (*.cadpack)",
        "Tous les fichiers (*)",
    ))
