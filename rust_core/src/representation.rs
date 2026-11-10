//! Spatial and CAD-topology descriptors shared by live learning and corpus jobs.
//!
//! This is a fixed-resolution MLP representation, not a graph neural network or
//! an exact occupancy field. No mesh quality, target connectivity, or solution
//! result is read here. The reference catalogue is obtained by CAD inspection.
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const INPUTS: usize = 128;
pub const SCHEMA: &str = "atelier.geometry_action_features.v2";
const DESCRIPTOR_SCHEMA: &str = "atelier.geometry_descriptor.v2";

#[derive(Clone)]
struct Entity {
    dim: i64,
    tag: i64,
    bounds: [f64; 6],
    boundary: Vec<(i64, i64)>,
}

fn n(v: &Value, key: &str, default: f64) -> f64 {
    v[key].as_f64().filter(|x| x.is_finite()).unwrap_or(default)
}
fn count(x: usize, scale: f64) -> f64 {
    x as f64 / (scale + x as f64)
}
fn factor(project: &Value) -> f64 {
    match project["units"]["input"].as_str().unwrap_or("mm") {
        "m" => 1.,
        "cm" => 0.01,
        "in" => 0.0254,
        _ => 0.001,
    }
}
fn array3(v: &Value) -> Option<[f64; 3]> {
    let a = v.as_array().filter(|a| a.len() == 3)?;
    Some([a[0].as_f64()?, a[1].as_f64()?, a[2].as_f64()?])
        .filter(|p| p.iter().all(|v| v.is_finite()))
}
fn transform(p: [f64; 3], project: &Value) -> [f64; 3] {
    let t = &project["geometry"]["transform"];
    let scale = n(t, "scale", 1.);
    let angle = n(t, "rotation_degrees", n(t, "angle_degrees", 0.)).to_radians();
    let translation = array3(&t["translation"]).unwrap_or([0.; 3]);
    let f = factor(project);
    let x = p[0] * scale;
    let y = p[1] * scale;
    [
        x * angle.cos() - y * angle.sin() + translation[0] * f,
        x * angle.sin() + y * angle.cos() + translation[1] * f,
        p[2] * scale + translation[2] * f,
    ]
}

fn analytic_points(project: &Value) -> Vec<([f64; 3], f64)> {
    let g = &project["geometry"];
    let f = factor(project);
    let mut p = Vec::new();
    let w = n(g, "width", 1.) * f;
    let h = n(g, "height", 1.) * f;
    let depth = n(g, "depth", n(g, "thickness", 1.)) * f;
    let z = if project["mode"] == "planar" {
        vec![0.]
    } else {
        vec![0., depth]
    };
    match g["kind"].as_str().unwrap_or("") {
        "plate" | "box" | "bracket" => {
            for &x in &[0., w] {
                for &y in &[0., h] {
                    for &zz in &z {
                        p.push(([x, y, zz], 0.2));
                    }
                }
            }
            if let Some(holes) = g["holes"].as_array() {
                for hole in holes {
                    let r = n(hole, "radius", 0.) * f;
                    let x = n(hole, "x", 0.) * f;
                    let y = n(hole, "y", 0.) * f;
                    for i in 0..16 {
                        let a = std::f64::consts::TAU * i as f64 / 16.;
                        for &zz in &z {
                            p.push(([x + r * a.cos(), y + r * a.sin(), zz], 1.));
                        }
                    }
                }
            }
        }
        "polygon" => {
            for ring in
                std::iter::once(&g["outer"]).chain(g["holes"].as_array().into_iter().flatten())
            {
                if let Some(points) = ring.as_array() {
                    for point in points {
                        if let Some(xy) = point.as_array().filter(|a| a.len() >= 2) {
                            if let (Some(x), Some(y)) = (xy[0].as_f64(), xy[1].as_f64()) {
                                p.push(([x * f, y * f, 0.], 1.));
                            }
                        }
                    }
                }
            }
        }
        "sphere" => {
            let r = n(g, "radius", 1.) * f;
            for i in 0..48 {
                let zz = 1. - 2. * (i as f64 + 0.5) / 48.;
                let a = i as f64 * 2.399963229728653;
                let s = (1. - zz * zz).sqrt();
                p.push(([r * s * a.cos(), r * s * a.sin(), r * zz], 1.));
            }
            for axis in 0..3 {
                for sign in [-1., 1.] {
                    let mut q = [0.; 3];
                    q[axis] = r * sign;
                    p.push((q, 0.2));
                }
            }
        }
        _ => {}
    }
    p.into_iter()
        .map(|(p, w)| (transform(p, project), w))
        .collect()
}

fn entities(report: Option<&Value>) -> Vec<Entity> {
    let mut result = Vec::new();
    for row in report
        .and_then(|r| r["entity_catalog"].as_array())
        .into_iter()
        .flatten()
    {
        let Some(b) = row["bbox"].as_array().filter(|b| b.len() == 6) else {
            continue;
        };
        let mut bounds = [0.; 6];
        if b.iter()
            .enumerate()
            .any(|(i, v)| match v.as_f64().filter(|v| v.is_finite()) {
                Some(v) => {
                    bounds[i] = v;
                    false
                }
                None => true,
            })
        {
            continue;
        }
        let mut boundary = Vec::new();
        for e in row["boundaries"].as_array().into_iter().flatten() {
            if let (Some(d), Some(t)) = (e["dimension"].as_i64(), e["tag"].as_i64()) {
                boundary.push((d, t.abs()));
            }
        }
        result.push(Entity {
            dim: row["dimension"]
                .as_i64()
                .or_else(|| row["dim"].as_i64())
                .unwrap_or(-1),
            tag: row["tag"].as_i64().unwrap_or(0),
            bounds,
            boundary,
        });
    }
    result.sort_by_key(|e| (e.dim, e.tag));
    result
}

fn bounds(
    project: &Value,
    report: Option<&Value>,
    es: &[Entity],
    ps: &[([f64; 3], f64)],
) -> [f64; 6] {
    if let Some(b) = report
        .and_then(|r| r["geometry_diagnostics"]["bounds"].as_array())
        .filter(|b| b.len() == 2)
    {
        if let (Some(a), Some(z)) = (array3(&b[0]), array3(&b[1])) {
            if (0..3).all(|i| z[i] >= a[i]) && (0..3).any(|i| z[i] > a[i]) {
                return [a[0], a[1], a[2], z[0], z[1], z[2]];
            }
        }
    }
    let mut b = [
        f64::INFINITY,
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for e in es {
        for i in 0..3 {
            b[i] = b[i].min(e.bounds[i]);
            b[i + 3] = b[i + 3].max(e.bounds[i + 3]);
        }
    }
    for (p, _) in ps {
        for i in 0..3 {
            b[i] = b[i].min(p[i]);
            b[i + 3] = b[i + 3].max(p[i]);
        }
    }
    if b.iter().all(|v| v.is_finite()) && (0..3).any(|i| b[i + 3] > b[i]) {
        b
    } else {
        let f = factor(project);
        [0., 0., 0., f, f, f]
    }
}
fn normal(p: [f64; 3], b: &[f64; 6]) -> [f64; 3] {
    let mut q = [0.; 3];
    for i in 0..3 {
        let d = b[i + 3] - b[i];
        q[i] = if d > 1e-20 { (p[i] - b[i]) / d } else { 0.5 };
        q[i] = q[i].clamp(0., 1.);
    }
    q
}
fn splat(grid: &mut [f64], dims: [usize; 3], p: [f64; 3], weight: f64) {
    let q = [
        p[0] * (dims[0] - 1) as f64,
        p[1] * (dims[1] - 1) as f64,
        p[2] * (dims[2] - 1) as f64,
    ];
    let lo = [
        q[0].floor() as usize,
        q[1].floor() as usize,
        q[2].floor() as usize,
    ];
    for mask in 0..8 {
        let mut idx = [0; 3];
        let mut w = weight;
        for i in 0..3 {
            let frac = q[i] - lo[i] as f64;
            if mask & (1 << i) != 0 {
                idx[i] = (lo[i] + 1).min(dims[i] - 1);
                w *= frac;
            } else {
                idx[i] = lo[i];
                w *= 1. - frac;
            }
        }
        grid[idx[0] + dims[0] * (idx[1] + dims[1] * idx[2])] += w;
    }
}
fn box_points(bounds: &[f64; 6]) -> Vec<[f64; 3]> {
    let mut p = vec![[
        (bounds[0] + bounds[3]) / 2.,
        (bounds[1] + bounds[4]) / 2.,
        (bounds[2] + bounds[5]) / 2.,
    ]];
    for mask in 0..8 {
        p.push(std::array::from_fn(|i| {
            bounds[i + if mask & (1 << i) == 0 { 0 } else { 3 }]
        }));
    }
    p
}
fn field_signature(project: &Value, es: &[Entity], b: &[f64; 6]) -> [f64; 32] {
    let mut bins = [0.; 32];
    for zone in project["critical_zones"].as_array().into_iter().flatten() {
        let d = zone["dimension"].as_i64().unwrap_or(2);
        let q = n(zone, "minimum_quality", 0.35);
        let role = match zone["kind"].as_str() {
            Some("stress") => 1.,
            Some("contact") => 0.85,
            Some("interface") => 0.7,
            _ => 0.5,
        };
        for tag in zone["tags"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_i64)
        {
            if let Some(e) = es.iter().find(|e| e.dim == d && e.tag == tag) {
                for p in box_points(&e.bounds) {
                    splat(
                        &mut bins,
                        [4, 4, 2],
                        normal(p, b),
                        role * (0.5 + 0.5 * q) / 9.,
                    );
                }
            }
        }
    }
    let base = n(&project["mesh"], "size", 0.5).max(1e-30);
    for local in project["local_sizes"].as_array().into_iter().flatten() {
        let d = local["dimension"]
            .as_i64()
            .or_else(|| local["dim"].as_i64())
            .unwrap_or(2);
        let tag = local["tag"].as_i64().unwrap_or(0);
        if let Some(e) = es.iter().find(|e| e.dim == d && e.tag == tag) {
            let weight = (base / n(local, "size", base).max(1e-30)).clamp(0., 4.) / 4.;
            for p in box_points(&e.bounds) {
                splat(&mut bins, [4, 4, 2], normal(p, b), weight / 9.);
            }
        }
    }
    for region in project["refinement_regions"]
        .as_array()
        .into_iter()
        .flatten()
    {
        if let Some(p) = array3(&region["center"]) {
            let f = factor(project);
            splat(
                &mut bins,
                [4, 4, 2],
                normal(p.map(|v| v * f), b),
                (base / n(region, "size", base).max(1e-30)).clamp(0., 4.) / 4.,
            );
        }
    }
    bins.map(|v| v / (1. + v))
}

/// A geometry-only reference descriptor; target quality and connectivity are ignored.
pub fn descriptor(project: &Value, geometry_report: Option<&Value>) -> Value {
    if let Some(d) = geometry_report
        .and_then(|r| r.get("geometry_descriptor"))
        .filter(|d| d["schema"] == DESCRIPTOR_SCHEMA && d["feature_schema"] == SCHEMA)
    {
        return d.clone();
    }
    let es = entities(geometry_report);
    let ps = analytic_points(project);
    let b = bounds(project, geometry_report, &es, &ps);
    let mut bins = [0.; 64];
    for e in &es {
        let weight = match e.dim {
            0 => 0.2,
            1 => 1.,
            2 => 0.4,
            _ => 0.1,
        };
        for p in box_points(&e.bounds) {
            splat(&mut bins, [4, 4, 4], normal(p, &b), weight / 9.);
        }
    }
    for (p, w) in &ps {
        splat(&mut bins, [4, 4, 4], normal(*p, &b), *w);
    }
    let spatial = bins.map(|v| v / (1. + v));
    let mut nodes = Vec::new();
    let mut edges = BTreeSet::new();
    let mut counts = [0usize; 4];
    for e in &es {
        if (0..4).contains(&e.dim) {
            counts[e.dim as usize] += 1;
        }
        nodes.push(json!({"dimension":e.dim,"tag":e.tag,"bbox_internal":e.bounds,"normalized_center":normal([(e.bounds[0]+e.bounds[3])/2.,(e.bounds[1]+e.bounds[4])/2.,(e.bounds[2]+e.bounds[5])/2.],&b)}));
        for &(d, t) in &e.boundary {
            edges.insert((e.dim, e.tag, d, t));
        }
    }
    let node_ids: BTreeSet<_> = es.iter().map(|e| (e.dim, e.tag)).collect();
    let mut adjacency: BTreeMap<(i64, i64), Vec<(i64, i64)>> = BTreeMap::new();
    for &(d, t, bd, bt) in &edges {
        if node_ids.contains(&(bd, bt)) {
            adjacency.entry((d, t)).or_default().push((bd, bt));
            adjacency.entry((bd, bt)).or_default().push((d, t));
        }
    }
    let mut seen = BTreeSet::new();
    let mut components = 0;
    for &id in &node_ids {
        if seen.contains(&id) {
            continue;
        }
        components += 1;
        let mut todo = vec![id];
        while let Some(id) = todo.pop() {
            if seen.insert(id) {
                todo.extend(adjacency.get(&id).into_iter().flatten());
            }
        }
    }
    let g = &project["geometry"];
    let holes = g["holes"].as_array().map_or(0, Vec::len);
    let f = factor(project);
    let diagonal = (0..3)
        .map(|i| (b[i + 3] - b[i]).powi(2))
        .sum::<f64>()
        .sqrt()
        .max(1e-30);
    let mut topology = [0.; 16];
    for i in 0..4 {
        topology[i] = count(counts[i], 16.);
    }
    topology[4] = count(edges.len(), 32.);
    topology[5] = count(components, 4.);
    topology[6] = count(holes, 4.);
    topology[7] = count(g["outer"].as_array().map_or(0, Vec::len), 16.);
    topology[8] = (n(g, "corner_radius", 0.) * f / diagonal).clamp(0., 1.);
    topology[9] = (n(g, "chamfer", 0.) * f / diagonal).clamp(0., 1.);
    topology[10] = (n(g, "wall", 0.) * f / diagonal).clamp(0., 1.);
    topology[11] = (n(g, "throughhole_radius", 0.) * f / diagonal).clamp(0., 1.);
    topology[12] = if edges.is_empty() { 0. } else { 1. };
    topology[13] = if es.is_empty() { 0. } else { 1. };
    topology[14] = count(ps.len(), 64.);
    topology[15] = (n(g, "thickness", 0.) * f / diagonal).clamp(0., 1.);
    json!({"schema":DESCRIPTOR_SCHEMA,"feature_schema":SCHEMA,"input_count":INPUTS,"bounds_internal":b,"boundary_spatial_signature":spatial.to_vec(),"topology_signature":topology.to_vec(),"entity_graph":{"nodes":nodes,"boundaries":edges.iter().map(|&(d,t,bd,bt)|json!({"from":[d,t],"to":[bd,bt]})).collect::<Vec<_>>(),"connected_components":components,"relation_source":if edges.is_empty(){"unavailable"}else{"native_cad_boundary"}},"reference_kind":if es.is_empty(){"parametric_geometry"}else{"cad_catalogue_and_parametric_geometry"},"spatial_method":"trilinear splats of CAD entity boxes and declared parametric boundaries, not exact volume occupancy","quality_or_target_mesh_used":false,"limitations":["Fixed spatial resolution 4x4x4; distinct geometries may still share a descriptor","The MLP uses graph summary statistics, not learned graph message passing"]})
}

/// Geometry, protected regions and candidate controls in a fixed schema.
pub fn features(
    original: &Value,
    candidate: &Value,
    geometry_report: Option<&Value>,
) -> [f64; INPUTS] {
    let d = descriptor(original, geometry_report);
    let es = entities(geometry_report);
    let mut b = [0.; 6];
    for i in 0..6 {
        b[i] = d["bounds_internal"][i]
            .as_f64()
            .unwrap_or(if i < 3 { 0. } else { 1. });
    }
    let f = factor(original);
    let mut extents = [b[3] - b[0], b[4] - b[1], b[5] - b[2]];
    extents.sort_by(f64::total_cmp);
    let extent = extents[2].max(1e-30);
    let base = n(&original["mesh"], "size", 0.5).max(1e-30);
    let mesh = &candidate["mesh"];
    let size = n(mesh, "size", base).max(1e-30);
    let mut v = [0.; INPUTS];
    v[..16].copy_from_slice(&[
        match original["mode"].as_str() {
            Some("volume") => 1.,
            Some("surface") => 0.5,
            _ => 0.,
        },
        match original["geometry"]["kind"].as_str() {
            Some("plate") => 0.,
            Some("polygon") => 0.2,
            Some("box") => 0.4,
            Some("sphere") => 0.6,
            Some("bracket") => 0.8,
            _ => 1.,
        },
        (extents[0] / extent).clamp(0., 1.),
        (extents[1] / extent).clamp(0., 1.),
        (base * f / extent).clamp(0., 1.),
        (n(mesh, "min_size", 0.1) / size).clamp(0., 1.),
        count(
            candidate["local_sizes"].as_array().map_or(0, Vec::len)
                + candidate["refinement_regions"]
                    .as_array()
                    .map_or(0, Vec::len),
            8.,
        ),
        count(
            original["critical_zones"].as_array().map_or(0, Vec::len),
            4.,
        ),
        count(
            original["correspondences"].as_array().map_or(0, Vec::len),
            4.,
        ),
        if mesh["recombine"] == true { 1. } else { 0. },
        if mesh["order"] == 2 { 1. } else { 0. },
        if original["repair"]["enabled"] == true {
            1.
        } else {
            0.
        },
        (size / base).clamp(0., 2.) / 2.,
        match mesh["algorithm"].as_str() {
            Some("delaunay") => 0.5,
            Some("frontal") => 1.,
            _ => 0.,
        },
        (n(mesh, "smoothing", 1.) / 100.).clamp(0., 1.),
        count(es.len(), 64.),
    ]);
    for i in 0..64 {
        v[16 + i] = d["boundary_spatial_signature"][i].as_f64().unwrap_or(0.);
    }
    v[80..112].copy_from_slice(&field_signature(candidate, &es, &b));
    for i in 0..16 {
        v[112 + i] = d["topology_signature"][i].as_f64().unwrap_or(0.);
    }
    v
}

#[cfg(test)]
mod tests {
    use super::*;
    fn plate() -> Value {
        json!({"mode":"planar","units":{"input":"mm"},"geometry":{"kind":"plate","width":10.,"height":6.,"holes":[{"x":2.,"y":2.,"radius":0.5}]},"mesh":{"size":1.,"min_size":0.1,"algorithm":"automatic"}})
    }
    fn report() -> Value {
        json!({"geometry_diagnostics":{"bounds":[[0.,0.,0.],[0.01,0.006,0.]]},"entity_catalog":[{"dimension":1,"tag":1,"bbox":[0.,0.,0.,0.01,0.,0.],"boundaries":[{"dimension":0,"tag":1}]},{"dimension":1,"tag":2,"bbox":[0.,0.006,0.,0.01,0.006,0.]},{"dimension":2,"tag":1,"bbox":[0.,0.,0.,0.01,0.006,0.],"boundaries":[{"dimension":1,"tag":1},{"dimension":1,"tag":2}]}]})
    }
    #[test]
    fn moved_holes_change_features_despite_equal_global_extents() {
        let a = plate();
        let mut b = a.clone();
        b["geometry"]["holes"][0]["x"] = json!(2.1);
        let af = features(&a, &a, None);
        let bf = features(&b, &b, None);
        assert_eq!(&af[..16], &bf[..16]);
        assert_ne!(&af[16..80], &bf[16..80]);
    }
    #[test]
    fn critical_entity_positions_are_distinguished() {
        let mut a = plate();
        a["critical_zones"] =
            json!([{"dimension":1,"tags":[1],"kind":"contact","minimum_quality":0.4}]);
        let mut b = a.clone();
        b["critical_zones"][0]["tags"] = json!([2]);
        let r = report();
        let af = features(&a, &a, Some(&r));
        let bf = features(&b, &b, Some(&r));
        assert_ne!(&af[80..112], &bf[80..112]);
    }
    #[test]
    fn millimetres_and_metres_have_the_same_representation() {
        let a = plate();
        let mut b = a.clone();
        b["units"]["input"] = json!("m");
        for key in ["width", "height"] {
            b["geometry"][key] = json!(a["geometry"][key].as_f64().unwrap() / 1000.);
        }
        for key in ["x", "y", "radius"] {
            b["geometry"]["holes"][0][key] =
                json!(a["geometry"]["holes"][0][key].as_f64().unwrap() / 1000.);
        }
        for key in ["size", "min_size"] {
            b["mesh"][key] = json!(a["mesh"][key].as_f64().unwrap() / 1000.);
        }
        let af = features(&a, &a, None);
        let bf = features(&b, &b, None);
        for (a, b) in af.iter().zip(bf) {
            assert!((a - b).abs() < 1e-12, "{a} != {b}");
        }
    }
    #[test]
    fn quality_and_target_connectivity_are_never_read() {
        let p = plate();
        let a = report();
        let mut b = a.clone();
        b["quality"] = json!({"mean":0.01,"minimum":0.});
        b["preview"] = json!({"points":[[999.,999.,999.]],"cells":[{"data":[[1,2,3]]}]});
        assert_eq!(features(&p, &p, Some(&a)), features(&p, &p, Some(&b)));
    }
    #[test]
    fn descriptor_retains_native_boundary_graph() {
        let p = plate();
        let d = descriptor(&p, Some(&report()));
        assert_eq!(d["quality_or_target_mesh_used"], false);
        assert_eq!(d["entity_graph"]["boundaries"].as_array().unwrap().len(), 3);
        assert_eq!(d["input_count"], 128);
        assert_eq!(features(&p, &p, Some(&report())).len(), 128);
    }
    #[test]
    fn imported_geometry_identity_does_not_depend_on_display_name_or_report_quality() {
        let mut a = plate();
        a["geometry"] = json!({"kind":"import"});
        a["name"] = json!("Premier STEP");
        let mut b = a.clone();
        b["name"] = json!("Même STEP renommé");
        let r = report();
        let mut other = r.clone();
        other["project_name"] = json!("Une autre session");
        other["quality"] = json!({"minimum":0.1,"mean":0.2});
        assert_eq!(features(&a, &a, Some(&r)), features(&b, &b, Some(&other)));
    }

    #[test]
    fn local_refinement_location_is_an_input() {
        let p = plate();
        let mut a = p.clone();
        a["refinement_regions"] = json!([{"center":[2.,2.,0.],"radius":1.,"size":0.2}]);
        let mut b = a.clone();
        b["refinement_regions"][0]["center"] = json!([8., 2., 0.]);
        assert_ne!(
            &features(&p, &a, Some(&report()))[80..112],
            &features(&p, &b, Some(&report()))[80..112]
        );
    }
}
