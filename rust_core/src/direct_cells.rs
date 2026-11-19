//! Direct structured connectivity: no simplex mesh, recombination or order elevation.
//!
//! Every requested cell, including its order-two nodes, is emitted in one pass
//! from a logical grid. The CAD adapter remains responsible for classification
//! and independent Jacobian/quality checks before publishing a mesh.
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};

type R<T> = Result<T, String>;
type Point = [f64; 3];
const MAX_NODES: usize = 8_000_000;
const MAX_CONNECTIVITY: usize = 64_000_000;

#[derive(Debug)]
pub struct DirectBlock {
    pub dimension: i32,
    pub element_type: i32,
    pub nodes_per_element: usize,
    /// Zero-based indices into DirectMesh::points.
    pub connectivity: Vec<usize>,
    pub entity_hint: String,
}
#[derive(Debug)]
pub struct DirectMesh {
    pub points: Vec<Point>,
    pub blocks: Vec<DirectBlock>,
    pub metadata: Value,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Family {
    Line,
    Triangle,
    Quad,
    Tet,
    Hex,
    Prism,
    Pyramid,
}
impl Family {
    fn name(self) -> &'static str {
        match self {
            Self::Line => "line",
            Self::Triangle => "triangle",
            Self::Quad => "quadrilateral",
            Self::Tet => "tetrahedron",
            Self::Hex => "hexahedron",
            Self::Prism => "prism",
            Self::Pyramid => "pyramid",
        }
    }
    fn dim(self) -> i32 {
        match self {
            Self::Line => 1,
            Self::Triangle | Self::Quad => 2,
            _ => 3,
        }
    }
    fn corners(self) -> usize {
        match self {
            Self::Line => 2,
            Self::Triangle => 3,
            Self::Quad | Self::Tet => 4,
            Self::Pyramid => 5,
            Self::Prism => 6,
            Self::Hex => 8,
        }
    }
    fn edges(self) -> &'static [(usize, usize)] {
        match self {
            Self::Line => &[(0, 1)],
            Self::Triangle => &[(0, 1), (1, 2), (2, 0)],
            Self::Quad => &[(0, 1), (1, 2), (2, 3), (3, 0)],
            Self::Tet => &[(0, 1), (1, 2), (2, 0), (3, 0), (3, 2), (3, 1)],
            Self::Hex => &[
                (0, 1),
                (0, 3),
                (0, 4),
                (1, 2),
                (1, 5),
                (2, 3),
                (2, 6),
                (3, 7),
                (4, 5),
                (4, 7),
                (5, 6),
                (6, 7),
            ],
            Self::Prism => &[
                (0, 1),
                (0, 2),
                (0, 3),
                (1, 2),
                (1, 4),
                (2, 5),
                (3, 4),
                (3, 5),
                (4, 5),
            ],
            Self::Pyramid => &[
                (0, 1),
                (0, 3),
                (0, 4),
                (1, 2),
                (1, 4),
                (2, 3),
                (2, 4),
                (3, 4),
            ],
        }
    }
    fn faces(self) -> &'static [&'static [usize]] {
        match self {
            Self::Tet => &[&[0, 2, 1], &[0, 1, 3], &[0, 3, 2], &[3, 1, 2]],
            Self::Hex => &[
                &[0, 3, 2, 1],
                &[0, 1, 5, 4],
                &[0, 4, 7, 3],
                &[1, 2, 6, 5],
                &[2, 3, 7, 6],
                &[4, 5, 6, 7],
            ],
            Self::Prism => &[
                &[0, 2, 1],
                &[3, 4, 5],
                &[0, 1, 4, 3],
                &[0, 3, 5, 2],
                &[1, 2, 5, 4],
            ],
            Self::Pyramid => &[
                &[0, 1, 4],
                &[3, 0, 4],
                &[1, 2, 4],
                &[2, 3, 4],
                &[0, 3, 2, 1],
            ],
            _ => &[],
        }
    }
    fn kind(self, order: usize, complete: bool) -> (i32, usize) {
        if order == 1 {
            return match self {
                Self::Line => (1, 2),
                Self::Triangle => (2, 3),
                Self::Quad => (3, 4),
                Self::Tet => (4, 4),
                Self::Hex => (5, 8),
                Self::Prism => (6, 6),
                Self::Pyramid => (7, 5),
            };
        }
        match self {
            Self::Line => (8, 3),
            Self::Triangle => (9, 6),
            Self::Quad => {
                if complete {
                    (10, 9)
                } else {
                    (16, 8)
                }
            }
            Self::Tet => (11, 10),
            Self::Hex => {
                if complete {
                    (12, 27)
                } else {
                    (17, 20)
                }
            }
            Self::Prism => {
                if complete {
                    (13, 18)
                } else {
                    (18, 15)
                }
            }
            Self::Pyramid => {
                if complete {
                    (14, 14)
                } else {
                    (19, 13)
                }
            }
        }
    }
}
fn average(points: &[Point]) -> Point {
    let mut p = [0.; 3];
    for a in points {
        for k in 0..3 {
            p[k] += a[k] / points.len() as f64;
        }
    }
    p
}
fn key_nodes(nodes: &[usize]) -> Vec<usize> {
    let mut key = nodes.to_vec();
    key.sort_unstable();
    key
}
fn edge_key(a: usize, b: usize) -> (usize, usize) {
    if a < b {
        (a, b)
    } else {
        (b, a)
    }
}
fn scalar(v: &Value, label: &str, default: f64) -> R<f64> {
    let x = if v.is_null() {
        default
    } else {
        v.as_f64()
            .ok_or_else(|| format!("{label} doit être numérique"))?
    };
    if !x.is_finite() {
        return Err(format!("{label} doit être fini"));
    }
    Ok(x)
}
fn positive(v: &Value, label: &str, default: f64) -> R<f64> {
    let x = scalar(v, label, default)?;
    if x <= 0. {
        Err(format!("{label} doit être strictement positif"))
    } else {
        Ok(x)
    }
}

/// Ratio between consecutive grid intervals, evaluated in a stable log scale.
fn coordinates(n: usize, ratio: f64) -> R<Vec<f64>> {
    if n == 0 || n > 2000 || !ratio.is_finite() || ratio <= 0. {
        return Err(
            "Divisions entre 1 et 2000 et progression strictement positive attendues".into(),
        );
    }
    if (ratio - 1.).abs() < 1e-14 {
        return Ok((0..=n).map(|i| i as f64 / n as f64).collect());
    }
    let log = ratio.ln();
    let shift = if log > 0. { (n - 1) as f64 * log } else { 0. };
    let weights = (0..n)
        .map(|i| (i as f64 * log - shift).exp())
        .collect::<Vec<_>>();
    let total = weights.iter().sum::<f64>();
    let mut out = Vec::with_capacity(n + 1);
    out.push(0.);
    let mut sum = 0.;
    for w in weights {
        sum += w;
        out.push(sum / total);
    }
    out[n] = 1.;
    if out.windows(2).any(|p| p[1] - p[0] <= 64. * f64::EPSILON) {
        return Err("Progression trop forte : certains intervalles ne sont plus résolus en précision flottante".into());
    }
    Ok(out)
}
#[derive(Clone)]
enum Footprint {
    Quad([[f64; 2]; 4]),
    Ring {
        center: [f64; 2],
        radius: f64,
        a: [f64; 2],
        b: [f64; 2],
        theta: f64,
        sweep: f64,
        side: usize,
    },
}
impl Footprint {
    fn map(&self, u: f64, v: f64) -> [f64; 2] {
        match self {
            Self::Quad(q) => {
                let w = [(1. - u) * (1. - v), u * (1. - v), u * v, (1. - u) * v];
                let mut p = [0.; 2];
                for i in 0..4 {
                    for k in 0..2 {
                        p[k] += w[i] * q[i][k];
                    }
                }
                p
            }
            Self::Ring {
                center,
                radius,
                a,
                b,
                theta,
                sweep,
                ..
            } => {
                let angle = theta + v * sweep;
                let inner = [
                    center[0] + radius * angle.cos(),
                    center[1] + radius * angle.sin(),
                ];
                let outer = [a[0] * (1. - v) + b[0] * v, a[1] * (1. - v) + b[1] * v];
                [
                    inner[0] * (1. - u) + outer[0] * u,
                    inner[1] * (1. - u) + outer[1] * u,
                ]
            }
        }
    }
    fn hint(&self, params: &[Point], include_z: bool) -> Option<String> {
        let constant =
            |axis: usize, value: f64| params.iter().all(|p| (p[axis] - value).abs() <= 1e-13);
        let mut labels = Vec::new();
        match self {
            Self::Quad(_) => {
                for (axis, value, name) in [
                    (0, 0., "umin"),
                    (0, 1., "umax"),
                    (1, 0., "vmin"),
                    (1, 1., "vmax"),
                ] {
                    if constant(axis, value) {
                        labels.push(name.to_string());
                    }
                }
            }
            Self::Ring { side, .. } => {
                if constant(0, 0.) {
                    labels.push("hole_0".into());
                }
                if constant(0, 1.) {
                    labels.push(format!("outer_{side}"));
                }
            }
        }
        if include_z {
            for (value, name) in [(0., "zmin"), (1., "zmax")] {
                if constant(2, value) {
                    labels.push(name.into());
                }
            }
        }
        if labels.is_empty() {
            None
        } else {
            Some(labels.join("&"))
        }
    }
}
#[derive(Clone)]
struct Mapping {
    footprint: Footprint,
    depth: f64,
    factor: f64,
    scale: f64,
    translation: Point,
    axis: Point,
    angle: f64,
}
impl Mapping {
    fn point(&self, p: Point) -> Point {
        let xy = self.footprint.map(p[0], p[1]);
        let mut x = [
            xy[0] * self.factor * self.scale,
            xy[1] * self.factor * self.scale,
            p[2] * self.depth * self.factor * self.scale,
        ];
        if self.angle != 0. {
            let c = self.angle.cos();
            let s = self.angle.sin();
            let a = self.axis;
            let dot = a.iter().zip(x).map(|(a, b)| a * b).sum::<f64>();
            let cross = [
                a[1] * x[2] - a[2] * x[1],
                a[2] * x[0] - a[0] * x[2],
                a[0] * x[1] - a[1] * x[0],
            ];
            x = std::array::from_fn(|i| x[i] * c + cross[i] * s + a[i] * dot * (1. - c));
        }
        std::array::from_fn(|i| x[i] + self.translation[i] * self.factor)
    }
}
struct Facet {
    corners: Vec<usize>,
    hint: Option<String>,
    count: usize,
}
struct Builder {
    points: Vec<Point>,
    origin: Option<Point>,
    precision: f64,
    nodes: HashMap<[i64; 3], usize>,
    blocks: BTreeMap<(i32, i32, usize, String), Vec<usize>>,
    edges: HashMap<(usize, usize), usize>,
    centers: HashMap<Vec<usize>, usize>,
    facets: BTreeMap<Vec<usize>, Facet>,
    order: usize,
    complete: bool,
    connectivity: usize,
}
impl Builder {
    fn new(order: usize, complete: bool, extent: f64) -> R<Self> {
        if !extent.is_finite() || extent <= 0. {
            return Err("Étendue directe invalide".into());
        }
        Ok(Self {
            points: Vec::new(),
            origin: None,
            precision: extent * 1e-12,
            nodes: HashMap::new(),
            blocks: BTreeMap::new(),
            edges: HashMap::new(),
            centers: HashMap::new(),
            facets: BTreeMap::new(),
            order,
            complete,
            connectivity: 0,
        })
    }
    fn node(&mut self, p: Point) -> R<usize> {
        if p.iter().any(|v| !v.is_finite()) {
            return Err("Coordonnée directe non finie".into());
        }
        let origin = *self.origin.get_or_insert(p);
        let key = std::array::from_fn(|i| ((p[i] - origin[i]) / self.precision).round() as i64);
        if let Some(&id) = self.nodes.get(&key) {
            return Ok(id);
        }
        if self.points.len() >= MAX_NODES {
            return Err("Limite de huit millions de nœuds directs dépassée".into());
        }
        let id = self.points.len();
        self.points.push(p);
        self.nodes.insert(key, id);
        Ok(id)
    }
    fn block(&mut self, family: Family, hint: &str, nodes: &[usize]) -> R<()> {
        let (kind, width) = family.kind(self.order, self.complete);
        if nodes.len() != width {
            return Err("Connectivité directe interne incohérente".into());
        }
        self.connectivity = self
            .connectivity
            .checked_add(width)
            .ok_or("Taille de connectivité invalide")?;
        if self.connectivity > MAX_CONNECTIVITY {
            return Err("Limite de connectivité directe dépassée".into());
        }
        self.blocks
            .entry((family.dim(), kind, width, hint.into()))
            .or_default()
            .extend_from_slice(nodes);
        Ok(())
    }
    fn cell(
        &mut self,
        family: Family,
        params: &[Point],
        mapping: &Mapping,
        hint: &str,
        include_z: bool,
    ) -> R<()> {
        if params.len() != family.corners() {
            return Err("Nombre de sommets directs invalide".into());
        }
        let primary = params
            .iter()
            .map(|p| self.node(mapping.point(*p)))
            .collect::<R<Vec<_>>>()?;
        if primary.iter().copied().collect::<BTreeSet<_>>().len() != primary.len() {
            return Err("Cellule directe dégénérée : sommets confondus".into());
        }
        let mut nodes = primary.clone();
        if self.order == 2 {
            for &(a, b) in family.edges() {
                let id = self.node(mapping.point(average(&[params[a], params[b]])))?;
                let key = edge_key(primary[a], primary[b]);
                if let Some(old) = self.edges.insert(key, id) {
                    if old != id {
                        return Err("Nœud quadratique d'arête non conforme".into());
                    }
                }
                nodes.push(id);
            }
            if self.complete {
                if family == Family::Quad {
                    let id = self.node(mapping.point(average(params)))?;
                    self.centers.insert(key_nodes(&primary), id);
                    nodes.push(id);
                } else if family.dim() == 3 {
                    for face in family.faces().iter().filter(|f| f.len() == 4) {
                        let ps = face.iter().map(|i| params[*i]).collect::<Vec<_>>();
                        let ids = face.iter().map(|i| primary[*i]).collect::<Vec<_>>();
                        let id = self.node(mapping.point(average(&ps)))?;
                        if let Some(old) = self.centers.insert(key_nodes(&ids), id) {
                            if old != id {
                                return Err("Nœud quadratique de face non conforme".into());
                            }
                        }
                        nodes.push(id);
                    }
                    if family == Family::Hex {
                        nodes.push(self.node(mapping.point(average(params)))?);
                    }
                }
            }
        }
        self.block(family, hint, &nodes)?;
        let fs: Vec<Vec<usize>> = if family.dim() == 2 {
            family.edges().iter().map(|&(a, b)| vec![a, b]).collect()
        } else {
            family.faces().iter().map(|v| v.to_vec()).collect()
        };
        for indices in fs {
            let corners = indices.iter().map(|i| primary[*i]).collect::<Vec<_>>();
            let ps = indices.iter().map(|i| params[*i]).collect::<Vec<_>>();
            let key = key_nodes(&corners);
            let hint = mapping.footprint.hint(&ps, include_z);
            self.facets
                .entry(key)
                .and_modify(|f| {
                    f.count += 1;
                })
                .or_insert(Facet {
                    corners,
                    hint,
                    count: 1,
                });
        }
        Ok(())
    }
    fn boundary_nodes(&self, family: Family, corners: &[usize]) -> R<Vec<usize>> {
        let mut nodes = corners.to_vec();
        if self.order == 2 {
            for &(a, b) in family.edges() {
                nodes.push(
                    *self
                        .edges
                        .get(&edge_key(corners[a], corners[b]))
                        .ok_or("Nœud d'arête frontière manquant")?,
                );
            }
            if self.complete && family == Family::Quad {
                nodes.push(
                    *self
                        .centers
                        .get(&key_nodes(corners))
                        .ok_or("Centre de face frontière manquant")?,
                );
            }
        }
        Ok(nodes)
    }
    fn boundary(&mut self, dimension: i32) -> R<()> {
        let facets = std::mem::take(&mut self.facets);
        let mut curves: BTreeMap<Vec<usize>, (Vec<usize>, BTreeSet<String>)> = BTreeMap::new();
        for (_, facet) in facets {
            if facet.count > 2 {
                return Err("Connectivité directe non-manifold".into());
            }
            if facet.count != 1
                && !(dimension == 2 && facet.hint.as_ref().is_some_and(|s| s.contains('&')))
            {
                continue;
            }
            let hint = facet
                .hint
                .ok_or("Frontière directe sans correspondance géométrique")?;
            let family = match facet.corners.len() {
                2 => Family::Line,
                3 => Family::Triangle,
                4 => Family::Quad,
                _ => return Err("Facette directe inconnue".into()),
            };
            let nodes = self.boundary_nodes(family, &facet.corners)?;
            self.block(family, &hint, &nodes)?;
            if dimension == 3 {
                for &(a, b) in family.edges() {
                    let ids = vec![facet.corners[a], facet.corners[b]];
                    let entry = curves
                        .entry(key_nodes(&ids))
                        .or_insert_with(|| (ids, BTreeSet::new()));
                    entry.1.insert(hint.clone());
                }
            }
        }
        if dimension == 3 {
            for (_, (corners, hints)) in curves {
                if hints.len() < 2 {
                    continue;
                }
                let hint = hints.into_iter().collect::<Vec<_>>().join("|");
                let nodes = self.boundary_nodes(Family::Line, &corners)?;
                self.block(Family::Line, &hint, &nodes)?;
            }
        }
        Ok(())
    }
}
fn transform(project: &Value) -> R<(f64, Point, Point, f64)> {
    let t = &project["geometry"]["transform"];
    let scale = positive(&t["scale"], "Échelle", 1.)?;
    let read = |v: &Value, default: Point, label: &str| -> R<Point> {
        if v.is_null() {
            return Ok(default);
        }
        let a = v
            .as_array()
            .filter(|a| a.len() == 3)
            .ok_or_else(|| format!("{label} attend trois coordonnées"))?;
        Ok([
            scalar(&a[0], label, 0.)?,
            scalar(&a[1], label, 0.)?,
            scalar(&a[2], label, 0.)?,
        ])
    };
    let delta = read(&t["translation"], [0.; 3], "Translation")?;
    let mut axis = read(&t["rotation_axis"], [0., 0., 1.], "Axe")?;
    let norm = axis.iter().map(|x| x * x).sum::<f64>().sqrt();
    let angle = scalar(&t["rotation_degrees"], "Rotation", 0.)?.to_radians();
    if norm == 0. || !norm.is_finite() {
        return Err("Axe de rotation invalide".into());
    }
    for x in &mut axis {
        *x /= norm;
    }
    Ok((scale, delta, axis, angle))
}
fn footprints(project: &Value) -> R<(Vec<Footprint>, f64, f64, f64, bool)> {
    let g = &project["geometry"];
    let kind = g["kind"].as_str().ok_or("Géométrie absente")?;
    let volume = project["mode"] == "volume";
    let closed_surface = kind == "box" && !volume;
    let (q,depth)=match kind {
        "box"|"plate"=>{let w=positive(&g["width"],"Largeur",10.)?;let h=positive(&g["height"],"Hauteur",6.)?;if kind=="plate"&&(scalar(&g["corner_radius"],"Congé",0.)?!=0.||scalar(&g["chamfer"],"Chanfrein",0.)?!=0.){return Err("Le générateur direct actuel demande une plaque sans congé ni chanfrein ; fournir un découpage compatible".into());}let d=if kind=="box"{positive(&g["depth"],"Profondeur",4.)?}else{positive(&g["thickness"],"Épaisseur",2.)?};([[0.,0.],[w,0.],[w,h],[0.,h]],d)},
        "polygon"=>{let rings=g["holes"].as_array();if rings.is_some_and(|a|!a.is_empty()){return Err("Les polygones percés généraux demandent un découpage en blocs".into());}let a=g["outer"].as_array().filter(|a|a.len()==4).ok_or("Le générateur direct actuel demande un polygone convexe à quatre sommets")?;let mut q=[[0.;2];4];for (i,p) in a.iter().enumerate(){let p=p.as_array().filter(|p|p.len()==2).ok_or("Sommet XY invalide")?;q[i]=[scalar(&p[0],"X",0.)?,scalar(&p[1],"Y",0.)?];}let area=(0..4).map(|i|q[i][0]*q[(i+1)%4][1]-q[(i+1)%4][0]*q[i][1]).sum::<f64>();if area<0.{q=[q[0],q[3],q[2],q[1]];}for i in 0..4{let a=q[i];let b=q[(i+1)%4];let c=q[(i+2)%4];if (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0])<=0.{return Err("Le bloc quadrilatère doit être strictement convexe et non croisé".into());}}(q,positive(&g["thickness"],"Épaisseur",2.)?)},
        _=>return Err(format!("Génération directe structurée non disponible pour {kind} : fournir une plaque, une boîte ou un quadrilatère convexe compatible")),
    };
    let length = |a: [f64; 2], b: [f64; 2]| ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2)).sqrt();
    let u = length(q[0], q[1]).max(length(q[2], q[3]));
    let v = length(q[0], q[3]).max(length(q[1], q[2]));
    let holes = if kind == "plate" {
        g["holes"].as_array().map(Vec::as_slice).unwrap_or(&[])
    } else {
        &[]
    };
    if holes.is_empty() {
        return Ok((vec![Footprint::Quad(q)], u, v, depth, closed_surface));
    }
    if holes.len() != 1 {
        return Err("Le générateur direct O-grid actuel accepte exactement un trou circulaire ; plusieurs trous demandent un découpage multi-blocs explicite".into());
    }
    let hole = &holes[0];
    let center = [
        scalar(&hole["x"], "Centre X", 0.)?,
        scalar(&hole["y"], "Centre Y", 0.)?,
    ];
    let radius = positive(&hole["radius"], "Rayon", 1.)?;
    if center[0] - radius <= 0.
        || center[1] - radius <= 0.
        || center[0] + radius >= q[2][0]
        || center[1] + radius >= q[2][1]
    {
        return Err("Le trou doit être strictement intérieur à la plaque".into());
    }
    let mut theta = q
        .iter()
        .map(|p| (p[1] - center[1]).atan2(p[0] - center[0]))
        .collect::<Vec<_>>();
    for i in 1..4 {
        while theta[i] <= theta[i - 1] {
            theta[i] += std::f64::consts::TAU;
        }
    }
    theta.push(theta[0] + std::f64::consts::TAU);
    let patches = (0..4)
        .map(|i| Footprint::Ring {
            center,
            radius,
            a: q[i],
            b: q[(i + 1) % 4],
            theta: theta[i],
            sweep: theta[i + 1] - theta[i],
            side: i,
        })
        .collect();
    let radial = q
        .iter()
        .map(|p| length(*p, center) - radius)
        .fold(0., f64::max);
    Ok((patches, radial, u.max(v), depth, false))
}

/// Build a direct structured grid on supported parametric CAD primitives.
/// Requested unsupported geometry/family/controls are errors, never fallbacks.
pub fn generate(project: &Value, factor: f64) -> R<DirectMesh> {
    if !factor.is_finite() || factor <= 0. {
        return Err("Facteur d'unité invalide".into());
    }
    let mesh = &project["mesh"];
    let mode = project["mode"].as_str().unwrap_or("planar");
    let dimension = if mode == "volume" {
        3
    } else if ["planar", "surface"].contains(&mode) {
        2
    } else {
        return Err("Mode direct inconnu".into());
    };
    if mesh["recombine"] == true {
        return Err("Le générateur direct interdit la recombinaison".into());
    }
    let family = match crate::backend::requested_shape(project)? {
        "quadrilateral" => Family::Quad,
        "hexahedron" => Family::Hex,
        "prism" => Family::Prism,
        "pyramid" => Family::Pyramid,
        "triangle" => Family::Triangle,
        "tetrahedron" => Family::Tet,
        _ => {
            return Err(
                "Une famille d'éléments explicite est requise pour le générateur direct".into(),
            )
        }
    };
    if family.dim() != dimension {
        return Err("La famille demandée ne correspond pas à la dimension du domaine".into());
    }
    let order = if mesh["order"].is_null() {
        2
    } else {
        mesh["order"]
            .as_u64()
            .ok_or("L'ordre direct doit être un entier 1 ou 2")? as usize
    };
    if ![1, 2].contains(&order) {
        return Err("Le générateur direct accepte les ordres 1 et 2".into());
    }
    let layout = if mesh["quadratic_layout"].is_null() {
        "serendipity"
    } else {
        mesh["quadratic_layout"]
            .as_str()
            .ok_or("quadratic_layout doit être une chaîne")?
    };
    let complete = match layout {
        "serendipity" => false,
        "complete" => true,
        _ => return Err("quadratic_layout doit être serendipity ou complete".into()),
    };
    if !mesh["structured"].is_null() && !mesh["structured"].is_object() {
        return Err("structured doit être un objet".into());
    }
    // A tensor-product grid cannot silently ignore an adaptive field. CAD
    // entity selection is applied by the backend after exact classification
    // of these logical blocks, before any nodes or elements are installed.
    for key in ["local_sizes", "refinement_regions", "correspondences"] {
        if project[key].as_array().is_some_and(|a| !a.is_empty()) {
            return Err(format!("{key} n'est pas encore pris en charge par les blocs directs ; utiliser divisions/grading ou définir un découpage compatible"));
        }
    }
    if project["repair"]["enabled"] == true {
        return Err("Une réparation CAO peut modifier la topologie ; désactiver la réparation pour ce bloc paramétrique direct".into());
    }
    let (patches, lu, lv, depth, closed_surface) = footprints(project)?;
    if closed_surface && family != Family::Quad && family != Family::Triangle {
        return Err("La surface fermée de boîte exige triangles ou quadrilatères".into());
    }
    let (scale, translation, axis, angle) = transform(project)?;
    let size = positive(&mesh["size"], "Taille cible", 0.5)?;
    let min_size = positive(&mesh["min_size"], "Taille minimale", 0.1)?;
    if min_size > size {
        return Err("Taille minimale supérieure à la taille cible".into());
    }
    let defaults = [
        (lu * scale / size).ceil().max(1.),
        (lv * scale / size).ceil().max(1.),
        if dimension == 3 || closed_surface {
            (depth * scale / size).ceil().max(1.)
        } else {
            1.
        },
    ];
    let divisions = if mesh["structured"]["divisions"].is_null() {
        defaults.map(|n| n as usize)
    } else {
        let a = mesh["structured"]["divisions"]
            .as_array()
            .filter(|a| a.len() == 3)
            .ok_or("structured.divisions attend [nx,ny,nz]")?;
        let mut d = [0; 3];
        for i in 0..3 {
            d[i] = a[i]
                .as_u64()
                .filter(|n| *n >= 1 && *n <= 2000)
                .ok_or("Chaque division doit être un entier de 1 à 2000")?
                as usize;
        }
        d
    };
    if divisions.iter().any(|n| *n == 0 || *n > 2000) {
        return Err("L'estimation de divisions dépasse la limite de 2000 par direction".into());
    }
    if dimension == 2 && !closed_surface && divisions[2] != 1 {
        return Err("Une surface ouverte demande nz=1".into());
    }
    let mut ratios = [1.; 3];
    if !mesh["structured"]["grading"].is_null() {
        let a = mesh["structured"]["grading"]
            .as_array()
            .filter(|a| a.len() == 3)
            .ok_or("structured.grading attend [rx,ry,rz]")?;
        for i in 0..3 {
            ratios[i] = positive(&a[i], "Progression", 1.)?;
        }
    }
    let [nx, ny, nz] = divisions;
    let base = if closed_surface {
        2usize
            .checked_mul(
                nx.checked_mul(ny)
                    .and_then(|a| a.checked_add(nx.checked_mul(nz)?))
                    .and_then(|a| a.checked_add(ny.checked_mul(nz)?))
                    .ok_or("Trop de cellules")?,
            )
            .ok_or("Trop de cellules")?
    } else {
        patches
            .len()
            .checked_mul(nx)
            .and_then(|n| n.checked_mul(ny))
            .and_then(|n| n.checked_mul(if dimension == 3 { nz } else { 1 }))
            .ok_or("Trop de cellules")?
    };
    let multiplier = match family {
        Family::Triangle | Family::Prism => 2,
        Family::Tet | Family::Pyramid => 6,
        _ => 1,
    };
    let count = base.checked_mul(multiplier).ok_or("Trop de cellules")?;
    let maximum = mesh["max_elements"]
        .as_u64()
        .unwrap_or(200000)
        .min(2_000_000) as usize;
    if count > maximum {
        return Err(format!(
            "Le maillage direct demanderait {count} éléments, au-delà de max_elements={maximum}"
        ));
    }
    if count
        .checked_mul(family.kind(order, complete).1)
        .is_none_or(|n| n > MAX_CONNECTIVITY)
    {
        return Err("Le maillage direct dépasse la limite de connectivité avant allocation".into());
    }
    // Conservative bound on unique nodes, checked before building the grid.
    // A quadratic pyramid additionally has eight corner-to-apex midpoints
    // inside each logical voxel. These are not tensor-grid nodes.
    let node_axes = divisions.map(|n| order * n + 1);
    let surface_bound = || {
        node_axes[0]
            .checked_mul(node_axes[1])
            .and_then(|a| a.checked_add(node_axes[0].checked_mul(node_axes[2])?))
            .and_then(|a| a.checked_add(node_axes[1].checked_mul(node_axes[2])?))
            .and_then(|a| a.checked_mul(2))
    };
    let tensor_bound = if closed_surface {
        surface_bound()
    } else {
        node_axes[0]
            .checked_mul(node_axes[1])
            .and_then(|n| n.checked_mul(if dimension == 3 { node_axes[2] } else { 1 }))
            .and_then(|n| n.checked_mul(patches.len()))
    };
    let node_bound = tensor_bound.and_then(|n| {
        if family == Family::Pyramid {
            n.checked_add(base.checked_mul(if order == 2 { 8 } else { 1 })?)
        } else {
            Some(n)
        }
    });
    if node_bound.is_none_or(|n| n > MAX_NODES) {
        return Err("L'estimation préalable dépasse huit millions de nœuds directs".into());
    }
    let axes = [
        coordinates(nx, ratios[0])?,
        coordinates(ny, ratios[1])?,
        coordinates(nz, ratios[2])?,
    ];
    let mut builder = Builder::new(order, complete, lu.max(lv).max(depth) * factor * scale)?;
    for footprint in &patches {
        let mapping = Mapping {
            footprint: footprint.clone(),
            depth,
            factor,
            scale,
            translation,
            axis,
            angle,
        };
        if closed_surface {
            for face in 0..6 {
                let (na, nb) = match face {
                    0 | 1 => (ny, nz),
                    2 | 3 => (nx, nz),
                    _ => (nx, ny),
                };
                for j in 0..nb {
                    for i in 0..na {
                        let ps = match face {
                            0 => vec![
                                [0., axes[1][i], axes[2][j]],
                                [0., axes[1][i], axes[2][j + 1]],
                                [0., axes[1][i + 1], axes[2][j + 1]],
                                [0., axes[1][i + 1], axes[2][j]],
                            ],
                            1 => vec![
                                [1., axes[1][i], axes[2][j]],
                                [1., axes[1][i + 1], axes[2][j]],
                                [1., axes[1][i + 1], axes[2][j + 1]],
                                [1., axes[1][i], axes[2][j + 1]],
                            ],
                            2 => vec![
                                [axes[0][i], 0., axes[2][j]],
                                [axes[0][i + 1], 0., axes[2][j]],
                                [axes[0][i + 1], 0., axes[2][j + 1]],
                                [axes[0][i], 0., axes[2][j + 1]],
                            ],
                            3 => vec![
                                [axes[0][i], 1., axes[2][j]],
                                [axes[0][i], 1., axes[2][j + 1]],
                                [axes[0][i + 1], 1., axes[2][j + 1]],
                                [axes[0][i + 1], 1., axes[2][j]],
                            ],
                            4 => vec![
                                [axes[0][i], axes[1][j], 0.],
                                [axes[0][i], axes[1][j + 1], 0.],
                                [axes[0][i + 1], axes[1][j + 1], 0.],
                                [axes[0][i + 1], axes[1][j], 0.],
                            ],
                            _ => vec![
                                [axes[0][i], axes[1][j], 1.],
                                [axes[0][i + 1], axes[1][j], 1.],
                                [axes[0][i + 1], axes[1][j + 1], 1.],
                                [axes[0][i], axes[1][j + 1], 1.],
                            ],
                        };
                        let hint = ["umin", "umax", "vmin", "vmax", "zmin", "zmax"][face];
                        emit_surface(&mut builder, family, &ps, &mapping, hint, true)?;
                    }
                }
            }
        } else {
            for k in 0..if dimension == 3 { nz } else { 1 } {
                for j in 0..ny {
                    for i in 0..nx {
                        let [u0, u1] = [axes[0][i], axes[0][i + 1]];
                        let [v0, v1] = [axes[1][j], axes[1][j + 1]];
                        let [z0, z1] = if dimension == 3 {
                            [axes[2][k], axes[2][k + 1]]
                        } else {
                            [0., 0.]
                        };
                        let p = [
                            [u0, v0, z0],
                            [u1, v0, z0],
                            [u1, v1, z0],
                            [u0, v1, z0],
                            [u0, v0, z1],
                            [u1, v0, z1],
                            [u1, v1, z1],
                            [u0, v1, z1],
                        ];
                        match family {
                            Family::Quad | Family::Triangle => emit_surface(
                                &mut builder,
                                family,
                                &p[..4],
                                &mapping,
                                "domain",
                                false,
                            )?,
                            Family::Hex => builder.cell(family, &p, &mapping, "domain", true)?,
                            Family::Prism => {
                                for ids in [[0, 1, 2, 4, 5, 6], [0, 2, 3, 4, 6, 7]] {
                                    let ps = ids.map(|i| p[i]);
                                    builder.cell(family, &ps, &mapping, "domain", true)?;
                                }
                            }
                            Family::Pyramid => {
                                let center = average(&p);
                                for face in Family::Hex.faces() {
                                    let ps =
                                        [p[face[0]], p[face[3]], p[face[2]], p[face[1]], center];
                                    builder.cell(family, &ps, &mapping, "domain", true)?;
                                }
                            }
                            Family::Tet => {
                                for ids in [
                                    [0, 1, 2, 6],
                                    [0, 2, 3, 6],
                                    [0, 3, 7, 6],
                                    [0, 7, 4, 6],
                                    [0, 4, 5, 6],
                                    [0, 5, 1, 6],
                                ] {
                                    let ps = ids.map(|i| p[i]);
                                    builder.cell(family, &ps, &mapping, "domain", true)?;
                                }
                            }
                            Family::Line => unreachable!(),
                        }
                    }
                }
            }
        }
    }
    builder.boundary(dimension)?;
    let blocks = builder
        .blocks
        .into_iter()
        .map(
            |((dimension, element_type, nodes_per_element, entity_hint), connectivity)| {
                DirectBlock {
                    dimension,
                    element_type,
                    nodes_per_element,
                    connectivity,
                    entity_hint,
                }
            },
        )
        .collect::<Vec<_>>();
    let actual = blocks
        .iter()
        .filter(|b| b.dimension == dimension)
        .map(|b| b.connectivity.len() / b.nodes_per_element)
        .sum::<usize>();
    if actual != count {
        return Err("Nombre d'éléments directs incohérent".into());
    }
    Ok(DirectMesh {
        points: builder.points,
        blocks,
        metadata: json!({"generator":"rust_direct_structured_v1","element_shape":family.name(),"order":order,"quadratic_layout":if complete{"complete"}else{"serendipity"},"divisions":divisions,"grading":ratios,"blocks":patches.len(),"top_elements":actual,"recombination_used":false,"order_elevation_used":false,"intermediate_mesh_generated":false,"construction":"requested-family connectivity and order-two nodes emitted directly from logical coordinates","scope":"parametric blocks; no arbitrary-CAD automatic block decomposition","spatial_size_fields_supported":false,"local_strict_remesh_supported":false}),
    })
}
fn emit_surface(
    builder: &mut Builder,
    family: Family,
    p: &[Point],
    mapping: &Mapping,
    hint: &str,
    z: bool,
) -> R<()> {
    if family == Family::Triangle {
        for ids in [[0, 1, 2], [0, 2, 3]] {
            let ps = ids.map(|i| p[i]);
            builder.cell(family, &ps, mapping, hint, z)?;
        }
    } else {
        builder.cell(family, p, mapping, hint, z)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn p(shape: &str, mode: &str, order: usize, layout: &str) -> Value {
        json!({"mode":mode,"geometry":if mode=="volume"{json!({"kind":"box","width":2.,"height":2.,"depth":2.})}else{json!({"kind":"plate","width":2.,"height":2.,"holes":[]})},"mesh":{"element_shape":shape,"order":order,"quadratic_layout":layout,"size":1.,"min_size":0.1,"max_elements":200000,"structured":{"divisions":[2,2,if mode=="volume"{2}else{1}]}}})
    }
    fn top(m: &DirectMesh, d: i32) -> Vec<&DirectBlock> {
        m.blocks.iter().filter(|b| b.dimension == d).collect()
    }
    #[test]
    fn direct_quad_counts_and_shared_order_two_nodes() {
        let m = generate(&p("quadrilateral", "planar", 2, "serendipity"), 1.).unwrap();
        assert_eq!(m.points.len(), 21);
        assert_eq!(top(&m, 2)[0].element_type, 16);
        assert_eq!(top(&m, 2)[0].connectivity.len(), 4 * 8);
        assert_eq!(m.metadata["recombination_used"], false);
        assert_eq!(m.metadata["order_elevation_used"], false);
        let complete = generate(&p("quadrilateral", "planar", 2, "complete"), 1.).unwrap();
        assert_eq!(complete.points.len(), 25);
        assert_eq!(top(&complete, 2)[0].element_type, 10);
    }
    #[test]
    fn all_solid_families_direct_both_orders_and_layouts() {
        for (shape, linear, serendipity, complete, multiplier) in [
            ("hexahedron", 5, 17, 12, 1),
            ("prism", 6, 18, 13, 2),
            ("pyramid", 7, 19, 14, 6),
            ("tetrahedron", 4, 11, 11, 6),
        ] {
            for (order, layout, kind) in [
                (1, "serendipity", linear),
                (2, "serendipity", serendipity),
                (2, "complete", complete),
            ] {
                let m = generate(&p(shape, "volume", order, layout), 1.).unwrap();
                let bs = top(&m, 3);
                assert_eq!(bs.len(), 1);
                assert_eq!(bs[0].element_type, kind);
                assert_eq!(
                    bs[0].connectivity.len() / bs[0].nodes_per_element,
                    8 * multiplier
                );
                assert!(m.blocks.iter().any(|b| b.dimension == 2));
                assert!(m.blocks.iter().any(|b| b.dimension == 1));
                for b in &m.blocks {
                    assert!(b.connectivity.iter().all(|n| *n < m.points.len()));
                }
            }
        }
    }
    #[test]
    fn hex27_has_tensor_product_node_count() {
        let m = generate(&p("hexahedron", "volume", 2, "complete"), 1.).unwrap();
        assert_eq!(m.points.len(), 125);
    }
    #[test]
    fn hole_nodes_follow_exact_circle_including_quadratic_midpoints() {
        let mut a = p("quadrilateral", "planar", 2, "serendipity");
        a["geometry"] =
            json!({"kind":"plate","width":6.,"height":4.,"holes":[{"x":2.5,"y":2.,"radius":0.5}]});
        a["mesh"]["structured"]["divisions"] = json!([2, 3, 1]);
        let m = generate(&a, 1.).unwrap();
        assert_eq!(top(&m, 2)[0].connectivity.len() / 8, 24);
        let hole = m.blocks.iter().find(|b| b.entity_hint == "hole_0").unwrap();
        assert_eq!(hole.element_type, 8);
        for &i in &hole.connectivity {
            let p = m.points[i];
            assert!(((p[0] - 2.5).hypot(p[1] - 2.) - 0.5).abs() < 1e-12);
        }
    }
    #[test]
    fn extruded_hole_has_no_internal_patch_boundaries() {
        let mut a = p("hexahedron", "volume", 2, "complete");
        a["geometry"] = json!({"kind":"plate","width":6.,"height":4.,"thickness":1.,"holes":[{"x":3.,"y":2.,"radius":0.5}]});
        let m = generate(&a, 1.).unwrap();
        let faces = m
            .blocks
            .iter()
            .filter(|b| b.dimension == 2)
            .collect::<Vec<_>>();
        assert_eq!(faces.len(), 7);
        assert!(faces.iter().all(|b| b.element_type == 10));
        assert_eq!(m.metadata["top_elements"], 32);
    }
    #[test]
    fn grading_is_positive_and_monotone() {
        let x = coordinates(8, 1.2).unwrap();
        assert_eq!(x[0], 0.);
        assert_eq!(x[8], 1.);
        let widths = x.windows(2).map(|a| a[1] - a[0]).collect::<Vec<_>>();
        for p in widths.windows(2) {
            assert!((p[1] / p[0] - 1.2).abs() < 1e-12);
        }
        assert!(coordinates(2000, 10.).is_err());
        assert!(coordinates(0, 1.).is_err());
    }
    #[test]
    fn unsupported_controls_and_geometry_never_fall_back() {
        let a = p("quadrilateral", "planar", 2, "complete");
        for key in ["refinement_regions", "local_sizes", "correspondences"] {
            let mut q = a.clone();
            q[key] = json!([{}]);
            assert!(generate(&q, 1.).is_err());
        }
        let mut q = a.clone();
        q["mesh"]["recombine"] = json!(true);
        assert!(generate(&q, 1.).is_err());
        q = a.clone();
        q["geometry"]["corner_radius"] = json!(0.2);
        assert!(generate(&q, 1.).is_err());
        q = a.clone();
        q["mesh"]["structured"]["divisions"] = json!([2000, 2000, 1]);
        assert!(generate(&q, 1.).is_err());
    }
    #[test]
    fn transform_and_si_units_are_applied_once() {
        let mut a = p("quadrilateral", "planar", 1, "complete");
        a["geometry"]["transform"] =
            json!({"scale":2.,"rotation_degrees":90.,"translation":[10.,20.,30.]});
        let m = generate(&a, 0.001).unwrap();
        let p = m.points[0];
        assert!((p[0] - 0.01).abs() < 1e-15);
        assert!((p[1] - 0.02).abs() < 1e-15);
        assert!((p[2] - 0.03).abs() < 1e-15);
    }
    #[test]
    fn box_surface_keeps_cad_edges_without_interior_grid_edges() {
        let mut a = p("quadrilateral", "surface", 2, "complete");
        a["geometry"] = json!({"kind":"box","width":2.,"height":2.,"depth":2.});
        a["mesh"]["structured"]["divisions"] = json!([2, 2, 2]);
        let m = generate(&a, 1.).unwrap();
        assert_eq!(
            top(&m, 2)
                .iter()
                .map(|b| b.connectivity.len() / b.nodes_per_element)
                .sum::<usize>(),
            24
        );
        let curves = m
            .blocks
            .iter()
            .filter(|b| b.dimension == 1)
            .collect::<Vec<_>>();
        assert_eq!(curves.len(), 12);
        assert_eq!(
            curves
                .iter()
                .map(|b| b.connectivity.len() / b.nodes_per_element)
                .sum::<usize>(),
            24
        );
    }
    #[test]
    fn quadratic_node_budget_is_checked_before_grid_allocation() {
        let mut a = p("hexahedron", "volume", 2, "complete");
        a["mesh"]["max_elements"] = json!(2_000_000);
        a["mesh"]["structured"]["divisions"] = json!([120, 120, 120]);
        let error = generate(&a, 1.).unwrap_err();
        assert!(error.contains("nœuds"));
        a["mesh"]["order"] = json!("2");
        assert!(generate(&a, 1.).unwrap_err().contains("entier"));
    }
}
