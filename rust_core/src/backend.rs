//! Native numerical pipeline. Python never participates in geometric calculations.
//! Gmsh/OpenCASCADE remains an explicitly identified native CAD/meshing dependency.
use crate::gmsh::{DimTag, Gmsh};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

const MAX_PREVIEW_FACES: usize = 250_000;
type R<T> = Result<T, String>;

fn number(value: Option<&Value>, default: f64, name: &str, positive: bool) -> R<f64> {
    let x = match value {
        Some(v) => v
            .as_f64()
            .ok_or_else(|| format!("{name} doit être un nombre"))?,
        None => default,
    };
    if !x.is_finite() || (positive && x <= 0.0) {
        return Err(format!("{name} doit être fini et strictement positif"));
    }
    Ok(x)
}
fn n(v: &Value, key: &str, default: f64, positive: bool) -> R<f64> {
    number(v.get(key), default, key, positive)
}
fn integer(v: &Value, key: &str, default: u64, max: u64) -> R<u64> {
    let x = match v.get(key) {
        Some(v) => v
            .as_u64()
            .ok_or_else(|| format!("{key} doit être entier"))?,
        None => default,
    };
    if x == 0 || x > max {
        return Err(format!("{key} hors limites 1..{max}"));
    }
    Ok(x)
}
fn flag(v: &Value, key: &str, default: bool) -> R<bool> {
    match v.get(key) {
        Some(x) => x
            .as_bool()
            .ok_or_else(|| format!("{key} doit être booléen")),
        None => Ok(default),
    }
}
fn nonnegative_integer(v: &Value, key: &str, default: u64, max: u64) -> R<u64> {
    let value = match v.get(key) {
        Some(v) => v.as_u64().ok_or_else(|| format!("{key} doit être un entier positif ou nul"))?,
        None => default,
    };
    if value > max { return Err(format!("{key} hors limites 0..{max}")); }
    Ok(value)
}
fn mesh_tuning(api: &Gmsh, mesh: &Value) -> R<()> {
    api.set_number("Mesh.Smoothing", nonnegative_integer(mesh,"smoothing",1,100)? as f64)?;
    api.set_number("Mesh.Optimize", if flag(mesh,"optimize",true)? {1.} else {0.})?;
    api.set_number("Mesh.MeshSizeFromCurvature", nonnegative_integer(mesh,"curvature_elements",16,100)? as f64)?;
    Ok(())
}
fn has_spatial_fields(project: &Value) -> bool {
    ["local_sizes", "refinement_regions"].iter().any(|key| project[*key].as_array().is_some_and(|a|!a.is_empty()))
}

fn vec3(v: Option<&Value>, default: [f64; 3], label: &str) -> R<[f64; 3]> {
    let Some(v) = v else { return Ok(default) };
    let a = v
        .as_array()
        .filter(|a| a.len() == 3)
        .ok_or_else(|| format!("{label} demande trois coordonnées"))?;
    Ok([
        number(a.first(), 0., label, false)?,
        number(a.get(1), 0., label, false)?,
        number(a.get(2), 0., label, false)?,
    ])
}
/// Ball fields provide a local scalar size with a transition of radius/4.
fn refinement_fields(api: &Gmsh, project: &Value, factor: f64, global_size: f64) -> R<Vec<f64>> {
    let mut fields = Vec::new();
    if let Some(raw) = project.get("refinement_regions") {
        let rows = raw.as_array().ok_or("refinement_regions doit être une liste")?;
        if rows.len() > 256 { return Err("Limite de 256 régions de raffinement dépassée".into()); }
        let min_size = n(&project["mesh"],"min_size",(global_size/factor).min(0.1),true)? * factor;
        for row in rows {
            if !row.is_object() { return Err("Chaque région de raffinement doit être un objet".into()); }
            let center = vec3(row.get("center"), [f64::NAN;3], "Centre de raffinement")?;
            if center.iter().any(|v|!v.is_finite()) { return Err("Un centre de raffinement à trois coordonnées est requis".into()); }
            let radius = n(row,"radius",f64::NAN,true)? * factor;
            let size = n(row,"size",f64::NAN,true)? * factor;
            if !radius.is_finite() || !size.is_finite() || center.iter().any(|v|!(v*factor).is_finite()) {
                return Err("Région de raffinement hors plage".into());
            }
            if size < min_size {
                return Err("Une taille de raffinement est inférieure à la taille minimale globale ; abaisser d'abord cette limite".into());
            }
            let field = api.mesh_field_add("Ball")?;
            for (name, value) in [("XCenter",center[0]*factor),("YCenter",center[1]*factor),
                ("ZCenter",center[2]*factor),("Radius",radius),("Thickness",radius*0.25),
                ("VIn",size),("VOut",global_size)] {
                api.mesh_field_set_number(field,name,value)?;
            }
            fields.push(field as f64);
        }
    }
    Ok(fields)
}
fn write_json(path: &Path, value: &Value) -> R<()> {
    let bytes = serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?;
    fs::write(path, bytes).map_err(|e| format!("Écriture {} : {e}", path.display()))
}
fn unit_factor(name: &str) -> R<f64> {
    match name {
        "m" => Ok(1.),
        "mm" => Ok(0.001),
        "cm" => Ok(0.01),
        "in" | "inch" => Ok(0.0254),
        _ => Err("Unités acceptées : m, cm, mm, in".into()),
    }
}
fn counts(api: &Gmsh) -> R<Value> {
    let mut result = serde_json::Map::new();
    for dim in 0..4 {
        result.insert(dim.to_string(), json!(api.entities(dim)?.len()));
    }
    Ok(Value::Object(result))
}

fn cross(a: [f64; 2], b: [f64; 2], c: [f64; 2]) -> f64 {
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}
fn area(ring: &[[f64; 2]]) -> f64 {
    let o = ring[0];
    (0..ring.len())
        .map(|i| cross(o, ring[i], ring[(i + 1) % ring.len()]))
        .sum::<f64>()
        * 0.5
}
fn on_segment(a: [f64; 2], b: [f64; 2], p: [f64; 2], eps: f64) -> bool {
    cross(a, b, p).abs() <= eps
        && p[0] >= a[0].min(b[0]) - eps
        && p[0] <= a[0].max(b[0]) + eps
        && p[1] >= a[1].min(b[1]) - eps
        && p[1] <= a[1].max(b[1]) + eps
}
fn intersects(a: [f64; 2], b: [f64; 2], c: [f64; 2], d: [f64; 2], eps: f64) -> bool {
    let ab_c = cross(a, b, c);
    let ab_d = cross(a, b, d);
    let cd_a = cross(c, d, a);
    let cd_b = cross(c, d, b);
    (ab_c * ab_d < -eps * eps && cd_a * cd_b < -eps * eps)
        || on_segment(a, b, c, eps)
        || on_segment(a, b, d, eps)
        || on_segment(c, d, a, eps)
        || on_segment(c, d, b, eps)
}
fn inside(p: [f64; 2], ring: &[[f64; 2]]) -> bool {
    let mut result = false;
    for i in 0..ring.len() {
        let a = ring[i];
        let b = ring[(i + 1) % ring.len()];
        if (a[1] > p[1]) != (b[1] > p[1])
            && p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
        {
            result = !result;
        }
    }
    result
}
fn ring(value: &Value) -> R<Vec<[f64; 2]>> {
    let values = value
        .as_array()
        .ok_or("Contour : liste de coordonnées attendue")?;
    if values.len() < 3 || values.len() > 5001 {
        return Err("Le contour doit contenir de 3 à 5000 sommets".into());
    }
    let mut points = Vec::new();
    for p in values {
        let a = p
            .as_array()
            .filter(|p| p.len() == 2)
            .ok_or("Un sommet demande [x,y]")?;
        points.push([
            number(a.first(), 0., "x", false)?,
            number(a.get(1), 0., "y", false)?,
        ]);
    }
    if points.len() > 3 && points.first() == points.last() {
        points.pop();
    }
    let xmin = points.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min);
    let ymin = points.iter().map(|p| p[1]).fold(f64::INFINITY, f64::min);
    let scale = points
        .iter()
        .map(|p| (p[0] - xmin).max(p[1] - ymin))
        .fold(0., f64::max);
    if scale <= 0. || !scale.is_finite() {
        return Err("Contour dégénéré".into());
    }
    let norm: Vec<_> = points
        .iter()
        .map(|p| [(p[0] - xmin) / scale, (p[1] - ymin) / scale])
        .collect();
    let eps = 2e-14;
    for i in 0..norm.len() {
        if cross(
            norm[(i + norm.len() - 1) % norm.len()],
            norm[i],
            norm[(i + 1) % norm.len()],
        )
        .abs()
            <= eps
        {
            return Err("Contour : sommets consécutifs alignés ou trop proches".into());
        }
        for j in i + 1..norm.len() {
            if j == i + 1 || (i == 0 && j == norm.len() - 1) {
                continue;
            }
            if intersects(
                norm[i],
                norm[(i + 1) % norm.len()],
                norm[j],
                norm[(j + 1) % norm.len()],
                eps,
            ) {
                return Err("Contour auto-intersecté ou se touchant".into());
            }
        }
    }
    if area(&norm).abs() <= eps {
        return Err("Contour d'aire nulle".into());
    }
    if area(&norm) < 0. {
        points.reverse();
    }
    Ok(points)
}
fn validated_rings(g: &Value) -> R<(Vec<[f64; 2]>, Vec<Vec<[f64; 2]>>)> {
    let outer = ring(g.get("outer").ok_or("Contour extérieur absent")?)?;
    let holes = if let Some(v) = g.get("holes") {
        v.as_array()
            .ok_or("Liste des trous attendue")?
            .iter()
            .map(ring)
            .collect::<R<Vec<_>>>()?
    } else {
        Vec::new()
    };
    if holes.len() > 1000 {
        return Err("Plus de 1000 trous".into());
    }
    let xmin = outer.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min);
    let ymin = outer.iter().map(|p| p[1]).fold(f64::INFINITY, f64::min);
    let scale = outer
        .iter()
        .map(|p| (p[0] - xmin).max(p[1] - ymin))
        .fold(0., f64::max);
    let all: Vec<_> = std::iter::once(&outer)
        .chain(holes.iter())
        .map(|r| {
            r.iter()
                .map(|p| [(p[0] - xmin) / scale, (p[1] - ymin) / scale])
                .collect::<Vec<_>>()
        })
        .collect();
    for i in 1..all.len() {
        if !inside(all[i][0], &all[0]) {
            return Err("Un trou est hors du contour".into());
        }
        for j in 0..i {
            if (j > 0) && (inside(all[i][0], &all[j]) || inside(all[j][0], &all[i])) {
                return Err("Des trous se chevauchent ou se contiennent".into());
            }
            for k in 0..all[i].len() {
                for l in 0..all[j].len() {
                    if intersects(
                        all[i][k],
                        all[i][(k + 1) % all[i].len()],
                        all[j][l],
                        all[j][(l + 1) % all[j].len()],
                        2e-14,
                    ) {
                        return Err("Des contours se croisent ou se touchent".into());
                    }
                }
            }
        }
    }
    Ok((outer, holes))
}
fn ring_surface(api: &Gmsh, outer: &[[f64; 2]], holes: &[Vec<[f64; 2]>]) -> R<i32> {
    let mut loops = Vec::new();
    for r in std::iter::once(outer).chain(holes.iter().map(Vec::as_slice)) {
        let p = r
            .iter()
            .map(|p| api.occ_add_point(p[0], p[1], 0.))
            .collect::<R<Vec<_>>>()?;
        let edges = (0..p.len())
            .map(|i| api.occ_add_line(p[i], p[(i + 1) % p.len()]))
            .collect::<R<Vec<_>>>()?;
        loops.push(api.occ_add_loop(&edges)?);
    }
    api.occ_add_plane_surface(&loops)
}
fn plate(api: &Gmsh, g: &Value) -> R<Vec<DimTag>> {
    let w = n(g, "width", 10., true)?;
    let h = n(g, "height", 6., true)?;
    let r = n(g, "corner_radius", 0., false)?;
    let c = n(g, "chamfer", 0., false)?;
    if r < 0. || c < 0. || (r > 0. && c > 0.) || r.max(c) >= w.min(h) / 2. {
        return Err("Congé/chanfrein : choisir un seul type, inférieur à la demi-dimension".into());
    }
    let surface = if c > 0. {
        ring_surface(
            api,
            &[
                [c, 0.],
                [w - c, 0.],
                [w, c],
                [w, h - c],
                [w - c, h],
                [c, h],
                [0., h - c],
                [0., c],
            ],
            &[],
        )?
    } else {
        api.occ_add_rectangle(0., 0., 0., w, h, r)?
    };
    let mut disks = Vec::new();
    let mut circles: Vec<(f64, f64, f64)> = Vec::new();
    let eps = w.max(h) * 1e-10;
    if let Some(holes) = g.get("holes") {
        let holes = holes
            .as_array()
            .ok_or("Liste de trous circulaires attendue")?;
        if holes.len() > 1000 {
            return Err("Limite de 1000 trous".into());
        }
        for hole in holes {
            let x = number(hole.get("x"), f64::NAN, "Centre x", false)?;
            let y = number(hole.get("y"), f64::NAN, "Centre y", false)?;
            let hr = number(hole.get("radius"), f64::NAN, "Rayon du trou", true)?;
            if x.min(w - x).min(y).min(h - y) <= hr + eps {
                return Err("Un trou touche ou dépasse le contour".into());
            }
            if r > 0. {
                let qx = (x - w / 2.).abs() - (w / 2. - r);
                let qy = (y - h / 2.).abs() - (h / 2. - r);
                let sd = qx.max(0.).hypot(qy.max(0.)) + qx.max(qy).min(0.) - r;
                if sd + hr >= -eps {
                    return Err("Un trou touche un congé".into());
                }
            }
            if c > 0.
                && (x + y - c)
                    .min(w - x + y - c)
                    .min(x + h - y - c)
                    .min(w - x + h - y - c)
                    <= 2f64.sqrt() * hr + eps
            {
                return Err("Un trou touche un chanfrein".into());
            }
            if circles
                .iter()
                .any(|(a, b, cr)| (x - a).hypot(y - b) <= hr + cr + eps)
            {
                return Err("Deux trous se touchent ou se chevauchent".into());
            }
            circles.push((x, y, hr));
            disks.push((2, api.occ_add_disk(x, y, 0., hr, hr)?));
        }
    }
    if disks.is_empty() {
        Ok(vec![(2, surface)])
    } else {
        api.occ_cut(&[(2, surface)], &disks)
    }
}
fn native(api: &Gmsh, project: &Value) -> R<()> {
    let g = &project["geometry"];
    let kind = g["kind"].as_str().ok_or("Type géométrique absent")?;
    let volume = project["mode"] == "volume";
    match kind {
        "plate" => {
            let surfaces = plate(api, g)?;
            if volume {
                api.occ_extrude(&surfaces, 0., 0., n(g, "thickness", 2., true)?)?;
            }
        }
        "polygon" => {
            let (o, h) = validated_rings(g)?;
            let s = ring_surface(api, &o, &h)?;
            if volume {
                api.occ_extrude(&[(2, s)], 0., 0., n(g, "thickness", 2., true)?)?;
            }
        }
        "box" => {
            api.occ_add_box(
                0.,
                0.,
                0.,
                n(g, "width", 10., true)?,
                n(g, "height", 6., true)?,
                n(g, "depth", 4., true)?,
            )?;
        }
        "sphere" => {
            api.occ_add_sphere(0., 0., 0., n(g, "radius", 3., true)?)?;
        }
        "bracket" => {
            let w = n(g, "width", 10., true)?;
            let h = n(g, "height", 8., true)?;
            let d = n(g, "depth", 4., true)?;
            let wall = n(g, "wall", 2., true)?;
            if wall >= w.min(h) {
                return Err("Paroi d'équerre trop épaisse".into());
            }
            let a = api.occ_add_box(0., 0., 0., w, d, wall)?;
            let b = api.occ_add_box(0., 0., wall, wall, d, h - wall)?;
            let solid = api.occ_fuse(&[(3, a)], &[(3, b)])?;
            let r = n(g, "throughhole_radius", 0., false)?;
            if r < 0. {
                return Err("Rayon de perçage négatif".into());
            }
            if r > 0. {
                if 2. * r >= d.min(w - wall) {
                    return Err("Perçage d'équerre trop grand".into());
                }
                let cylinder =
                    api.occ_add_cylinder((w + wall) / 2., d / 2., -wall, 0., 0., 3. * wall, r)?;
                api.occ_cut(&solid, &[(3, cylinder)])?;
            }
        }
        _ => return Err(format!("Type géométrique inconnu : {kind}")),
    }
    api.occ_sync()
}
fn stl_topology(api: &Gmsh, tag: i32) -> R<Value> {
    let mut incidence: BTreeMap<(usize, usize), usize> = BTreeMap::new();
    let mut vertices = BTreeSet::new();
    let mut triangles = 0usize;
    for block in api.elements(2, tag)? {
        if block.num_primary != 3 {
            continue;
        }
        for row in block.nodes.chunks_exact(block.num_nodes) {
            triangles += 1;
            for &v in &row[..3] {
                vertices.insert(v);
            }
            for i in 0..3 {
                let a = row[i];
                let b = row[(i + 1) % 3];
                *incidence.entry((a.min(b), a.max(b))).or_default() += 1;
            }
        }
    }
    Ok(
        json!({"triangles":triangles,"open_edges":incidence.values().filter(|&&v|v==1).count(),"nonmanifold_edges":incidence.values().filter(|&&v|v>2).count(),"euler_characteristic":vertices.len() as i64-incidence.len() as i64+triangles as i64}),
    )
}
fn import_geometry(
    api: &Gmsh,
    source: &Path,
    project: &Value,
    factor: f64,
    warnings: &mut Vec<String>,
) -> R<bool> {
    let ext = source
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();
    if ![
        "step", "stp", "iges", "igs", "brep", "stl", "msh", "vtk", "unv",
    ]
    .contains(&ext.as_str())
        || !source.is_file()
        || source.is_symlink()
    {
        return Err("Importer STEP, IGES, BREP, STL ou un maillage surfacique triangulé MSH/VTK/UNV ; les scripts et liens sont refusés".into());
    }
    if source.metadata().map_err(|e| e.to_string())?.len() > 200 * 1024 * 1024 {
        return Err("Source CAO supérieure à 200 Mio".into());
    }
    if ["step", "stp", "iges", "igs", "brep"].contains(&ext.as_str()) {
        if ["iges", "igs"].contains(&ext.as_str()) {
            // Gmsh 4.15.2 sets xstep.cascade.unit before constructing its
            // first IGES reader; OCC has not initialized that registry yet.
            // Bootstrap through the public API in a discarded model, then
            // reread with the explicit target unit. No first-pass geometry or
            // default-unit coordinates can reach the actual mesh.
            let target = api.get_string("Geometry.OCCTargetUnit")?;
            let bootstrap = (|| -> R<()> {
                api.set_string("Geometry.OCCTargetUnit", "")?;
                api.occ_import(source)?;
                api.remove_model()?;
                api.add_model("atelier-rust")
            })();
            // Restore this process-global option even when bootstrapping
            // fails: another recipe may run in the same native process.
            let restored = api.set_string("Geometry.OCCTargetUnit", &target);
            bootstrap?;
            restored?;
        }
        api.occ_import(source)?;
        api.occ_sync()?;
        return Ok(true);
    }
    api.merge(source)?;
    if api.elements(3, -1)?.iter().any(|b| !b.tags.is_empty()) {
        return Err("Ce fichier contient déjà des cellules volumiques. Fournir sa peau triangulée pour le remaillage géométrique".into());
    }
    let surface_blocks = api.elements(2, -1)?;
    if surface_blocks.is_empty() || surface_blocks.iter().all(|b| b.tags.is_empty()) {
        return Err("Le fichier ne contient aucune surface triangulée".into());
    }
    if surface_blocks.iter().any(|b| b.num_primary != 3) {
        return Err("L'import de maillage géométrique exige une peau triangulée ; trianguler d'abord les quadrilatères".into());
    }
    // Unitless surface coordinates are transformed natively before parametrization.
    let transform = &project["geometry"]["transform"];
    let scale = n(transform, "scale", 1., true)? * factor;
    let delta = vec3(transform.get("translation"), [0.; 3], "Translation")?;
    let degrees = n(transform, "rotation_degrees", 0., false)?;
    let mut rotation = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]];
    if degrees != 0. {
        let mut axis = vec3(
            transform.get("rotation_axis"),
            [0., 0., 1.],
            "Axe de rotation",
        )?;
        let norm = axis.iter().map(|x| x * x).sum::<f64>().sqrt();
        if norm == 0. {
            return Err("Axe de rotation nul".into());
        }
        for a in &mut axis {
            *a /= norm;
        }
        let c = degrees.to_radians().cos();
        let si = degrees.to_radians().sin();
        let k = [
            [0., -axis[2], axis[1]],
            [axis[2], 0., -axis[0]],
            [-axis[1], axis[0], 0.],
        ];
        for i in 0..3 {
            for j in 0..3 {
                rotation[i][j] =
                    c * if i == j { 1. } else { 0. } + (1. - c) * axis[i] * axis[j] + si * k[i][j];
            }
        }
    }
    let mut affine = [0.; 16];
    affine[15] = 1.;
    for i in 0..3 {
        for j in 0..3 {
            affine[i * 4 + j] = rotation[i][j] * scale;
        }
        affine[i * 4 + 3] = delta[i] * factor;
    }
    api.mesh_affine_transform(&affine, &[])?;
    if flag(&project["repair"], "enabled", false)? {
        api.mesh_remove_duplicate_nodes(&[])?;
    }
    let topology = stl_topology(api, -1)?;
    if topology["nonmanifold_edges"].as_u64().unwrap_or(0) > 0 {
        return Err("STL non manifold : plusieurs faces partagent une arête".into());
    }
    if project["mode"] == "volume" && topology["open_edges"].as_u64().unwrap_or(0) > 0 {
        return Err("STL ouvert : un volume exige une enveloppe fermée. Aucun trou n'est bouché automatiquement".into());
    }
    api.mesh_classify_surfaces(40f64.to_radians(), true, false, 1e-8)?;
    let mut requires_partition = false;
    for (_, tag) in api.entities(2)? {
        if stl_topology(api, tag)?["euler_characteristic"].as_i64() != Some(1) {
            requires_partition = true;
        }
    }
    if requires_partition {
        api.mesh_classify_surfaces(40f64.to_radians(), true, true, 1e-8)?;
    }
    api.mesh_create_geometry(&[])?;
    if project["mode"] == "volume" {
        let tags = api.entities(2)?.iter().map(|(_, t)| *t).collect::<Vec<_>>();
        let s = api.geo_add_surface_loop(&tags)?;
        api.geo_add_volume(&[s])?;
        api.geo_sync()?;
    }
    warnings.push(format!("{} : surface discrète triangulée ; les courbes CAO exactes ne sont pas reconstruites. Les sommets des frontières polygonales sont conservés",ext.to_uppercase()));
    Ok(false)
}
fn entity_catalog(api: &Gmsh, is_cad: bool) -> R<Vec<Value>> {
    let mut items = Vec::new();
    for dim in 0..=3 {
        for (_, tag) in api.entities(dim)? {
            let bbox = api.bbox(dim, tag)?;
            let label = match dim {
                0 => "Sommet",
                1 => "Arête",
                2 => "Face",
                _ => "Volume",
            };
            let name = api.entity_name(dim, tag)?;
            let boundaries = if dim > 0 { api.boundary(&[(dim, tag)], false, false, false)?.into_iter().map(|(dimension, tag)| json!({"dimension":dimension,"tag":tag.abs()})).collect::<Vec<_>>() } else { Vec::new() };
            items.push(json!({"dim":dim,"dimension":dim,"tag":tag,"name":if name.is_empty(){format!("{label} {tag}")}else{name},"bbox":bbox,"bbox_source":if is_cad{"opencascade_stl_tessellation"}else{"discrete_geometry"},"bbox_approximate":is_cad,"center":[(bbox[0]+bbox[3])/2.,(bbox[1]+bbox[4])/2.,(bbox[2]+bbox[5])/2.],"boundaries":boundaries}));
        }
    }
    Ok(items)
}
fn maximal_entities(api: &Gmsh) -> R<Vec<DimTag>> {
    let mut result = Vec::new();
    let mut contained = BTreeSet::new();
    for dim in (0..=3).rev() {
        let entities = api.entities(dim)?;
        result.extend(entities.iter().copied().filter(|e| !contained.contains(e)));
        if dim > 0 {
            contained.extend(api.boundary(&entities, false, false, false)?);
        }
    }
    Ok(result)
}
fn top_snapshot(api: &Gmsh, dim: i32, tags: &[i32]) -> R<BTreeMap<i32, Value>> {
    let (ids, points) = api.nodes()?;
    let coords: HashMap<_, _> = ids.into_iter().zip(points).collect();
    let mut result = BTreeMap::new();
    for &tag in tags {
        let mut rows: Vec<Value> = Vec::new();
        for b in api.elements(dim, tag)? {
            for nodes in b.nodes.chunks_exact(b.num_nodes) {
                let mut c = nodes
                    .iter()
                    .map(|n| {
                        coords
                            .get(n)
                            .copied()
                            .ok_or_else(|| "Nœud de maillage absent".to_string())
                    })
                    .collect::<R<Vec<_>>>()?;
                // Gmsh may renumber node/element identifiers while preserving a cell.
                // The exact geometric connectivity is the invariant, not those IDs.
                let oriented = c.clone();
                c.sort_by(|a, b| {
                    a[0].total_cmp(&b[0])
                        .then(a[1].total_cmp(&b[1]))
                        .then(a[2].total_cmp(&b[2]))
                });
                rows.push(
                    json!({"type":b.element_type,"coordinates":c,"oriented_coordinates":oriented}),
                );
            }
        }
        result.insert(tag, json!(rows));
    }
    Ok(result)
}

fn same_snapshot(before: &BTreeMap<i32, Value>, after: &BTreeMap<i32, Value>) -> bool {
    if before.keys().collect::<Vec<_>>() != after.keys().collect::<Vec<_>>() {
        return false;
    }
    for (tag, old) in before {
        let Some(a) = old.as_array() else {
            return false;
        };
        let Some(b) = after[tag].as_array() else {
            return false;
        };
        if a.len() != b.len() {
            return false;
        }
        if a.is_empty() {
            continue;
        }
        let points = |row: &Value| -> Option<Vec<[f64; 3]>> {
            row["coordinates"]
                .as_array()?
                .iter()
                .map(|p| Some([p[0].as_f64()?, p[1].as_f64()?, p[2].as_f64()?]))
                .collect()
        };
        let Some(old_points) = a.iter().map(points).collect::<Option<Vec<_>>>() else {
            return false;
        };
        let Some(new_points) = b.iter().map(points).collect::<Option<Vec<_>>>() else {
            return false;
        };
        let mut low = [f64::INFINITY; 3];
        let mut high = [f64::NEG_INFINITY; 3];
        for p in old_points.iter().chain(&new_points).flatten() {
            for d in 0..3 {
                low[d] = low[d].min(p[d]);
                high[d] = high[d].max(p[d]);
            }
        }
        let scale = (0..3)
            .map(|d| high[d] - low[d])
            .fold(0., f64::max)
            .max(f64::MIN_POSITIVE);
        let tolerance = 64. * f64::EPSILON * scale;
        let bin = scale * 1e-10;
        let key = |kind: i64, coordinates: &Vec<[f64; 3]>| -> (i64, i64, i64, i64) {
            let mut c = [0.; 3];
            for p in coordinates {
                for d in 0..3 {
                    c[d] += p[d] / coordinates.len() as f64;
                }
            }
            (
                kind,
                ((c[0] - low[0]) / bin).round() as i64,
                ((c[1] - low[1]) / bin).round() as i64,
                ((c[2] - low[2]) / bin).round() as i64,
            )
        };
        let mut bins: HashMap<(i64, i64, i64, i64), Vec<usize>> = HashMap::new();
        for (i, row) in b.iter().enumerate() {
            bins.entry(key(row["type"].as_i64().unwrap_or(-1), &new_points[i]))
                .or_default()
                .push(i);
        }
        let mut used = vec![false; b.len()];
        for (i, row) in a.iter().enumerate() {
            let (kind, x, y, z) = key(row["type"].as_i64().unwrap_or(-1), &old_points[i]);
            let mut found = None;
            'search: for dx in -1..=1 {
                for dy in -1..=1 {
                    for dz in -1..=1 {
                        if let Some(indices) = bins.get(&(kind, x + dx, y + dy, z + dz)) {
                            for &j in indices {
                                if !used[j] && old_points[i].len() == new_points[j].len() && {
                                    let orientation_ok =
                                        if [2, 3, 9, 10, 16].contains(&(kind as i32)) {
                                            let normal = |r: &Value| -> [f64; 3] {
                                                let p = &r["oriented_coordinates"];
                                                let a = [
                                                    p[0][0].as_f64().unwrap_or(0.),
                                                    p[0][1].as_f64().unwrap_or(0.),
                                                    p[0][2].as_f64().unwrap_or(0.),
                                                ];
                                                let b = [
                                                    p[1][0].as_f64().unwrap_or(0.),
                                                    p[1][1].as_f64().unwrap_or(0.),
                                                    p[1][2].as_f64().unwrap_or(0.),
                                                ];
                                                let c = [
                                                    p[2][0].as_f64().unwrap_or(0.),
                                                    p[2][1].as_f64().unwrap_or(0.),
                                                    p[2][2].as_f64().unwrap_or(0.),
                                                ];
                                                let u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
                                                let v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
                                                [
                                                    u[1] * v[2] - u[2] * v[1],
                                                    u[2] * v[0] - u[0] * v[2],
                                                    u[0] * v[1] - u[1] * v[0],
                                                ]
                                            };
                                            let u = normal(row);
                                            let v = normal(&b[j]);
                                            u.iter().zip(v).map(|(u, v)| u * v).sum::<f64>() > 0.
                                        } else {
                                            true
                                        };
                                    let mut matched = vec![false; new_points[j].len()];
                                    orientation_ok
                                        && old_points[i].iter().all(|p| {
                                            if let Some(k) = new_points[j]
                                                .iter()
                                                .enumerate()
                                                .position(|(k, q)| {
                                                    !matched[k]
                                                        && (0..3).all(|d| {
                                                            (p[d] - q[d]).abs() <= tolerance
                                                        })
                                                })
                                            {
                                                matched[k] = true;
                                                true
                                            } else {
                                                false
                                            }
                                        })
                                } {
                                    found = Some(j);
                                    break 'search;
                                }
                            }
                        }
                    }
                }
            }
            match found {
                Some(j) => used[j] = true,
                None => return false,
            }
        }
    }
    true
}

fn mesh_data(api: &Gmsh, dimension: i32, max_elements: usize, project: &Value, factor: f64, build_preview: bool) -> R<(Value, Value, usize, Value)> {
    let (node_tags, points) = api.nodes()?;
    let map: HashMap<_, _> = node_tags
        .iter()
        .copied()
        .enumerate()
        .map(|(i, t)| (t, i))
        .collect();
    if points.is_empty() {
        return Err("Le moteur n'a produit aucun nœud".into());
    }
    if !build_preview && points.iter().flatten().any(|coordinate| !coordinate.is_finite()) {
        return Err("Coordonnées de maillage non finies".into());
    }
    let mut cells = Vec::new();
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut top_tags = Vec::new();
    // Keep the native blocks until quality assessment has consumed them. The
    // assessor borrows their flat connectivity instead of fetching and copying
    // the same element data a second time through the C ABI.
    let mut top_blocks = Vec::new();
    for dim in 1..=dimension {
        for (_, entity) in api.entities(dim)? {
            for b in api.elements(dim, entity)? {
                if b.num_nodes == 0 || b.num_primary > b.num_nodes
                    || b.tags.len().checked_mul(b.num_nodes) != Some(b.nodes.len()) {
                    return Err("Connectivité de maillage incohérente".into());
                }
                *counts.entry(b.name.clone()).or_default() += b.tags.len();
                if b.dim == dimension {
                    top_tags.extend(&b.tags);
                    if top_tags.len() > max_elements {
                        return Err(format!("{} éléments dépassent la limite {max_elements}", top_tags.len()));
                    }
                }
                let kind = match (b.dim, b.num_primary) {
                    (1, 2) => "line",
                    (2, 3) => "triangle",
                    (2, 4) => "quad",
                    (3, 4) => "tetra",
                    (3, 8) => "hexa",
                    (3, 6) => "wedge",
                    (3, 5) => "pyramid",
                    _ => {
                        if b.dim == dimension { top_blocks.push((entity, b)); }
                        continue;
                    },
                };
                if build_preview {
                    let mut rows = Vec::with_capacity(b.tags.len());
                    for row in b.nodes.chunks_exact(b.num_nodes) {
                        rows.push(
                            row[..b.num_primary]
                                .iter()
                                .map(|id| {
                                    map.get(id)
                                        .copied()
                                        .ok_or_else(|| "Nœud absent du catalogue".to_string())
                                })
                                .collect::<R<Vec<_>>>()?,
                        );
                    }
                    cells.push(json!({"type":kind,"data":rows,"order":b.order,"entity":entity,"entity_tag":entity,"dimension":dim,"element_tags":b.tags}));
                } else if b.dim != dimension {
                    // Top-dimensional connectivity is checked exhaustively by
                    // quality::assess below. Preserve the lower-dimensional
                    // catalogue validation even when its display JSON is omitted.
                    for row in b.nodes.chunks_exact(b.num_nodes) {
                        for id in &row[..b.num_primary] {
                            if !map.contains_key(id) { return Err("Nœud absent du catalogue".into()); }
                        }
                    }
                }
                if b.dim == dimension { top_blocks.push((entity, b)); }
            }
        }
    }
    if top_tags.is_empty() {
        return Err("Aucun élément de la dimension demandée".into());
    }
    if top_tags.len() > max_elements {
        return Err(format!(
            "{} éléments dépassent la limite {max_elements}",
            top_tags.len()
        ));
    }
    let q = api.element_qualities(&top_tags, "minSICN")?;
    let jac = api.element_qualities(&top_tags, "minDetJac")?;
    if q.is_empty() || q.iter().chain(&jac).any(|x| !x.is_finite() || *x <= 0.) {
        return Err("Éléments inversés ou dégénérés détectés ; résultat non exporté".into());
    }
    let quality = crate::quality::assess(api, project, dimension, factor, &map, &points, &top_tags, &q, &jac, &top_blocks)?;
    if !build_preview {
        // Internal metadata only: this value is never persisted as a display
        // preview. The complete normal-mode preview schema stays unchanged.
        return Ok((quality, json!({"point_count":points.len()}), top_tags.len(), json!(counts)));
    }
    let mut triangles = Vec::new();
    let mut face_tags = Vec::new();
    let mut all_faces = 0usize;
    for cell in &cells {
        let kind = cell["type"].as_str().unwrap_or("");
        if kind != "triangle" && kind != "quad" {
            continue;
        }
        for row in cell["data"].as_array().unwrap() {
            let r = row.as_array().unwrap();
            all_faces += if kind == "quad" { 2 } else { 1 };
            if triangles.len() < MAX_PREVIEW_FACES {
                triangles.push(json!([r[0], r[1], r[2]]));
                face_tags.push(cell["entity"].clone());
            }
            if kind == "quad" && triangles.len() < MAX_PREVIEW_FACES {
                triangles.push(json!([r[0], r[2], r[3]]));
                face_tags.push(cell["entity"].clone());
            }
        }
    }
    let mut lower = [f64::INFINITY; 3];
    let mut upper = [f64::NEG_INFINITY; 3];
    for p in &points {
        for d in 0..3 {
            lower[d] = lower[d].min(p[d]);
            upper[d] = upper[d].max(p[d]);
        }
    }
    let preview = json!({"schema":"atelier.preview.v1","points":points,"node_tags":node_tags,"cells":cells,"surface_triangles":triangles,"face_tags":face_tags,"bounds":[lower,upper],"truncated":all_faces>MAX_PREVIEW_FACES,"units":"m"});
    Ok((quality, preview, top_tags.len(), json!(counts)))
}

fn constraint_fields(api: &Gmsh, p: &Value, factor: f64, global_size: f64) -> R<()> {
    let mut fields = Vec::new();
    if let Some(items) = p.get("local_sizes") {
        for item in items
            .as_array()
            .ok_or("Liste des tailles locales attendue")?
        {
            let dim = item["dim"]
                .as_i64()
                .or_else(|| item["dimension"].as_i64())
                .ok_or("Dimension locale absente")? as i32;
            let tag = item["tag"].as_i64().ok_or("Entité locale absente")? as i32;
            if !api.entities(dim)?.contains(&(dim, tag)) {
                return Err("Entité locale inexistante".into());
            }
            let size = n(item, "size", global_size / factor, true)? * factor;
            let mut points = if dim == 0 {
                vec![(0, tag)]
            } else {
                api.boundary(&[(dim, tag)], false, false, true)?
            };
            points.retain(|(d, _)| *d == 0);
            if !points.is_empty() {
                api.mesh_set_size(&points, size)?;
            }
            let field = api.mesh_field_add("MathEval")?;
            api.mesh_field_set_string(field, "F", &format!("{size:.16e}"))?;
            let restrict = api.mesh_field_add("Restrict")?;
            api.mesh_field_set_number(restrict, "InField", field as f64)?;
            api.mesh_field_set_numbers(
                restrict,
                match dim {
                    0 => "PointsList",
                    1 => "CurvesList",
                    2 => "SurfacesList",
                    _ => "VolumesList",
                },
                &[tag as f64],
            )?;
            fields.push(restrict as f64);
        }
    }
    fields.extend(refinement_fields(api,p,factor,global_size)?);
    let global = api.mesh_field_add("MathEval")?;
    api.mesh_field_set_string(global, "F", &format!("{global_size:.16e}"))?;
    fields.push(global as f64);
    let minimum = api.mesh_field_add("Min")?;
    api.mesh_field_set_numbers(minimum, "FieldsList", &fields)?;
    api.mesh_field_background(minimum)?;
    if let Some(items) = p.get("correspondences") {
        for item in items
            .as_array()
            .ok_or("Liste des correspondances attendue")?
        {
            let dim = item["dimension"]
                .as_i64()
                .ok_or("Dimension de correspondance absente")? as i32;
            let master = item["master"].as_i64().ok_or("Maître absent")? as i32;
            let slave = item["slave"].as_i64().ok_or("Esclave absent")? as i32;
            let delta = vec3(item.get("translation"), [0.; 3], "Translation périodique")?;
            let mut a = [
                1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.,
            ];
            for i in 0..3 {
                a[i * 4 + 3] = delta[i] * factor;
            }
            api.mesh_set_periodic(dim, &[slave], &[master], &a)?;
        }
    }
    Ok(())
}
/// Gmsh's direct high-order optimizer can log an error inside an OpenMP
/// region. Throwing there (AbortOnError=2) terminates the native process before
/// the C ABI can return ierr. Keep the same optimizer, collect its diagnostics
/// in non-throwing mode, restore normal error handling, and refuse any error.
fn optimize_high_order(api:&Gmsh, warnings:&mut Vec<String>) -> R<()> {
    let previous=api.get_number("General.AbortOnError")?;
    api.logger_start()?;
    let configured=api.set_number("General.AbortOnError",1.);
    let optimized=if configured.is_ok(){api.mesh_optimize("HighOrder")}else{Ok(())};
    let messages=api.logger_messages();
    let stopped=api.logger_stop();
    let restored=api.set_number("General.AbortOnError",previous);
    // Run every cleanup before propagating errors; no global Gmsh error mode
    // or logger is left changed by an ordinary C-ABI failure.
    let mut errors=Vec::new();
    for (stage,result) in [("configuration",configured),("optimisation",optimized),("journal",stopped),("restauration",restored)] {
        if let Err(error)=result {errors.push(format!("{stage} : {error}"));}
    }
    match messages {
        Ok(messages)=>{
            for message in messages {
                let level=message.trim_start();
                if level.starts_with("Error") {errors.push(message);}
                else if level.starts_with("Warning") {warnings.push(format!("Optimisation quadratique Gmsh : {message}"));}
            }
        },
        Err(error)=>errors.push(format!("lecture du journal : {error}")),
    }
    if !errors.is_empty() {
        return Err(format!("Optimisation quadratique Gmsh refusée ; aucun maillage public n'est publié : {}",errors.join(" ; ")));
    }
    Ok(())
}

fn replay_generate(api: &Gmsh, p: &Value, factor: f64, dim: i32, local: bool, warnings:&mut Vec<String>) -> R<()> {
    let m = &p["mesh"];
    let size = n(m, "size", 0.5, true)? * factor;
    let min = n(m, "min_size", 0.1, true)? * factor;
    let algo = m["algorithm"].as_str().unwrap_or("automatic");
    if dim == 3 && algo == "frontal" && has_spatial_fields(p) {
        return Err("Le frontal 3D ne prend pas en charge les champs de taille locaux ; utiliser Delaunay ou Automatique".into());
    }
    let order = integer(m, "order", 1, 2)?;
    api.set_number("Mesh.MeshSizeMin", min)?;
    api.set_number("Mesh.MeshSizeMax", size)?;
    mesh_tuning(api,m)?;
    api.set_number("Mesh.MeshSizeExtendFromBoundary", 1.)?;
    api.set_number(
        "Mesh.Algorithm",
        match algo {
            "delaunay" => 5.,
            "frontal" => 6.,
            _ => 2.,
        },
    )?;
    api.set_number("Mesh.Algorithm3D", if algo == "frontal" { 4. } else { 1. })?;
    api.set_number("Mesh.ElementOrder", order as f64)?;
    api.set_number("Mesh.SecondOrderLinear", 0.)?;
    api.set_number(
        "Mesh.RecombineAll",
        if flag(m, "recombine", false)? { 1. } else { 0. },
    )?;
    let selection = &p["selection"];
    let tags = selection["tags"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(Value::as_i64)
                .map(|t| t as i32)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if local {
        if tags.is_empty() {
            return Err("Historique de remaillage sans sélection".into());
        }
        let selected = tags.iter().map(|t| (dim, *t)).collect::<Vec<_>>();
        api.mesh_clear(&selected)?;
        api.set_number("Mesh.MeshOnlyEmpty", 1.)?;
    } else {
        api.set_number("Mesh.MeshOnlyEmpty", 0.)?;
    }
    if !local && selection["scope"] == "entities" {
        api.set_visibility(&api.entities(-1)?, false, true)?;
        api.set_visibility(
            &tags.iter().map(|t| (dim, *t)).collect::<Vec<_>>(),
            true,
            true,
        )?;
        api.set_number("Mesh.MeshOnlyVisible", 1.)?;
    }
    constraint_fields(api, p, factor, size)?;
    api.mesh_generate(dim)?;
    if order == 2 && !local {
        optimize_high_order(api,warnings)?;
    }
    Ok(())
}
/// Execute a complete isolated native operation and persist its reviewable result.
pub fn run(request: &Value) -> R<Value> {
    let started = Instant::now();
    let project = request.get("project").ok_or("Projet absent")?;
    let operation = request["operation"].as_str().unwrap_or("mesh");
    let report_only = flag(request, "report_only", false)?;
    if report_only && !["mesh", "inspect"].contains(&operation) {
        return Err("Le mode sans artefacts est réservé aux évaluations de maillage et de géométrie".into());
    }
    if !["mesh", "inspect", "remesh"].contains(&operation) {
        return Err("Opération inconnue".into());
    }
    let mode = project["mode"].as_str().unwrap_or("planar");
    let requested_dim = match mode {
        "planar" | "surface" => 2,
        "volume" => 3,
        _ => return Err("Mode inconnu".into()),
    };
    let dimension = if operation == "inspect" {
        2
    } else {
        requested_dim
    };
    let geometry = project
        .get("geometry")
        .filter(|v| v.is_object())
        .ok_or("Géométrie absente")?;
    let kind = geometry["kind"].as_str().ok_or("Type géométrique absent")?;
    if mode == "planar" && ["box", "sphere", "bracket"].contains(&kind) {
        return Err("Cette géométrie exige le mode coque ou volume".into());
    }
    let mesh = &project["mesh"];
    let repair = &project["repair"];
    let size = n(mesh, "size", 0.5, true)?;
    let min_size = n(mesh, "min_size", size.min(0.1), true)?;
    if min_size > size {
        return Err("La taille minimale dépasse la taille cible".into());
    }
    let order = integer(mesh, "order", 1, 2)? as i32;
    let threads = integer(mesh, "threads", 1, 64)?;
    let max_elements = integer(mesh, "max_elements", 200000, 2000000)? as usize;
    let algorithm = mesh["algorithm"].as_str().unwrap_or("automatic");
    if !["automatic", "delaunay", "frontal"].contains(&algorithm) {
        return Err("Algorithme inconnu".into());
    }
    if operation != "inspect" && dimension == 3 && algorithm == "frontal" && has_spatial_fields(project) {
        return Err("Le frontal 3D ne prend pas en charge les champs de taille locaux ; utiliser Delaunay ou Automatique".into());
    }
    let recombine = flag(mesh, "recombine", false)?;
    if dimension == 3 && recombine {
        return Err("La recombinaison quadrangulaire est réservée aux surfaces".into());
    }
    let units = project.get("units").unwrap_or(&Value::Null);
    let input_unit = units["input"].as_str().unwrap_or("mm");
    let factor = unit_factor(input_unit)?;
    let repair_enabled = flag(repair, "enabled", false)?;
    let tolerance = n(repair, "tolerance", 1e-7, true)? * factor;
    let output = PathBuf::from(
        request["output_dir"]
            .as_str()
            .ok_or("Dossier de résultats absent")?,
    );
    fs::create_dir_all(&output).map_err(|e| e.to_string())?;
    let source = request
        .get("source")
        .and_then(Value::as_str)
        .map(PathBuf::from);
    for input in [
        source.as_ref(),
        request
            .get("previous_mesh")
            .and_then(Value::as_str)
            .map(PathBuf::from)
            .as_ref(),
    ]
    .into_iter()
    .flatten()
    {
        if [
            "mesh.msh",
            "mesh.state.msh",
            "model.brep",
            "report.json",
            "preview.json",
            "replay.json",
            "project.normalized.json",
            "quality_rejection.json",
            "remesh_preservation.json",
        ]
        .iter()
        .any(|name| {
            fs::canonicalize(input).ok() == fs::canonicalize(output.join(name)).ok()
                && input.exists()
        }) {
            return Err("Les sources doivent être distinctes du dossier de résultats".into());
        }
    }
    for name in [
        "mesh.msh",
        "mesh.state.msh",
        "model.brep",
        "report.json",
        "preview.json",
        "replay.json",
        "project.normalized.json",
        "quality_rejection.json",
        "remesh_preservation.json",
    ] {
        let file = output.join(name);
        if file.exists() {
            fs::remove_file(file).map_err(|e| format!("Ancien résultat non remplaçable : {e}"))?;
        }
    }
    let library = request["gmsh_library"]
        .as_str()
        .map(str::to_owned)
        .or_else(|| std::env::var("GMSH_LIBRARY").ok())
        .ok_or("Chemin de bibliothèque Gmsh absent")?;
    let mut recipe = json!({"base_project":project,"steps":[]});
    let mut old_reference = None;
    let mut replay_seconds = 0.0;
    if operation == "remesh" {
        let old_path = PathBuf::from(
            request["previous_mesh"]
                .as_str()
                .ok_or("Maillage précédent absent")?,
        );
        if !old_path.is_file() {
            return Err("Maillage précédent absent".into());
        }
        let recipe_path = old_path
            .parent()
            .unwrap_or(Path::new("."))
            .join("replay.json");
        recipe = if recipe_path.is_file() {
            serde_json::from_slice(&fs::read(recipe_path).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?
        } else {
            json!({"base_project":request.get("previous_project").ok_or("Le projet précédent est nécessaire au remaillage")?,"steps":[]})
        };
        let steps = recipe["steps"]
            .as_array()
            .ok_or("Historique de remaillage invalide")?;
        if steps.len() >= 32 {
            return Err(
                "Limite de 32 opérations locales atteinte ; créer un nouvel état de référence"
                    .into(),
            );
        }
        if recipe["base_project"]["geometry"] != project["geometry"] {
            return Err(
                "La géométrie a changé ; régénérer le maillage avant un remaillage local".into(),
            );
        }
        if unit_factor(
            recipe["base_project"]["units"]["input"]
                .as_str()
                .unwrap_or("mm"),
        )? != factor
        {
            return Err(
                "Les unités ont changé ; régénérer le maillage avant un remaillage local".into(),
            );
        }
        {
            let old_api = Gmsh::new(&library)?;
            old_api.set_number("General.Terminal", 0.)?;
            old_api.open(&old_path)?;
            let wanted = project["selection"]["tags"]
                .as_array()
                .ok_or("Sélection locale absente")?
                .iter()
                .filter_map(Value::as_i64)
                .map(|x| x as i32)
                .collect::<BTreeSet<_>>();
            let unselected = old_api
                .entities(dimension)?
                .iter()
                .filter(|(_, t)| !wanted.contains(t))
                .map(|(_, t)| *t)
                .collect::<Vec<_>>();
            old_reference = Some(top_snapshot(&old_api, dimension, &unselected)?);
        }
    }
    let api = Gmsh::new(&library)?;
    api.set_number("General.Terminal", 0.)?;
    api.set_number("General.NumThreads", threads as f64)?;
    for d in 1..=3 {
        api.set_number(&format!("Mesh.MaxNumThreads{d}D"), threads as f64)?;
    }
    api.set_number("Geometry.Tolerance", tolerance)?;
    api.add_model("atelier-rust")?;
    let mut warnings = Vec::new();
    let is_cad = if kind == "import" {
        let source = source.as_ref().ok_or("Sélectionner la source CAO")?;
        // Ask OCC for the selected coordinate unit, then convert exactly once to SI.
        let target = match input_unit {
            "m" => "M",
            "cm" => "CM",
            "in" | "inch" => "INCH",
            _ => "MM",
        };
        api.set_string("Geometry.OCCTargetUnit", target)?;
        import_geometry(&api, source, project, factor, &mut warnings)?
    } else {
        native(&api, project)?;
        true
    };
    if is_cad {
        let all = maximal_entities(&api)?;
        if factor != 1. {
            api.occ_dilate(&all, 0., 0., 0., factor, factor, factor)?;
            api.occ_sync()?;
        }
        if let Some(transform) = geometry.get("transform") {
            let scale = n(transform, "scale", 1., true)?;
            if scale != 1. {
                api.occ_dilate(&maximal_entities(&api)?, 0., 0., 0., scale, scale, scale)?;
            }
            let degrees = n(transform, "rotation_degrees", 0., false)?;
            if degrees != 0. {
                let axis = vec3(
                    transform.get("rotation_axis"),
                    [0., 0., 1.],
                    "Axe de rotation",
                )?;
                if axis.iter().map(|x| x * x).sum::<f64>() == 0. {
                    return Err("Axe de rotation nul".into());
                }
                api.occ_rotate(
                    &maximal_entities(&api)?,
                    0.,
                    0.,
                    0.,
                    axis[0],
                    axis[1],
                    axis[2],
                    degrees.to_radians(),
                )?;
            }
            let delta = vec3(transform.get("translation"), [0.; 3], "Translation")?;
            if delta.iter().any(|x| *x != 0.) {
                api.occ_translate(
                    &maximal_entities(&api)?,
                    delta[0] * factor,
                    delta[1] * factor,
                    delta[2] * factor,
                )?;
            }
            api.occ_sync()?;
        }
    }
    let before = counts(&api)?;
    let mut repair_report = json!({"requested":repair_enabled,"applied":false,"tolerance":tolerance,"holes_capped":false});
    if repair_enabled && is_cad {
        api.occ_heal(
            &[],
            tolerance,
            true,
            flag(repair, "small_edges", true)?,
            flag(repair, "small_faces", false)?,
            flag(repair, "sew", true)?,
            flag(repair, "make_solids", false)?,
        )?;
        api.occ_sync()?;
        repair_report["applied"] = json!(true);
        warnings.push("La réparation peut modifier de petits détails : comparer la géométrie avant utilisation mécanique".into());
    } else if repair_enabled {
        repair_report["applied"] = json!(true);
        repair_report["method"] = json!("duplicate_nodes_only");
    }
    repair_report["before_entities"] = before;
    repair_report["after_entities"] = counts(&api)?;
    let all_top = api.entities(dimension)?;
    if all_top.is_empty() {
        return Err(if requested_dim==3{"Aucun volume fermé détecté : coudre une enveloppe fermée avec make_solids, ou choisir le mode coque"}else{"Aucune surface détectée"}.into());
    }
    let bbox = api.bbox(-1, -1)?;
    if bbox.iter().any(|x| !x.is_finite()) {
        return Err("Étendue géométrique invalide".into());
    }
    let extent = (0..3).map(|i| bbox[i + 3] - bbox[i]).fold(0., f64::max);
    if mode == "planar" && bbox[5] - bbox[2] > (extent * 1e-6).max(2.1e-7) {
        return Err(
            "Le mode 2D exige une pièce plane XY ; choisir coque pour une surface spatiale".into(),
        );
    }
    let catalog = entity_catalog(&api, is_cad)?;
    if is_cad {
        warnings.push("Les boîtes CAO proviennent de la tessellation OpenCASCADE (OCCBoundsUseStl) ; leurs extrema sont approximatifs et peuvent sous-estimer une surface courbe".into());
    }
    let selection = &project["selection"];
    let scope = selection["scope"].as_str().unwrap_or("all");
    if scope != "all" && scope != "entities" {
        return Err("Portée de sélection inconnue".into());
    }
    let mut selected = all_top.clone();
    if scope == "entities" && operation != "inspect" {
        let selected_dim = selection["dimension"].as_i64().unwrap_or(dimension as i64) as i32;
        if selected_dim != dimension {
            return Err(
                "La dimension de la sélection doit correspondre au domaine à mailler".into(),
            );
        }
        let tags = selection["tags"]
            .as_array()
            .ok_or("Sélectionner au moins une entité")?;
        let tag_set: BTreeSet<i32> = tags
            .iter()
            .map(|v| {
                v.as_i64()
                    .filter(|n| *n > 0 && *n <= i32::MAX as i64)
                    .map(|n| n as i32)
                    .ok_or("Identifiant d'entité invalide".to_string())
            })
            .collect::<R<_>>()?;
        if tag_set.is_empty() {
            return Err("Sélection vide".into());
        }
        selected = all_top
            .iter()
            .copied()
            .filter(|(_, t)| tag_set.contains(t))
            .collect();
        if selected.len() != tag_set.len() {
            return Err("La sélection contient une entité inexistante".into());
        }
    }
    let mut unselected = Vec::new();
    let mut snapshot = None;
    if operation == "remesh" {
        if scope != "entities" {
            return Err("Un remaillage local exige une sélection explicite d'entités".into());
        }
        let replay_started = Instant::now();
        replay_generate(&api, &recipe["base_project"], factor, dimension, false, &mut warnings)?;
        for step in recipe["steps"].as_array().ok_or("Historique invalide")? {
            replay_generate(&api, step, factor, dimension, true, &mut warnings)?;
        }
        replay_seconds = replay_started.elapsed().as_secs_f64();
        unselected = all_top
            .iter()
            .copied()
            .filter(|x| !selected.contains(x))
            .map(|(_, t)| t)
            .collect();
        let reconstructed = top_snapshot(&api, dimension, &unselected)?;
        if !same_snapshot(
            old_reference
                .as_ref()
                .ok_or("Référence précédente absente")?,
            &reconstructed,
        ) {
            write_json(
                &output.join("remesh_preservation.json"),
                &json!({"reference":old_reference,"reconstructed":reconstructed}),
            )?;
            return Err("L'état précédent ne peut pas être rejoué à l'identique ; remaillage refusé pour conserver la zone extérieure".into());
        }
        snapshot = Some(reconstructed);
        api.mesh_clear(&selected)?;
        api.set_number("Mesh.MeshOnlyEmpty", 1.)?;
        warnings.push("Remaillage par entité : le maillage des frontières existantes est conservé ; les changements de taille concernent l'intérieur".into());
    }
    if scope == "entities" && operation != "inspect" && operation != "remesh" {
        api.set_visibility(&api.entities(-1)?, false, true)?;
        api.set_visibility(&selected, true, true)?;
        api.set_number("Mesh.MeshOnlyVisible", 1.)?;
    }
    api.remove_physical_groups(&[])?;
    let meshed_domains = if operation == "remesh" {
        all_top.clone()
    } else {
        selected.clone()
    };
    let domain_tags: Vec<_> = meshed_domains.iter().map(|(_, t)| *t).collect();
    let domain_group = api.physical_group(dimension, &domain_tags, "DOMAIN")?;
    let mut groups = vec![
        json!({"dimension":dimension,"tag":domain_group,"name":"DOMAIN","entities":domain_tags}),
    ];
    for (_, tag) in &meshed_domains {
        let name = format!("PART_{tag:03}");
        let id = api.physical_group(dimension, &[*tag], &name)?;
        groups.push(json!({"dimension":dimension,"tag":id,"name":name,"entities":[tag]}));
    }
    let boundary = api.boundary(&meshed_domains, true, false, false)?;
    let boundary_tags: Vec<i32> = boundary
        .iter()
        .map(|(_, t)| *t)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    if !boundary_tags.is_empty() {
        let id = api.physical_group(dimension - 1, &boundary_tags, "BOUNDARY")?;
        groups.push(
            json!({"dimension":dimension-1,"tag":id,"name":"BOUNDARY","entities":boundary_tags}),
        );
    }
    for &tag in &boundary_tags {
        let name = if dimension == 3 {
            format!("FACE_{tag:03}")
        } else {
            format!("EDGE_{tag:03}")
        };
        let id = api.physical_group(dimension - 1, &[tag], &name)?;
        groups.push(json!({"dimension":dimension-1,"tag":id,"name":name,"entities":[tag]}));
    }
    // Persist the lower-dimensional mesh as well: local remeshing must reload
    // the original edge discretization instead of silently rebuilding interfaces.
    for dim in 0..dimension {
        let entities = api.entities(dim)?;
        let tags = entities.iter().map(|(_, t)| *t).collect::<Vec<_>>();
        if !tags.is_empty() {
            let name = format!("GEOMETRY_{dim}D");
            let id = api.physical_group(dim, &tags, &name)?;
            groups.push(json!({"dimension":dim,"tag":id,"name":name,"entities":tags}));
        }
    }
    let scaled_size = if operation == "inspect" {
        extent / 12.
    } else {
        size * factor
    };
    if scaled_size / extent < 1e-6 {
        return Err("Taille cible trop petite par rapport à l'étendue du modèle".into());
    }
    let mass = if is_cad {
        Some(
            selected
                .iter()
                .map(|(d, t)| api.occ_mass(*d, *t).map(f64::abs))
                .collect::<R<Vec<_>>>()?
                .iter()
                .sum::<f64>(),
        )
    } else {
        None
    };
    let estimate =
        mass.map(|m| m / scaled_size.powi(dimension) * if dimension == 3 { 2. } else { 1. });
    if estimate.is_some_and(|e| !e.is_finite() || e > 8. * max_elements as f64) {
        return Err("L'estimation préalable dépasse largement la limite d'éléments".into());
    }
    api.set_number(
        "Mesh.MeshSizeMin",
        if operation == "inspect" {
            scaled_size / 3.
        } else {
            min_size * factor
        },
    )?;
    api.set_number("Mesh.MeshSizeMax", scaled_size)?;
    mesh_tuning(&api,mesh)?;
    api.set_number("Mesh.MeshSizeExtendFromBoundary", 1.)?;
    let algo2 = match algorithm {
        "delaunay" => 5,
        "frontal" => 6,
        _ => 2,
    };
    let algo3 = if algorithm == "frontal" { 4 } else { 1 };
    api.set_number("Mesh.Algorithm", algo2 as f64)?;
    api.set_number("Mesh.Algorithm3D", algo3 as f64)?;
    api.set_number(
        "Mesh.ElementOrder",
        if operation == "inspect" {
            1.
        } else {
            order as f64
        },
    )?;
    api.set_number("Mesh.SecondOrderLinear", 0.)?;
    api.set_number(
        "Mesh.RecombineAll",
        if recombine && operation != "inspect" {
            1.
        } else {
            0.
        },
    )?;
    api.set_number("Mesh.MshFileVersion", 4.1)?;
    let mut size_fields = Vec::new();
    if operation != "inspect" {
        if let Some(local) = project.get("local_sizes") {
            let local = local.as_array().ok_or("local_sizes doit être une liste")?;
            if local.len() > 1000 {
                return Err("Trop de tailles locales".into());
            }
            for item in local {
                let dim = item["dim"]
                    .as_i64()
                    .or_else(|| item["dimension"].as_i64())
                    .ok_or("Dimension de taille locale absente")? as i32;
                let tag = item["tag"]
                    .as_i64()
                    .ok_or("Entité de taille locale absente")? as i32;
                if !(0..=3).contains(&dim) || !api.entities(dim)?.contains(&(dim, tag)) {
                    return Err("Entité de taille locale inexistante".into());
                }
                let local_size = n(item, "size", size, true)? * factor;
                if local_size < min_size * factor {
                    return Err("Une taille locale est inférieure à la taille minimale globale ; abaisser d'abord cette limite".into());
                }
                let mut points = if dim == 0 {
                    vec![(0, tag)]
                } else {
                    api.boundary(&[(dim, tag)], false, false, true)?
                };
                points.retain(|(d, _)| *d == 0);
                if !points.is_empty() {
                    api.mesh_set_size(&points, local_size)?;
                }
                let field = api.mesh_field_add("MathEval")?;
                api.mesh_field_set_string(field, "F", &format!("{local_size:.16e}"))?;
                let restrict = api.mesh_field_add("Restrict")?;
                api.mesh_field_set_number(restrict, "InField", field as f64)?;
                let list = match dim {
                    0 => "PointsList",
                    1 => "CurvesList",
                    2 => "SurfacesList",
                    _ => "VolumesList",
                };
                api.mesh_field_set_numbers(restrict, list, &[tag as f64])?;
                size_fields.push(restrict as f64);
            }
        }
    }
    if operation != "inspect" {
        size_fields.extend(refinement_fields(&api,project,factor,scaled_size)?);
    }
    {
        let global = api.mesh_field_add("MathEval")?;
        api.mesh_field_set_string(global, "F", &format!("{scaled_size:.16e}"))?;
        size_fields.push(global as f64);
        let field = api.mesh_field_add("Min")?;
        api.mesh_field_set_numbers(field, "FieldsList", &size_fields)?;
        api.mesh_field_background(field)?;
    }
    let mut periodic_requests = Vec::new();
    if operation != "inspect" {
        if let Some(items) = project.get("correspondences") {
            for item in items
                .as_array()
                .ok_or("Liste de correspondances attendue")?
            {
                let dim = item["dimension"]
                    .as_i64()
                    .ok_or("Dimension de correspondance absente")? as i32;
                let master = item["master"].as_i64().ok_or("Entité maître absente")? as i32;
                let slave = item["slave"].as_i64().ok_or("Entité esclave absente")? as i32;
                if !(1..=2).contains(&dim)
                    || master == slave
                    || !api.entities(dim)?.contains(&(dim, master))
                    || !api.entities(dim)?.contains(&(dim, slave))
                {
                    return Err("Correspondance maître/esclave invalide".into());
                }
                let delta = vec3(
                    item.get("translation"),
                    [0.; 3],
                    "Translation de correspondance",
                )?;
                let mut affine = [
                    1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.,
                ];
                for i in 0..3 {
                    affine[i * 4 + 3] = delta[i] * factor;
                }
                api.mesh_set_periodic(dim, &[slave], &[master], &affine)?;
                periodic_requests.push((dim, slave, master));
            }
        }
    }
    let geometry_seconds = started.elapsed().as_secs_f64();
    if is_cad && !report_only {
        api.write(&output.join("model.brep"))?;
    }
    let mesh_started = Instant::now();
    api.mesh_generate(dimension)?;
    if order == 2 && operation != "inspect" && operation != "remesh" {
        optimize_high_order(&api,&mut warnings)?;
    }
    let generation_seconds = mesh_started.elapsed().as_secs_f64();
    let validation_started = Instant::now();
    if let Some(before) = snapshot {
        let after = top_snapshot(&api, dimension, &unselected)?;
        if !same_snapshot(&before, &after) {
            write_json(
                &output.join("remesh_preservation.json"),
                &json!({"before":before,"after":after}),
            )?;
            return Err("Le remaillage modifierait la zone conservée : résultat refusé pour préserver la conformité".into());
        }
    }
    let display_project = json!({});
    let quality_project = if operation == "inspect" { &display_project } else { project };
    let (mut quality, preview, elements, cell_counts) = mesh_data(&api, dimension, max_elements, quality_project, factor, !report_only)?;
    quality["diagnostic_only"] = json!(operation=="inspect");
    let mut correspondences = Vec::new();
    for (dim, slave, master) in periodic_requests {
        let (actual_master, slave_nodes, master_nodes, affine) =
            api.mesh_get_periodic_nodes(dim, slave)?;
        // Serialize the exact native node correspondence, not a guessed geometric match.
        if actual_master != master
            || slave_nodes.is_empty()
            || slave_nodes.len() != master_nodes.len()
        {
            return Err("La correspondance n'a produit aucun appariement nodal valide".into());
        }
        let node_pairs = slave_nodes
            .iter()
            .zip(&master_nodes)
            .map(|(a, b)| [*a, *b])
            .collect::<Vec<_>>();
        correspondences.push(json!({"dimension":dim,"slave":slave,"master":master,"matched_nodes":node_pairs.len(),"node_pairs":node_pairs,"affine_transform":affine}));
    }
    if quality["below_0_1"].as_u64().unwrap_or(0) > 0 {
        warnings.push("Certains éléments ont un minSICN inférieur à 0,1 ; contrôler leur acceptabilité dans le solveur".into());
    }
    if recombine {
        warnings.push("La recombinaison peut conserver des triangles ; un maillage tout quadrilatères n'est pas garanti".into());
    }
    let free = api.boundary(&api.entities(2)?, true, false, false)?.len();
    let mut report = json!({"schema":"atelier.report.v1","project_name":project["name"].as_str().unwrap_or("Nouvelle pièce"),"mode":mode,"operation":operation,"display_only":operation=="inspect","dimension":dimension,"requested_dimension":requested_dim,"nodes":preview["point_count"].as_u64().unwrap_or_else(||preview["points"].as_array().map_or(0,Vec::len) as u64),"elements":elements,"cell_counts":cell_counts,"quality":quality,"entity_catalog":catalog,"units":{"input":input_unit,"internal":"m","input_to_internal":factor},"geometry_diagnostics":{"entity_counts":counts(&api)?,"free_boundary_curves":free,"bounds":[bbox[..3].to_vec(),bbox[3..].to_vec()],"bounds_source":if is_cad{"opencascade_stl_tessellation"}else{"discrete_geometry"},"bounds_approximate":is_cad,"bounds_tessellation":if is_cad{json!({"relative_linear_deflection":api.get_number("Mesh.StlLinearDeflection")?,"relative_to_curve_length":api.get_number("Mesh.StlLinearDeflectionRelative")?!=0.,"angular_deflection_radians":api.get_number("Mesh.StlAngularDeflection")?,"not_an_exact_analytical_envelope":true})}else{Value::Null},"intersection_check":"not_exhaustive","source_format":source.as_ref().and_then(|p|p.extension()).and_then(|s|s.to_str())},"repair":repair_report,"groups":groups,"warnings":warnings,"selection":{"scope":scope,"dimension":dimension,"tags":selected.iter().map(|(_,t)|*t).collect::<Vec<_>>()},"correspondences":correspondences,"remesh":if operation=="remesh"{json!({"requested_tags":selected.iter().map(|(_,t)|*t).collect::<Vec<_>>(),"preserved_entities":unselected,"unselected_elements_unchanged":true,"boundary_policy":"existing_boundary_mesh_locked","strategy":"replay_then_local","replay_seconds":replay_seconds,"replayed_local_steps":recipe["steps"].as_array().map_or(0,Vec::len),"coordinate_tolerance_relative":64.*f64::EPSILON})}else{Value::Null},"backend":{"name":"Rust / Gmsh / OpenCASCADE","language":"Rust","gmsh_version":"4.15.2","python_numerical_backend":false},"algorithms":{"surface":algo2,"volume":if dimension==3{Some(algo3)}else{None}},"preflight":{"estimated_elements":estimate,"estimate_kind":"indicative_not_a_memory_bound"},"mesh_settings":mesh,"timings":{"geometry_seconds":geometry_seconds,"generation_seconds":generation_seconds,"validation_seconds":validation_started.elapsed().as_secs_f64(),"total_seconds":started.elapsed().as_secs_f64()},"limitations":["Réparation arbitraire des défauts et intersections non garantie","Remaillage local par entité géométrique, pas par sous-ensemble arbitraire de cellules","La validation du fichier ne remplace pas les contrôles du solveur"]});
    if let Err(error) = crate::quality::enforce(&report["quality"]) {
        if !report_only {
            write_json(&output.join("report.json"), &report)?;
            write_json(&output.join("quality_rejection.json"), &json!({"error":error,"quality":report["quality"]}))?;
        }
        return Err(error);
    }
    if report["quality"]["accepted"] == false {
        report["warnings"].as_array_mut().unwrap().push(json!("Maillage d'essai non admissible : les critères de qualité ne sont pas satisfaits"));
    }
    if !report_only {
        api.set_number("Mesh.SaveParametric", 1.)?;
        api.write(&output.join("mesh.state.msh"))?;
        api.set_number("Mesh.SaveParametric", 0.)?;
    }
    let point_groups = report["groups"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|g| g["dimension"] == 0)
        .filter_map(|g| g["tag"].as_i64().map(|t| (0, t as i32)))
        .collect::<Vec<_>>();
    if !point_groups.is_empty() && !report_only {
        api.remove_physical_groups(&point_groups)?;
    }
    report["groups"] = json!(report["groups"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|g| g["dimension"] != 0)
        .cloned()
        .collect::<Vec<_>>());
    report["artifacts_written"] = json!(!report_only);
    if report_only {
        return Ok(report);
    }
    if operation == "remesh" {
        recipe["steps"]
            .as_array_mut()
            .ok_or("Historique invalide")?
            .push(project.clone());
    }
    write_json(&output.join("replay.json"), &recipe)?;
    api.write(&output.join("mesh.msh"))?;
    write_json(&output.join("preview.json"), &preview)?;
    write_json(&output.join("report.json"), &report)?;
    write_json(&output.join("project.normalized.json"), project)?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn sample_snapshot() -> BTreeMap<i32, Value> {
        BTreeMap::from([(
            1,
            json!([{"type":2,"coordinates":[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]],"oriented_coordinates":[[0.,0.,0.],[1.,0.,0.],[0.,1.,0.]]}]),
        )])
    }
    #[test]
    fn preservation_ignores_only_floating_roundoff_and_node_numbering() {
        let a = sample_snapshot();
        let mut b = sample_snapshot();
        b.get_mut(&1).unwrap()[0]["coordinates"] =
            json!([[0., 1., 0.], [1.0000000000000002, 0., 0.], [0., 0., 0.]]);
        assert!(same_snapshot(&a, &b));
    }
    #[test]
    fn preservation_rejects_changed_geometry_and_cell_count() {
        let a = sample_snapshot();
        let mut b = sample_snapshot();
        b.get_mut(&1).unwrap()[0]["coordinates"][1][0] = json!(1.00001);
        assert!(!same_snapshot(&a, &b));
        let mut b = sample_snapshot();
        b.get_mut(&1).unwrap().as_array_mut().unwrap().clear();
        assert!(!same_snapshot(&a, &b));
    }
    #[test]
    fn preservation_rejects_reversed_shell_normals() {
        let a = sample_snapshot();
        let mut b = sample_snapshot();
        b.get_mut(&1).unwrap()[0]["oriented_coordinates"] =
            json!([[0., 0., 0.], [0., 1., 0.], [1., 0., 0.]]);
        assert!(!same_snapshot(&a, &b));
    }
    #[test]
    fn units_are_exact() {
        assert_eq!(unit_factor("mm").unwrap(), 0.001);
        assert_eq!(unit_factor("in").unwrap(), 0.0254);
        assert!(unit_factor("yd").is_err());
    }
    #[test]
    fn optional_mesh_tuning_bounds_allow_zero_but_not_negative_or_unbounded_values() {
        assert_eq!(nonnegative_integer(&json!({"smoothing":0}),"smoothing",1,100).unwrap(),0);
        assert_eq!(nonnegative_integer(&json!({}),"smoothing",1,100).unwrap(),1);
        assert!(nonnegative_integer(&json!({"smoothing":-1}),"smoothing",1,100).is_err());
        assert!(nonnegative_integer(&json!({"smoothing":101}),"smoothing",1,100).is_err());
        assert!(nonnegative_integer(&json!({"smoothing":1.5}),"smoothing",1,100).is_err());
    }
    #[test]
    fn self_crossing_ring_is_rejected() {
        assert!(ring(&json!([[0, 0], [1, 1], [0, 1], [1, 0]])).is_err());
    }
    #[test]
    fn touching_holes_are_rejected() {
        assert!(validated_rings(
            &json!({"outer":[[0,0],[4,0],[4,4],[0,4]],"holes":[[[0,1],[1,1],[1,2],[0,2]]]})
        )
        .is_err());
    }
}
