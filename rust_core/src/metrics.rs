//! Linear-time numerical audit of the primary-corner mesh, implemented in Rust.
//!
//! These metrics supplement Gmsh's high-order Jacobian checks. They deliberately
//! do not claim to certify a curved element from its corner coordinates alone.

use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};

type Point = [f64; 3];

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum Kind { Vertex, Line, Triangle, Quad, Tetra, Hexa, Wedge, Pyramid }

impl Kind {
    fn parse(name: &str) -> Result<Self, String> {
        let name = name.to_ascii_lowercase();
        if name == "vertex" || name == "point" { Ok(Self::Vertex) }
        else if name.starts_with("line") || name.starts_with("segment") { Ok(Self::Line) }
        else if name.starts_with("triangle") || name == "tri" { Ok(Self::Triangle) }
        else if name.starts_with("quad") { Ok(Self::Quad) }
        else if name.starts_with("tetra") { Ok(Self::Tetra) }
        else if name.starts_with("hexa") { Ok(Self::Hexa) }
        else if name.starts_with("wedge") || name.starts_with("prism") { Ok(Self::Wedge) }
        else if name.starts_with("pyramid") { Ok(Self::Pyramid) }
        else { Err(format!("Type d'élément non reconnu pour l'audit Rust : {name}")) }
    }
    fn dim(self) -> usize { match self { Self::Vertex => 0, Self::Line => 1, Self::Triangle | Self::Quad => 2, _ => 3 } }
    fn arity(self) -> usize { match self { Self::Vertex => 1, Self::Line => 2, Self::Triangle => 3, Self::Quad | Self::Tetra => 4, Self::Hexa => 8, Self::Wedge => 6, Self::Pyramid => 5 } }
    fn name(self) -> &'static str { match self { Self::Vertex => "vertex", Self::Line => "line", Self::Triangle => "triangle", Self::Quad => "quad", Self::Tetra => "tetra", Self::Hexa => "hexa", Self::Wedge => "wedge", Self::Pyramid => "pyramid" } }
    fn edges(self) -> &'static [[usize; 2]] {
        match self {
            Self::Vertex => &[], Self::Line => &[[0,1]],
            Self::Triangle => &[[0,1],[1,2],[2,0]],
            Self::Quad => &[[0,1],[1,2],[2,3],[3,0]],
            Self::Tetra => &[[0,1],[0,2],[0,3],[1,2],[1,3],[2,3]],
            Self::Hexa => &[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]],
            Self::Wedge => &[[0,1],[1,2],[2,0],[3,4],[4,5],[5,3],[0,3],[1,4],[2,5]],
            Self::Pyramid => &[[0,1],[1,2],[2,3],[3,0],[0,4],[1,4],[2,4],[3,4]],
        }
    }
    fn faces(self) -> &'static [&'static [usize]] {
        match self {
            Self::Tetra => &[&[0,2,1], &[0,1,3], &[1,2,3], &[2,0,3]],
            Self::Hexa => &[&[0,3,2,1], &[4,5,6,7], &[0,1,5,4], &[1,2,6,5], &[2,3,7,6], &[3,0,4,7]],
            Self::Wedge => &[&[0,2,1], &[3,4,5], &[0,1,4,3], &[1,2,5,4], &[2,0,3,5]],
            Self::Pyramid => &[&[0,3,2,1], &[0,1,4], &[1,2,4], &[2,3,4], &[3,0,4]],
            _ => &[],
        }
    }
}

struct Cell { kind: Kind, nodes: Vec<usize> }

#[derive(Default)]
struct Sum { value: f64, correction: f64 }
impl Sum {
    fn add(&mut self, value: f64) {
        let y = value - self.correction;
        let sum = self.value + y;
        self.correction = (sum - self.value) - y;
        self.value = sum;
    }
}

#[derive(Default)]
struct Stats { count: usize, min: f64, max: f64, sum: Sum }
impl Stats {
    fn add(&mut self, value: f64) {
        if self.count == 0 { self.min = value; self.max = value; }
        else { self.min = self.min.min(value); self.max = self.max.max(value); }
        self.count += 1;
        self.sum.add(value);
    }
    fn value(&self) -> Value {
        if self.count == 0 { json!({"count":0,"minimum":null,"maximum":null,"mean":null}) }
        else { json!({"count":self.count,"minimum":self.min,"maximum":self.max,"mean":self.sum.value / self.count as f64}) }
    }
}

fn sub(a: Point, b: Point) -> Point { [a[0]-b[0], a[1]-b[1], a[2]-b[2]] }
fn cross(a: Point, b: Point) -> Point { [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]] }
fn dot(a: Point, b: Point) -> f64 { a[0]*b[0] + a[1]*b[1] + a[2]*b[2] }
fn norm(a: Point) -> f64 { a[0].hypot(a[1]).hypot(a[2]) }
fn distance(a: Point, b: Point) -> f64 { norm(sub(a,b)) }
fn triangle_area(a: Point, b: Point, c: Point) -> f64 { norm(cross(sub(b,a),sub(c,a))) * 0.5 }
fn tetra_volume(a: Point, b: Point, c: Point, d: Point) -> f64 { dot(sub(b,a), cross(sub(c,a), sub(d,a))) / 6.0 }

fn triangle_quality(p: &[Point]) -> f64 {
    let denominator = distance(p[0],p[1]).powi(2) + distance(p[1],p[2]).powi(2) + distance(p[2],p[0]).powi(2);
    if denominator == 0.0 { 0.0 } else { (4.0 * 3.0_f64.sqrt() * triangle_area(p[0],p[1],p[2]) / denominator).clamp(0.0,1.0) }
}

fn tetra_quality(p: &[Point], volume: f64) -> f64 {
    let denominator: f64 = Kind::Tetra.edges().iter().map(|e| distance(p[e[0]],p[e[1]]).powi(2)).sum();
    if denominator == 0.0 || volume == 0.0 { 0.0 }
    else { (volume.signum() * 12.0 * (3.0*volume.abs()).powf(2.0/3.0) / denominator).clamp(-1.0,1.0) }
}

fn quad_quality(p: &[Point]) -> f64 {
    // Newell's normal is defined by the cell's own winding. This detects local
    // concavity but cannot decide whether an entire shell has been reversed.
    let mut normal = [0.0;3];
    for i in 0..4 {
        let term = cross(p[i],p[(i+1)%4]);
        for d in 0..3 { normal[d] += term[d]; }
    }
    let magnitude = norm(normal);
    if magnitude == 0.0 { return 0.0; }
    for value in &mut normal { *value /= magnitude; }
    let mut minimum = 1.0_f64;
    for i in 0..4 {
        let a = sub(p[(i+1)%4],p[i]);
        let b = sub(p[(i+3)%4],p[i]);
        let denominator = norm(a)*norm(b);
        let q = if denominator == 0.0 { 0.0 } else { dot(cross(a,b),normal) / denominator };
        minimum = minimum.min(q);
    }
    minimum.clamp(-1.0,1.0)
}

fn hexa_quality(p: &[Point]) -> f64 {
    let triples = [[1,3,4],[2,0,5],[3,1,6],[0,2,7],[7,5,0],[4,6,1],[5,7,2],[6,4,3]];
    let mut minimum = 1.0_f64;
    for (i, neighbors) in triples.iter().enumerate() {
        let a = sub(p[neighbors[0]],p[i]);
        let b = sub(p[neighbors[1]],p[i]);
        let c = sub(p[neighbors[2]],p[i]);
        let denominator = norm(a)*norm(b)*norm(c);
        let q = if denominator == 0.0 { 0.0 } else { dot(a,cross(b,c)) / denominator };
        minimum = minimum.min(q);
    }
    minimum.clamp(-1.0,1.0)
}

fn primary_volume(kind: Kind, p: &[Point]) -> f64 {
    let decomposition: &[[usize;4]] = match kind {
        Kind::Tetra => &[[0,1,2,3]],
        Kind::Hexa => &[[0,1,2,6],[0,2,3,6],[0,3,7,6],[0,7,4,6],[0,4,5,6],[0,5,1,6]],
        Kind::Wedge => &[[0,1,2,3],[1,2,4,3],[2,4,5,3]],
        Kind::Pyramid => &[[0,1,2,4],[0,2,3,4]],
        _ => &[],
    };
    let mut sum = Sum::default();
    for t in decomposition { sum.add(tetra_volume(p[t[0]],p[t[1]],p[t[2]],p[t[3]])); }
    sum.value
}

fn physical(value: f64, scale: f64, power: i32) -> Result<f64,String> {
    let mut result = value;
    for _ in 0..power { result *= scale; }
    if !result.is_finite() || (value != 0.0 && result == 0.0) {
        Err("Une mesure géométrique dépasse la plage numérique représentable".to_string())
    } else { Ok(result) }
}

fn parse_points(document: &Value) -> Result<Vec<Point>,String> {
    let array = document.get("points").and_then(Value::as_array).ok_or("Liste points absente")?;
    let mut points = Vec::with_capacity(array.len());
    for (index, item) in array.iter().enumerate() {
        let row = item.as_array().ok_or_else(|| format!("Point {index} invalide"))?;
        if row.len() != 3 { return Err(format!("Point {index} : trois coordonnées attendues")); }
        let mut point = [0.0;3];
        for d in 0..3 {
            point[d] = row[d].as_f64().filter(|x| x.is_finite()).ok_or_else(|| format!("Point {index} : coordonnée non finie ou non numérique"))?;
        }
        points.push(point);
    }
    Ok(points)
}

/// Audit primary-corner coordinates and connectivity in O(nodes + elements).
pub fn analyze_preview(preview: &Value) -> Result<Value,String> {
    let physical_points = parse_points(preview)?;
    let blocks = preview.get("cells").and_then(Value::as_array).ok_or("Liste cells absente")?;
    let mut cells = Vec::new();
    let mut used = HashSet::new();
    let mut counts = Map::new();
    let mut extra_connectivity = 0;
    for (block_index, block) in blocks.iter().enumerate() {
        let name = block.get("type").and_then(Value::as_str).ok_or_else(|| format!("Type du bloc {block_index} absent"))?;
        let kind = Kind::parse(name)?;
        let rows = block.get("data").and_then(Value::as_array).ok_or_else(|| format!("Connectivité du bloc {block_index} absente"))?;
        let count = counts.get(name).and_then(Value::as_u64).unwrap_or(0) + rows.len() as u64;
        counts.insert(name.to_string(), json!(count));
        for (row_index, item) in rows.iter().enumerate() {
            let row = item.as_array().ok_or_else(|| format!("Connectivité {block_index}:{row_index} invalide"))?;
            if row.len() < kind.arity() { return Err(format!("Élément {block_index}:{row_index} incomplet")); }
            let mut nodes = Vec::with_capacity(kind.arity());
            let mut all_nodes = HashSet::new();
            for (corner, value) in row.iter().enumerate() {
                let index = value.as_u64().and_then(|x| usize::try_from(x).ok()).filter(|x| *x < physical_points.len())
                    .ok_or_else(|| format!("Indice hors plage ou non entier dans {block_index}:{row_index}"))?;
                if !all_nodes.insert(index) { return Err(format!("Sommet répété dans l'élément {block_index}:{row_index}")); }
                used.insert(index);
                if corner < kind.arity() { nodes.push(index); }
            }
            extra_connectivity += row.len() - kind.arity();
            cells.push(Cell {kind,nodes});
        }
    }
    let dimension = cells.iter().map(|cell| cell.kind.dim()).max().unwrap_or(0);
    let mut minimum = [f64::INFINITY;3];
    let mut maximum = [f64::NEG_INFINITY;3];
    for point in &physical_points {
        for d in 0..3 { minimum[d] = minimum[d].min(point[d]); maximum[d] = maximum[d].max(point[d]); }
    }
    let scale = if physical_points.is_empty() { 1.0 } else { (0..3).map(|d| maximum[d]-minimum[d]).fold(0.0_f64,f64::max) };
    if !scale.is_finite() { return Err("Étendue des coordonnées hors plage numérique".to_string()); }
    if scale == 0.0 && cells.iter().any(|cell| cell.kind != Kind::Vertex) { return Err("Étendue géométrique nulle".to_string()); }
    let scale = if scale == 0.0 { 1.0 } else { scale };
    let points: Vec<Point> = physical_points.iter().map(|point| [(point[0]-minimum[0])/scale,(point[1]-minimum[1])/scale,(point[2]-minimum[2])/scale]).collect();

    let mut unique_cells = HashSet::new();
    let mut duplicate_cells = 0;
    let mut edges: HashMap<[usize;2],usize> = HashMap::new();
    // Keep one oriented copy along with the incidence count. Re-finding the
    // owning element for each boundary face would turn this audit quadratic.
    let mut faces: HashMap<Vec<usize>,(usize,Vec<usize>)> = HashMap::new();
    let mut shape_stats: HashMap<&'static str,Stats> = HashMap::new();
    let mut top_quality = Stats::default();
    let mut signed_tetra = Stats::default();
    let mut signed_volume = Stats::default();
    let mut triangle_area_sum = Sum::default();
    let mut stored_surface_area = Sum::default();
    let mut positive_tetra_volume = Sum::default();
    let mut positive_volume = Sum::default();
    let mut inverted_tetra = 0;
    let mut nonpositive_volume = 0;
    let mut top_count = 0;
    let mut degenerate_top = 0;
    for cell in &cells {
        let mut key = cell.nodes.clone(); key.sort_unstable();
        if !unique_cells.insert((cell.kind,key)) { duplicate_cells += 1; }
        let mut corner_buffer = [[0.0;3];8];
        for (corner,id) in cell.nodes.iter().enumerate() { corner_buffer[corner] = points[*id]; }
        let p = &corner_buffer[..cell.kind.arity()];
        if cell.kind == Kind::Triangle { triangle_area_sum.add(triangle_area(p[0],p[1],p[2])); }
        if cell.kind.dim() == 2 {
            stored_surface_area.add(triangle_area(p[0],p[1],p[2]));
            if cell.kind == Kind::Quad { stored_surface_area.add(triangle_area(p[0],p[2],p[3])); }
        }
        if cell.kind == Kind::Tetra {
            let volume = tetra_volume(p[0],p[1],p[2],p[3]);
            signed_tetra.add(volume);
            if volume < 0.0 { inverted_tetra += 1; }
            if volume > 0.0 { positive_tetra_volume.add(volume); }
        }
        if cell.kind.dim() != dimension { continue; }
        top_count += 1;
        for edge in cell.kind.edges() {
            let a = cell.nodes[edge[0]]; let b = cell.nodes[edge[1]];
            let key = if a < b { [a,b] } else { [b,a] };
            *edges.entry(key).or_insert(0) += 1;
        }
        for face in cell.kind.faces() {
            let mut ids: Vec<usize> = face.iter().map(|i| cell.nodes[*i]).collect();
            let ordered = ids.clone();
            ids.sort_unstable();
            faces.entry(ids).or_insert_with(|| (0,ordered)).0 += 1;
        }
        if dimension == 3 {
            let volume = primary_volume(cell.kind,&p);
            signed_volume.add(volume);
            if volume > 0.0 { positive_volume.add(volume); } else { nonpositive_volume += 1; }
        }
        let quality = match cell.kind {
            Kind::Triangle => Some(triangle_quality(&p)),
            Kind::Quad => Some(quad_quality(&p)),
            Kind::Tetra => Some(tetra_quality(&p,tetra_volume(p[0],p[1],p[2],p[3]))),
            Kind::Hexa => Some(hexa_quality(&p)),
            _ => None,
        };
        if let Some(q) = quality {
            if !q.is_finite() { return Err("Qualité hors plage numérique".to_string()); }
            if q == 0.0 { degenerate_top += 1; }
            shape_stats.entry(cell.kind.name()).or_default().add(q);
            top_quality.add(q);
        }
    }
    let mut edge_lengths = Stats::default();
    for edge in edges.keys() { edge_lengths.add(physical(distance(points[edge[0]],points[edge[1]]),scale,1)?); }
    let mut boundary_area = Sum::default();
    for (incidence, ids) in faces.values() {
        if *incidence == 1 {
            boundary_area.add(triangle_area(points[ids[0]],points[ids[1]],points[ids[2]]));
            if ids.len() == 4 { boundary_area.add(triangle_area(points[ids[0]],points[ids[2]],points[ids[3]])); }
        }
    }
    let physical_stats = |stats: &Stats, power: i32| -> Result<Value,String> {
        if stats.count == 0 { Ok(stats.value()) }
        else { Ok(json!({"count":stats.count,"minimum":physical(stats.min,scale,power)?,"maximum":physical(stats.max,scale,power)?,"mean":physical(stats.sum.value/stats.count as f64,scale,power)?})) }
    };
    let mut qualities = Map::new();
    let mut names: Vec<_> = shape_stats.keys().copied().collect(); names.sort_unstable();
    for name in names { qualities.insert(name.to_string(),shape_stats[name].value()); }
    Ok(json!({
        "schema":"atelier.rust_metrics.v1", "computation_backend":"rust", "revision":1,
        "points":physical_points.len(), "used_points":used.len(), "unused_preview_points":physical_points.len()-used.len(),
        "stored_elements":cells.len(), "cell_counts":counts, "top_dimension":dimension, "top_dimension_elements":top_count,
        "edges":edge_lengths.value(), "unique_top_dimension_edges":edges.len(),
        "shape_quality":qualities, "top_dimension_quality":top_quality.value(),
        "signed_tetra_volume":physical_stats(&signed_tetra,3)?,
        "sum_positive_tetra_volume":physical(positive_tetra_volume.value,scale,3)?,
        "signed_primary_volume":physical_stats(&signed_volume,3)?,
        "sum_positive_primary_volume":physical(positive_volume.value,scale,3)?,
        "stored_triangle_area":physical(triangle_area_sum.value,scale,2)?,
        "stored_surface_area":physical(stored_surface_area.value,scale,2)?,
        "linear_boundary_area":if dimension == 3 { Some(physical(boundary_area.value,scale,2)?) } else {None},
        "topology":{
            "duplicate_cells":duplicate_cells,
            "open_surface_edges":if dimension == 2 { Some(edges.values().filter(|n| **n == 1).count()) } else {None},
            "interior_surface_edges":if dimension == 2 { Some(edges.values().filter(|n| **n == 2).count()) } else {None},
            "nonmanifold_surface_edges":if dimension == 2 { Some(edges.values().filter(|n| **n > 2).count()) } else {None},
            "boundary_volume_faces":if dimension == 3 { Some(faces.values().filter(|n| n.0 == 1).count()) } else {None},
            "interior_volume_faces":if dimension == 3 { Some(faces.values().filter(|n| n.0 == 2).count()) } else {None},
            "nonmanifold_volume_faces":if dimension == 3 { Some(faces.values().filter(|n| n.0 > 2).count()) } else {None},
            "inverted_tetrahedra":inverted_tetra,"nonpositive_primary_volume_elements":nonpositive_volume,
            "zero_shape_quality_top_elements":degenerate_top
        },
        "extra_connectivity_nodes_not_used_for_corner_metrics":extra_connectivity,
        "definitions":{
            "coordinates":"Input coordinates and distances use the internal SI length unit (metre); areas m^2 and volumes m^3.",
            "scope":"Edge and quality statistics use only top-dimensional cells. Stored surface areas count explicit surface cells, not displayed triangulation duplicates.",
            "triangle":"4*sqrt(3)*area/sum(edge_length^2); 1 for an equilateral triangle; orientation independent.",
            "tetra":"sign(V)*12*(3*abs(V))^(2/3)/sum(six_edge_length^2); signed volume det(b-a,c-a,d-a)/6.",
            "quad":"Minimum corner scaled Jacobian projected onto the cell Newell normal; detects local concavity, not a globally reversed shell.",
            "hexa":"Minimum of eight canonical corner scaled Jacobians; not an interior or curved-element validity certificate.",
            "primary_volume":"Straight primary corners; hexahedra use a six-tetrahedron diagonal fan, wedges three tetrahedra, pyramids two.",
            "top_dimension_quality":"Mixed element families use different indicators. Compare per-family statistics; this combined minimum is not a universal quality standard.",
            "high_order":"Only primary corners are audited. Gmsh high-order Jacobian checks remain necessary for curved elements.",
            "topology":"Incidence counts and duplicate connectivity are checked; arbitrary distant intersections and coincident coordinates with distinct IDs are not detected."
        }
    }))
}

/// Scale a preview's coordinates and bounds atomically after full validation.
/// Connectivity is unchanged; physical metrics must be recomputed afterwards.
pub fn scale_mesh_json(document: &mut Value, factor: f64) -> Result<(),String> {
    if !factor.is_finite() || factor <= 0.0 { return Err("Facteur d'échelle fini strictement positif attendu".to_string()); }
    let points = parse_points(document)?;
    let scale_point = |p: Point| -> Result<Value,String> {
        let result = [p[0]*factor,p[1]*factor,p[2]*factor];
        if result.iter().any(|x| !x.is_finite()) || p.iter().zip(result.iter()).any(|(a,b)| *a != 0.0 && *b == 0.0) {
            return Err("Conversion d'unité hors plage numérique".to_string());
        }
        Ok(json!(result))
    };
    let scaled_points: Vec<Value> = points.into_iter().map(scale_point).collect::<Result<_,_>>()?;
    let mut scaled_bounds = None;
    if let Some(bounds) = document.get("bounds") {
        let rows = bounds.as_array().filter(|x| x.len()==2).ok_or("Bornes attendues sous la forme [[min_x,min_y,min_z],[max_x,max_y,max_z]]")?;
        let temp = json!({"points":rows});
        let converted = parse_points(&temp)?;
        scaled_bounds = Some(Value::Array(converted.into_iter().map(scale_point).collect::<Result<_,_>>()?));
    }
    let object = document.as_object_mut().ok_or("Objet de maillage attendu")?;
    object.insert("points".to_string(),Value::Array(scaled_points));
    if let Some(bounds) = scaled_bounds { object.insert("bounds".to_string(),bounds); }
    // Leaving a copied SI metric unchanged would mislabel its units.
    object.remove("metrics");
    object.remove("rust_metrics");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn close(actual:f64,expected:f64) { assert!((actual-expected).abs() <= 1e-12*expected.abs().max(1.0),"{actual} != {expected}"); }
    fn number(v:&Value,path:&str)->f64 { v.pointer(path).unwrap().as_f64().unwrap() }
    fn tet() -> Value { json!({"points":[[0,0,0],[1,0,0],[0,1,0],[0,0,1]],"cells":[{"type":"tetra","data":[[0,1,2,3]]}]}) }
    #[test]
    fn equilateral_triangle_has_unit_quality() {
        let p=json!({"points":[[0,0,0],[1,0,0],[0.5,3.0_f64.sqrt()/2.0,0]],"cells":[{"type":"triangle","data":[[0,1,2]]}]});
        let a=analyze_preview(&p).unwrap(); close(number(&a,"/shape_quality/triangle/minimum"),1.0);
        close(number(&a,"/stored_triangle_area"),3.0_f64.sqrt()/4.0);
        assert_eq!(a["topology"]["open_surface_edges"],3);
    }
    #[test]
    fn rectangle_and_its_boundary_are_not_double_counted() {
        let p=json!({"points":[[0,0,0],[2,0,0],[2,1,0],[0,1,0]],"cells":[{"type":"line","data":[[0,1],[1,2],[2,3],[3,0]]},{"type":"quad","data":[[0,1,2,3]]}]});
        let a=analyze_preview(&p).unwrap(); assert_eq!(a["top_dimension_elements"],1);
        assert_eq!(a["unique_top_dimension_edges"],4); close(number(&a,"/shape_quality/quad/minimum"),1.0);
        close(number(&a,"/stored_surface_area"),2.0); close(number(&a,"/edges/minimum"),1.0); close(number(&a,"/edges/maximum"),2.0);
    }
    #[test]
    fn tetra_signed_volume_and_boundary_area() {
        let a=analyze_preview(&tet()).unwrap(); close(number(&a,"/signed_tetra_volume/minimum"),1.0/6.0);
        close(number(&a,"/sum_positive_tetra_volume"),1.0/6.0);
        close(number(&a,"/linear_boundary_area"),1.5+3.0_f64.sqrt()/2.0);
        assert_eq!(a["topology"]["boundary_volume_faces"],4); assert_eq!(a["topology"]["inverted_tetrahedra"],0);
    }
    #[test]
    fn inverted_tetra_is_reported_not_hidden_by_absolute_value() {
        let mut p=tet(); p["cells"][0]["data"][0]=json!([0,2,1,3]);
        let a=analyze_preview(&p).unwrap(); assert_eq!(a["topology"]["inverted_tetrahedra"],1);
        close(number(&a,"/signed_tetra_volume/minimum"),-1.0/6.0); assert!(number(&a,"/shape_quality/tetra/minimum")<0.0);
        close(number(&a,"/sum_positive_tetra_volume"),0.0);
    }
    #[test]
    fn regular_tetra_has_unit_quality() {
        let p=json!({"points":[[0,0,0],[1,0,0],[0.5,3.0_f64.sqrt()/2.0,0],[0.5,3.0_f64.sqrt()/6.0,(2.0_f64/3.0).sqrt()]],"cells":[{"type":"tetra","data":[[0,1,2,3]]}]});
        let a=analyze_preview(&p).unwrap(); close(number(&a,"/shape_quality/tetra/minimum"),1.0);
    }
    #[test]
    fn cube_has_unit_corner_jacobians_and_volume() {
        let p=json!({"points":[[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]],"cells":[{"type":"hexa","data":[[0,1,2,3,4,5,6,7]]}]});
        let a=analyze_preview(&p).unwrap(); close(number(&a,"/shape_quality/hexa/minimum"),1.0);
        close(number(&a,"/sum_positive_primary_volume"),1.0); close(number(&a,"/linear_boundary_area"),6.0);
    }
    #[test]
    fn adjacent_triangles_share_one_interior_edge() {
        let p=json!({"points":[[0,0,0],[1,0,0],[1,1,0],[0,1,0]],"cells":[{"type":"triangle","data":[[0,1,2],[0,2,3]]}]});
        let a=analyze_preview(&p).unwrap(); assert_eq!(a["unique_top_dimension_edges"],5);
        assert_eq!(a["topology"]["open_surface_edges"],4); assert_eq!(a["topology"]["interior_surface_edges"],1);
    }
    #[test]
    fn duplicate_and_nonmanifold_connectivity_are_audited() {
        let p=json!({"points":[[0,0,0],[1,0,0],[0,1,0],[0,-1,0],[0,0,1]],"cells":[{"type":"triangle","data":[[0,1,2],[1,0,3],[0,1,4],[2,1,0]]}]});
        let a=analyze_preview(&p).unwrap(); assert_eq!(a["topology"]["duplicate_cells"],1);
        assert_eq!(a["topology"]["nonmanifold_surface_edges"],1);
    }
    #[test]
    fn malformed_indices_and_nonfinite_coordinates_fail() {
        for row in [json!([0,1,2,-1]),json!([0,1,2,4]),json!([0,1,2,2]),json!([0,1,2,1.5])] {
            let mut p=tet(); p["cells"][0]["data"][0]=row; assert!(analyze_preview(&p).is_err());
        }
        let mut p=tet(); p["points"][0][0]=Value::Null; assert!(analyze_preview(&p).is_err());
    }
    #[test]
    fn translation_and_si_scaling_preserve_quality() {
        let original=analyze_preview(&tet()).unwrap(); let mut p=tet();
        for row in p["points"].as_array_mut().unwrap() {
            for x in row.as_array_mut().unwrap() { *x=json!(1000.0+x.as_f64().unwrap()*0.001); }
        }
        let a=analyze_preview(&p).unwrap();
        assert!((number(&a,"/shape_quality/tetra/minimum")-number(&original,"/shape_quality/tetra/minimum")).abs()<1e-9);
        assert!((number(&a,"/sum_positive_tetra_volume")-1e-9/6.0).abs()<1e-17);
    }
    #[test]
    fn scale_conversion_is_atomic_and_updates_bounds() {
        let mut p=tet(); p["bounds"]=json!([[0,0,0],[1,1,1]]); p["metrics"]=json!({"old":true});
        scale_mesh_json(&mut p,1000.0).unwrap(); assert_eq!(p["points"][1][0],1000.0); assert_eq!(p["bounds"][1][2],1000.0);
        assert!(p.get("metrics").is_none()); let before=p.clone();
        assert!(scale_mesh_json(&mut p,f64::INFINITY).is_err()); assert_eq!(p,before);
        assert!(scale_mesh_json(&mut p,1e308).is_err()); assert_eq!(p,before);
    }
    #[test]
    fn curved_connectivity_is_validated_but_not_misrepresented_as_curved_quality() {
        let p=json!({"points":[[0,0,0],[1,0,0],[0,1,0],[0.5,0,0],[0.5,0.5,0],[0,0.5,0]],"cells":[{"type":"triangle6","data":[[0,1,2,3,4,5]]}]});
        let a=analyze_preview(&p).unwrap(); assert_eq!(a["extra_connectivity_nodes_not_used_for_corner_metrics"],3);
        assert_eq!(a["used_points"],6); close(number(&a,"/stored_triangle_area"),0.5);
    }
}
