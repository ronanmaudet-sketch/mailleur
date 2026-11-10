//! Reconstruct a discrete skin from typed mesh-file data, never a CAO model.
//!
//! File parsers supply points, connectivity and affine transforms. Extraction,
//! topology, orientation and triangulation execute here. Quadratic cells are
//! deliberately approximated by their primary corners for this reconstruction;
//! the original mesh is not modified or presented as a lossless conversion.

use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

type R<T> = Result<T, String>;
type Point = [f64; 3];
const SCHEMA: &str = "atelier.mesh_import.v1";
const PREPARATION_VERSION: &str = "primary_corner_skin_v1";
const MAX_INPUT_BYTES: u64 = 200 * 1024 * 1024;
const MAX_NODES: usize = 2_000_000;
const MAX_CELLS: usize = 2_000_000;
const MAX_FACES: usize = 4_000_000;
const MAX_TRIANGLES: usize = 4_000_000;

fn one() -> f64 { 1. }

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct Part {
    #[serde(default)]
    points: Vec<Point>,
    #[serde(default)]
    cells: Vec<CellBlock>,
    /// Row-major affine matrices, applied local-to-parent in list order.
    #[serde(default)]
    transforms: Vec<[f64; 16]>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    schema: String,
    #[serde(default)]
    points: Vec<Point>,
    #[serde(default)]
    cells: Vec<CellBlock>,
    #[serde(default)]
    objects: Vec<Part>,
    #[serde(default = "one")]
    coordinate_scale: f64,
    /// 3MF positive-fill unions are not reconstructed: conservatively reject
    /// touching/overlapping object boxes instead of silently treating a union
    /// as independent surfaces. Boxes may produce deliberate false positives.
    #[serde(default)]
    reject_overlapping_objects: bool,
    #[serde(default)]
    metadata: Value,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CellBlock {
    #[serde(rename = "type")]
    kind: String,
    data: Vec<Vec<usize>>,
}

#[derive(Clone, Copy)]
struct Shape {
    dimension: usize,
    nodes: usize,
    corners: usize,
    faces: &'static [&'static [usize]],
}

const TETRA: &[&[usize]] = &[&[0,2,1], &[0,1,3], &[1,2,3], &[2,0,3]];
const HEXA: &[&[usize]] = &[&[0,3,2,1], &[4,5,6,7], &[0,1,5,4], &[1,2,6,5], &[2,3,7,6], &[3,0,4,7]];
const WEDGE: &[&[usize]] = &[&[0,2,1], &[3,4,5], &[0,1,4,3], &[1,2,5,4], &[2,0,3,5]];
const PYRAMID: &[&[usize]] = &[&[0,3,2,1], &[0,1,4], &[1,2,4], &[2,3,4], &[3,0,4]];

fn shape(kind: &str) -> R<Shape> {
    let (dimension, nodes, corners, faces): (_, _, _, &[&[usize]]) = match kind {
        "vertex" => (0,1,1,&[]),
        "line" => (1,2,2,&[]),
        "line3" => (1,3,2,&[]),
        "triangle" => (2,3,3,&[]),
        "triangle6" => (2,6,3,&[]),
        "quad" => (2,4,4,&[]),
        "quad8" => (2,8,4,&[]),
        "quad9" => (2,9,4,&[]),
        "tetra" => (3,4,4,TETRA),
        "tetra10" => (3,10,4,TETRA),
        "hexahedron" => (3,8,8,HEXA),
        "hexahedron20" => (3,20,8,HEXA),
        "hexahedron27" => (3,27,8,HEXA),
        "wedge" => (3,6,6,WEDGE),
        "wedge15" => (3,15,6,WEDGE),
        "wedge18" => (3,18,6,WEDGE),
        "pyramid" => (3,5,5,PYRAMID),
        "pyramid13" => (3,13,5,PYRAMID),
        "pyramid14" => (3,14,5,PYRAMID),
        _ => return Err(format!("Topologie d'import non prise en charge : {kind}")),
    };
    Ok(Shape {dimension, nodes, corners, faces})
}

fn subtract(a: Point, b: Point) -> Point { std::array::from_fn(|i| a[i]-b[i]) }
fn cross(a: Point, b: Point) -> Point {
    [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
}
fn dot(a: Point, b: Point) -> f64 { a.iter().zip(b).map(|(a,b)|a*b).sum() }

/// Return the handedness of a finite, nonsingular affine transform.
fn transform_sign(matrix: &[f64;16]) -> R<f64> {
    if matrix.iter().any(|x| !x.is_finite()) || matrix[12..] != [0.,0.,0.,1.] {
        return Err("La transformation d'import doit être affine 4×4 et finie".into());
    }
    let scale = matrix[..12].iter().enumerate().filter(|(i,_)|i%4!=3)
        .map(|(_,v)|v.abs()).fold(0.,f64::max);
    if scale == 0. { return Err("Transformation d'import singulière".into()); }
    let a = [matrix[0]/scale,matrix[1]/scale,matrix[2]/scale];
    let b = [matrix[4]/scale,matrix[5]/scale,matrix[6]/scale];
    let c = [matrix[8]/scale,matrix[9]/scale,matrix[10]/scale];
    let determinant = dot(a,cross(b,c));
    if !determinant.is_finite() || determinant.abs() <= 64.*f64::EPSILON {
        return Err("Transformation d'import singulière ou numériquement non résolue".into());
    }
    Ok(determinant.signum())
}

fn transform(mut point: Point, matrices: &[[f64;16]], scale: f64) -> R<Point> {
    for matrix in matrices {
        point = std::array::from_fn(|i| {
            matrix[4*i]*point[0]+matrix[4*i+1]*point[1]+matrix[4*i+2]*point[2]+matrix[4*i+3]
        });
    }
    for x in &mut point { *x *= scale; }
    if point.iter().any(|x|!x.is_finite()) {
        return Err("Coordonnées d'import non finies ou hors plage après transformation".into());
    }
    Ok(point)
}

#[derive(Clone, Copy, Hash, Eq, PartialEq)]
struct FaceKey { count: usize, nodes: [usize;4] }

fn face_key(nodes: &[usize]) -> FaceKey {
    let mut key = [0;4];
    key[..nodes.len()].copy_from_slice(nodes);
    key[..nodes.len()].sort_unstable();
    FaceKey {count:nodes.len(), nodes:key}
}

struct Face { nodes: Vec<usize>, count: usize }
struct Cell { nodes: Vec<usize>, shape: Shape, orientation: f64 }

struct Prepared {
    points: Vec<Point>,
    triangles: Vec<[usize;3]>,
    report: Value,
}

/// Coordinates normalized within one cell avoid scale-dependent tolerances.
fn local_points(points: &[Point], nodes: &[usize]) -> R<Vec<Point>> {
    let origin = points[nodes[0]];
    let offsets: Vec<_> = nodes.iter().map(|i|subtract(points[*i],origin)).collect();
    let size = offsets.iter().flatten().map(|x|x.abs()).fold(0.,f64::max);
    if !size.is_finite() || size == 0. {
        return Err("Élément primaire d'import dégénéré ou hors plage".into());
    }
    let result: Vec<Point> = offsets.iter().map(|p|std::array::from_fn(|i|p[i]/size)).collect();
    if result.iter().flatten().any(|x|!x.is_finite()) {
        return Err("Élément primaire d'import hors plage numérique".into());
    }
    Ok(result)
}

fn triangle_normal(points: &[Point], triangle: &[usize;3]) -> R<Point> {
    let p = local_points(points,triangle)?;
    let normal = cross(subtract(p[1],p[0]),subtract(p[2],p[0]));
    let norm = dot(normal,normal).sqrt();
    if !norm.is_finite() || norm <= 64.*f64::EPSILON {
        return Err("Triangle de reconstruction dégénéré ou numériquement non résolu".into());
    }
    Ok(normal.map(|x|x/norm))
}

fn triangulate(nodes: &[usize], points: &[Point], triangles: &mut Vec<[usize;3]>) -> R<()> {
    let a = [nodes[0],nodes[1],nodes[2]];
    let normal=triangle_normal(points,&a)?;
    if nodes.len()==4 {
        let b = [nodes[0],nodes[2],nodes[3]];
        let other=triangle_normal(points,&b)?;
        if dot(normal,other)<=64.*f64::EPSILON {
            return Err("Quadrilatère primaire croisé ou fortement replié ; triangulation de reconstruction refusée".into());
        }
        triangles.push(a);
        triangles.push(b);
    } else {triangles.push(a);}
    if triangles.len()>MAX_TRIANGLES { return Err("Limite de triangles de reconstruction dépassée".into()); }
    Ok(())
}

fn opposite(a: &[usize], b: &[usize]) -> bool {
    (0..b.len()).any(|start|(0..a.len()).all(|i|a[i]==b[(start+b.len()-i)%b.len()]))
}

fn volume_faces(cell: &Cell, points: &[Point]) -> R<Vec<Vec<usize>>> {
    let local = local_points(points,&cell.nodes)?;
    let center: Point = std::array::from_fn(|i|local.iter().map(|p|p[i]/local.len() as f64).sum());
    let mut result = Vec::new();
    for indices in cell.shape.faces {
        let mut face: Vec<_> = indices.iter().map(|i|cell.nodes[*i]).collect();
        // Both halves of a quad must point away from the primary cell center.
        // This conservative primary-polyhedron check is not a curved Jacobian.
        for i in 1..indices.len()-1 {
            let a=local[indices[0]]; let b=local[indices[i]]; let c=local[indices[i+1]];
            let normal=cross(subtract(b,a),subtract(c,a));
            let outward=dot(normal,subtract(a,center))*cell.orientation;
            if !outward.is_finite() || outward<=64.*f64::EPSILON {
                return Err("Cellule volumique primaire inversée, non convexe ou dégénérée ; reconstruction refusée".into());
            }
        }
        if cell.orientation<0. { face.reverse(); }
        result.push(face);
    }
    Ok(result)
}

#[derive(Clone, Copy)]
struct Edge {
    triangles: [usize;2],
    directions: [bool;2],
    count: usize,
}
fn edge_key(a: usize,b: usize) -> (usize,usize) { if a<b {(a,b)} else {(b,a)} }

fn topology(points: &[Point], triangles: &mut [[usize;3]]) -> R<Value> {
    let mut edges: HashMap<(usize,usize),Edge> = HashMap::new();
    let mut unique = HashSet::new();
    let mut incident = vec![Vec::new();points.len()];
    for (i, triangle) in triangles.iter().enumerate() {
        triangle_normal(points,triangle)?;
        if !unique.insert(face_key(triangle)) {
            return Err("Faces triangulées dupliquées dans la peau d'import".into());
        }
        for &node in triangle { incident[node].push(i); }
        for j in 0..3 {
            let a=triangle[j]; let b=triangle[(j+1)%3];
            let edge=edges.entry(edge_key(a,b)).or_insert(Edge {triangles:[i,0],directions:[a<b,false],count:0});
            if edge.count==2 { return Err("Peau non manifold : une arête appartient à plus de deux faces".into()); }
            edge.triangles[edge.count]=i; edge.directions[edge.count]=a<b; edge.count+=1;
        }
    }
    let mut flips=vec![None;triangles.len()];
    let mut components=0usize;
    let mut queue=VecDeque::new();
    for root in 0..triangles.len() {
        if flips[root].is_some() {continue;}
        components+=1; flips[root]=Some(false); queue.push_back(root);
        while let Some(i)=queue.pop_front() {
            let triangle=triangles[i];
            for j in 0..3 {
                let edge=edges[&edge_key(triangle[j],triangle[(j+1)%3])];
                if edge.count!=2 {continue;}
                let slot=usize::from(edge.triangles[1]==i); let other=1-slot;
                let next=edge.triangles[other];
                let expected=flips[i].unwrap()^(edge.directions[slot]==edge.directions[other]);
                match flips[next] {
                    Some(old) if old!=expected=>return Err("Peau non orientable : orientations incompatibles".into()),
                    None=>{flips[next]=Some(expected);queue.push_back(next);},
                    _=>{},
                }
            }
        }
    }
    // Edge incidence alone misses two independent surface fans sharing a
    // vertex. Verify one connected fan and zero/two boundary edges per vertex.
    let mut marks=vec![0usize;triangles.len()];
    for (node, faces) in incident.iter().enumerate().filter(|(_,faces)|!faces.is_empty()) {
        let stamp=node+1; let mut visited=0; let mut boundary=0;
        queue.push_back(faces[0]); marks[faces[0]]=stamp;
        while let Some(i)=queue.pop_front() {
            visited+=1;
            let triangle=triangles[i];
            for j in 0..3 {
                let a=triangle[j];let b=triangle[(j+1)%3];
                if a!=node&&b!=node {continue;}
                let edge=edges[&edge_key(a,b)];
                if edge.count==1 {boundary+=1;continue;}
                let next=if edge.triangles[0]==i {edge.triangles[1]} else {edge.triangles[0]};
                if marks[next]!=stamp {marks[next]=stamp;queue.push_back(next);}
            }
        }
        if visited!=faces.len() || ![0,2].contains(&boundary) {
            return Err("Peau non manifold : éventails de faces incompatibles autour d'un sommet".into());
        }
    }
    let reoriented=flips.iter().filter(|v|**v==Some(true)).count();
    for (triangle,flip) in triangles.iter_mut().zip(flips) {
        if flip==Some(true) { triangle.swap(1,2); }
    }
    let open=edges.values().filter(|edge|edge.count==1).count();
    Ok(json!({"triangles":triangles.len(),"edges":edges.len(),"open_edges":open,
        "closed":open==0,"connected_components":components,"nonmanifold_edges":0,
        "nonmanifold_vertices":0,"orientable":true,"triangles_reoriented":reoriented,
        "intersection_check":"not_exhaustive"}))
}

fn extract(input: Input) -> R<Prepared> {
    if input.schema!=SCHEMA { return Err("Schéma de données d'import incompatible".into()); }
    if !input.coordinate_scale.is_finite() || input.coordinate_scale<=0. {
        return Err("coordinate_scale doit être strictement positif et fini".into());
    }
    if serde_json::to_vec(&input.metadata).map_err(|e|e.to_string())?.len()>2*1024*1024 {
        return Err("Métadonnées d'import supérieures à 2 Mio".into());
    }
    let mut parts=input.objects;
    if !input.points.is_empty() || !input.cells.is_empty() {
        parts.insert(0,Part {points:input.points,cells:input.cells,transforms:Vec::new()});
    }
    if parts.is_empty() || parts.len()>100_000 { return Err("L'import exige entre 1 et 100000 objets".into()); }
    let mut points=Vec::new();
    let mut welded=HashMap::<[u64;3],usize>::new();
    let mut cells=Vec::new();
    let mut source_nodes=0usize; let mut source_cells=0usize;
    let mut high_order=0usize; let mut transforms=0usize;
    let mut counts=BTreeMap::<String,usize>::new();
    let mut dimensions=[0usize;4];
    let mut object_bounds=Vec::new();
    let object_count=parts.len();
    for part in parts {
        source_nodes=source_nodes.checked_add(part.points.len()).ok_or("Trop de nœuds d'import")?;
        if source_nodes>MAX_NODES { return Err("Limite de deux millions de nœuds d'import dépassée".into()); }
        if part.transforms.len()>128 { return Err("Chaîne de transformations d'import trop longue".into()); }
        transforms+=part.transforms.len();
        let mut orientation=1.;
        for matrix in &part.transforms { orientation*=transform_sign(matrix)?; }
        let mut mapping=Vec::with_capacity(part.points.len());
        let mut bounds=[f64::INFINITY,f64::INFINITY,f64::INFINITY,f64::NEG_INFINITY,f64::NEG_INFINITY,f64::NEG_INFINITY];
        for raw in part.points {
            if raw.iter().any(|x|!x.is_finite()) { return Err("Coordonnées d'import non finies".into()); }
            let point=transform(raw,&part.transforms,input.coordinate_scale)?;
            for i in 0..3 {bounds[i]=bounds[i].min(point[i]);bounds[i+3]=bounds[i+3].max(point[i]);}
            let key=point.map(|x|if x==0. {0f64.to_bits()} else {x.to_bits()});
            let index=*welded.entry(key).or_insert_with(||{let index=points.len();points.push(point);index});
            mapping.push(index);
        }
        if !mapping.is_empty() {object_bounds.push(bounds);}
        for block in part.cells {
            let kind=shape(&block.kind)?;
            source_cells=source_cells.checked_add(block.data.len()).ok_or("Trop de cellules d'import")?;
            if source_cells>MAX_CELLS { return Err("Limite de deux millions de cellules d'import dépassée".into()); }
            *counts.entry(block.kind.clone()).or_default()+=block.data.len();
            dimensions[kind.dimension]+=block.data.len();
            if kind.nodes>kind.corners {high_order+=block.data.len();}
            for row in block.data {
                if row.len()!=kind.nodes || row.iter().any(|i|*i>=mapping.len()) {
                    return Err(format!("Connectivité d'import invalide pour {} : nombre ou indice de nœud incorrect",block.kind));
                }
                let nodes:Vec<_>=row[..kind.corners].iter().map(|i|mapping[*i]).collect();
                if nodes.iter().copied().collect::<HashSet<_>>().len()!=nodes.len() {
                    return Err("Cellule d'import avec sommets primaires confondus".into());
                }
                if kind.dimension>=2 {cells.push(Cell {nodes,shape:kind,orientation});}
            }
        }
    }
    if input.reject_overlapping_objects {
        object_bounds.sort_by(|a,b|a[0].total_cmp(&b[0]));
        let mut comparisons=0usize;
        for (i,a) in object_bounds.iter().enumerate() {
            for b in &object_bounds[i+1..] {
                if b[0]>a[3] {break;}
                comparisons+=1;
                if comparisons>1_000_000 {return Err("Trop de paires d'objets pour le contrôle conservateur des intersections".into());}
                if (1..3).all(|axis|a[axis]<=b[axis+3] && a[axis+3]>=b[axis]) {
                    return Err("Objets dont les boîtes englobantes se touchent ou se chevauchent : l'union positive 3MF n'est pas reconstruite ; séparer les objets ou préparer la CAO (contrôle conservateur pouvant refuser des objets pourtant disjoints)".into());
                }
            }
        }
    }
    let dimension=if dimensions[3]>0 {3} else if dimensions[2]>0 {2} else {
        return Err("L'import ne contient aucune cellule surfacique ou volumique prise en charge".into());
    };
    let mut triangles=Vec::new(); let mut internal_faces=0;
    if dimension==3 {
        let mut lookup=HashMap::<FaceKey,usize>::new();
        let mut faces:Vec<Face>=Vec::new();
        let mut volume_keys=HashSet::new();
        for cell in cells.iter().filter(|c|c.shape.dimension==3) {
            let mut key=cell.nodes.clone();key.sort_unstable();
            if !volume_keys.insert(key) { return Err("Cellules volumiques d'import dupliquées".into()); }
            for nodes in volume_faces(cell,&points)? {
                let key=face_key(&nodes);
                if let Some(&i)=lookup.get(&key) {
                    let face=&mut faces[i];
                    if face.count!=1 {return Err("Volume non manifold : plus de deux cellules partagent une face".into());}
                    if !opposite(&face.nodes,&nodes) {
                        return Err("Cellules volumiques superposées ou orientation de face interne incohérente".into());
                    }
                    face.count+=1;internal_faces+=1;
                } else {
                    if faces.len()>=MAX_FACES {return Err("Limite de faces d'extraction dépassée".into());}
                    lookup.insert(key,faces.len());faces.push(Face {nodes,count:1});
                }
            }
        }
        for face in faces.iter().filter(|face|face.count==1) {triangulate(&face.nodes,&points,&mut triangles)?;}
    } else {
        for cell in cells.iter().filter(|c|c.shape.dimension==2) {triangulate(&cell.nodes,&points,&mut triangles)?;}
    }
    if triangles.is_empty() {return Err("Aucune peau extérieure après extraction".into());}
    let topology=topology(&points,&mut triangles)?;
    if dimension==3 && topology["closed"]!=true {
        return Err("La peau extraite du volume n'est pas fermée ; maillage non conforme ou incomplet".into());
    }
    let mut used:Vec<_>=triangles.iter().flatten().copied().collect();used.sort_unstable();used.dedup();
    let report=json!({"schema":"atelier.mesh_import_report.v1","operation":"prepare_mesh_import",
        "preparation_version":PREPARATION_VERSION,"computation_backend":"rust",
        "source_dimension":dimension,"source_objects":object_count,"source_nodes":source_nodes,
        "source_cell_counts":counts,"source_dimension_counts":dimensions,"source_cells":source_cells,
        "source_metadata":input.metadata,"coordinate_scale":input.coordinate_scale,
        "affine_transforms_applied":transforms,"exact_duplicate_points_merged":source_nodes-points.len(),
        "overlapping_objects_policy":if input.reject_overlapping_objects{"reject_touching_or_overlapping_aabb_conservative"}else{"no_exhaustive_intersection_test"},
        "higher_order_elements":high_order,"higher_order_geometry_linearized":high_order>0,
        "output_nodes":used.len(),"output_triangles":triangles.len(),"internal_faces_removed":internal_faces,
        "source_nodes_not_used_in_skin":source_nodes-used.len(),"topology":topology,
        "source_file_modified":false,"original_groups_reapplied":false,"internal_material_interfaces_preserved":false,
        "source_volume_validation":if dimension==3{"primary_corner_oriented_convex_face_fans_not_full_curved_jacobians"}else{"not_applicable"},
        "warnings":["Reconstruction géométrique approximative par une peau triangulée ; ce n'est pas une conversion conservant le maillage d'origine",
            "Les nœuds et courbures d'ordre supérieur sont remplacés par les sommets primaires pour cette reconstruction ; aucune validation des Jacobiennes courbes du fichier source n'est revendiquée",
            "Les groupes, matériaux, interfaces internes et conditions aux limites d'origine ne sont pas transférés au nouveau maillage ; les recréer explicitement",
            "Les éléments de dimension inférieure au domaine retenu servent uniquement aux métadonnées de provenance",
            "Les points exactement confondus sont réunis ; aucune tolérance de rapprochement n'est appliquée ici",
            "Les intersections géométriques entre faces ou objets ne sont pas contrôlées exhaustivement"]});
    Ok(Prepared {points,triangles,report})
}

fn write_surface(path:&Path, prepared:&Prepared) -> R<()> {
    let extension=path.extension().and_then(|x|x.to_str()).unwrap_or("").to_ascii_lowercase();
    if !["msh","stl"].contains(&extension.as_str()) {return Err("La peau préparée doit être écrite en MSH ou STL".into());}
    if path.exists() || path.is_symlink() {return Err("La destination de préparation existe déjà ; choisir un nouveau fichier".into());}
    let parent=path.parent().unwrap_or(Path::new("."));
    fs::create_dir_all(parent).map_err(|e|e.to_string())?;
    let stamp=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|e|e.to_string())?.as_nanos();
    let temporary=parent.join(format!(".mesh-import-{}-{stamp}.tmp",std::process::id()));
    let result=(||{
        let file=OpenOptions::new().write(true).create_new(true).open(&temporary).map_err(|e|e.to_string())?;
        let mut out=BufWriter::new(file);
        if extension=="stl" {
            writeln!(out,"solid atelier_prepared_surface_v1").map_err(|e|e.to_string())?;
            for triangle in &prepared.triangles {
                let normal=triangle_normal(&prepared.points,triangle)?;
                writeln!(out,"  facet normal {:.17e} {:.17e} {:.17e}\n    outer loop",normal[0],normal[1],normal[2]).map_err(|e|e.to_string())?;
                for &i in triangle {
                    let p=prepared.points[i];writeln!(out,"      vertex {:.17e} {:.17e} {:.17e}",p[0],p[1],p[2]).map_err(|e|e.to_string())?;
                }
                writeln!(out,"    endloop\n  endfacet").map_err(|e|e.to_string())?;
            }
            writeln!(out,"endsolid atelier_prepared_surface_v1").map_err(|e|e.to_string())?;
        } else {
            let mut used:Vec<_>=prepared.triangles.iter().flatten().copied().collect();used.sort_unstable();used.dedup();
            writeln!(out,"$MeshFormat\n2.2 0 8\n$EndMeshFormat\n$Nodes\n{}",used.len()).map_err(|e|e.to_string())?;
            for i in used {
                let p=prepared.points[i];writeln!(out,"{} {:.17e} {:.17e} {:.17e}",i+1,p[0],p[1],p[2]).map_err(|e|e.to_string())?;
            }
            writeln!(out,"$EndNodes\n$Elements\n{}",prepared.triangles.len()).map_err(|e|e.to_string())?;
            for (i,t) in prepared.triangles.iter().enumerate() {
                writeln!(out,"{} 2 2 0 1 {} {} {}",i+1,t[0]+1,t[1]+1,t[2]+1).map_err(|e|e.to_string())?;
            }
            writeln!(out,"$EndElements").map_err(|e|e.to_string())?;
        }
        out.flush().map_err(|e|e.to_string())?;
        out.get_ref().sync_all().map_err(|e|e.to_string())?;
        drop(out);
        // A hard link publishes a complete file without replacing a destination
        // created concurrently. The temporary and destination share a directory.
        fs::hard_link(&temporary,path).map_err(|e|format!("Peau préparée non publiable sans écrasement : {e}"))?;
        Ok(())
    })();
    let cleanup=fs::remove_file(&temporary);
    if result.is_ok() {cleanup.map_err(|e|e.to_string())?;}
    result
}

fn source_file(path:&Path) -> R<()> {
    if path.is_symlink() || !path.is_file() {return Err("Les données d'import doivent être un fichier régulier, sans lien symbolique".into());}
    if path.metadata().map_err(|e|e.to_string())?.len()>MAX_INPUT_BYTES {return Err("Données d'import supérieures à 200 Mio".into());}
    Ok(())
}

fn preserve_source_report(request:&Value, source:&Path) -> R<()> {
    if let Some(directory)=request.get("output_dir").and_then(Value::as_str) {
        let report=Path::new(directory).join("report.json");
        if report.exists() && report.canonicalize().map_err(|e|e.to_string())?==source.canonicalize().map_err(|e|e.to_string())? {
            return Err("Le rapport de préparation doit être distinct du fichier source".into());
        }
    }
    Ok(())
}

fn gmsh_input(request:&Value, source:&Path) -> R<Input> {
    let extension=source.extension().and_then(|x|x.to_str()).unwrap_or("").to_ascii_lowercase();
    if !["unv","msh","vtk"].contains(&extension.as_str()) {
        return Err("La lecture native de maillage accepte uniquement UNV, MSH et VTK classiques ; les scripts sont refusés".into());
    }
    let library=request["gmsh_library"].as_str().ok_or("Bibliothèque Gmsh requise pour cette lecture native")?;
    let api=crate::gmsh::Gmsh::new(library)?;api.open(source)?;
    let (tags,points)=api.nodes()?;
    if points.len()>MAX_NODES {return Err("Limite de deux millions de nœuds d'import dépassée".into());}
    let mapping:HashMap<_,_>=tags.iter().enumerate().map(|(index,tag)|(*tag,index)).collect();
    if mapping.len()!=tags.len() {return Err("Identifiants de nœuds source dupliqués".into());}
    let mut cells=Vec::new();let mut count=0usize;
    for block in api.elements(-1,-1)? {
        let kind=match (block.dim,block.num_nodes) {
            (0,1)=>"vertex",(1,2)=>"line",(1,3)=>"line3",(2,3)=>"triangle",(2,6)=>"triangle6",
            (2,4)=>"quad",(2,8)=>"quad8",(2,9)=>"quad9",(3,4)=>"tetra",(3,10)=>"tetra10",
            (3,8)=>"hexahedron",(3,20)=>"hexahedron20",(3,27)=>"hexahedron27",
            (3,6)=>"wedge",(3,15)=>"wedge15",(3,18)=>"wedge18",
            (3,5)=>"pyramid",(3,13)=>"pyramid13",(3,14)=>"pyramid14",
            _=>return Err(format!("Type d'élément natif non pris en charge : {}",block.name)),
        };
        let shape=shape(kind)?;
        if block.num_primary!=shape.corners || ![0,1,2].contains(&block.order) {
            return Err(format!("Ordre ou sommets primaires non pris en charge : {}",block.name));
        }
        count=count.checked_add(block.tags.len()).ok_or("Trop de cellules natives")?;
        if count>MAX_CELLS {return Err("Limite de deux millions de cellules d'import dépassée".into());}
        let data=block.nodes.chunks_exact(block.num_nodes).map(|row|row.iter().map(|tag|{
            mapping.get(tag).copied().ok_or_else(||"Connectivité native : nœud source absent".to_string())
        }).collect::<R<Vec<_>>>()).collect::<R<Vec<_>>>()?;
        cells.push(CellBlock {kind:kind.into(),data});
    }
    let groups=api.physical_group_inventory()?.into_iter().map(|(dimension,tag,name,entities)|{
        json!({"dimension":dimension,"tag":tag,"name":name,"entity_tags":entities})
    }).collect::<Vec<_>>();
    let entities=api.entities(-1)?.into_iter().map(|(dimension,tag)|json!({"dimension":dimension,"tag":tag})).collect::<Vec<_>>();
    let coordinate_scale=match request.get("coordinate_scale") {
        Some(value)=>value.as_f64().ok_or("coordinate_scale doit être un nombre")?,None=>1.,
    };
    Ok(Input {schema:SCHEMA.into(),points,cells,objects:Vec::new(),coordinate_scale,
        reject_overlapping_objects:false,
        metadata:json!({"reader":"Rust / Gmsh 4.15 C ABI","source_format":extension,
            "physical_groups":groups,"source_entities":entities,
            "groups_note":"Groupes présents après lecture native ; non transférés à la reconstruction"})})
}

pub fn prepare(request:&Value) -> R<Value> {
    let output=PathBuf::from(request["output_mesh"].as_str().ok_or("Destination de peau absente")?);
    if output.exists() || output.is_symlink() {return Err("La destination de préparation existe déjà ; choisir un nouveau fichier".into());}
    let input=match (request.get("mesh_data"),request.get("source_mesh")) {
        (Some(value),None)=>{
            let source=PathBuf::from(value.as_str().ok_or("mesh_data doit être un chemin")?);
            source_file(&source)?;preserve_source_report(request,&source)?;
            serde_json::from_reader(BufReader::new(File::open(&source).map_err(|e|e.to_string())?))
                .map_err(|e|format!("Données d'import invalides : {e}"))?
        },
        (None,Some(value))=>{
            let source=PathBuf::from(value.as_str().ok_or("source_mesh doit être un chemin")?);
            source_file(&source)?;preserve_source_report(request,&source)?;gmsh_input(request,&source)?
        },
        _=>return Err("Fournir exactement mesh_data (JSON normalisé) ou source_mesh (lecture native)".into()),
    };
    let mut prepared=extract(input)?;
    write_surface(&output,&prepared)?;
    prepared.report["output_mesh"]=json!(output);
    prepared.report["output_format"]=json!(output.extension().and_then(|x|x.to_str()).unwrap_or("").to_ascii_lowercase());
    Ok(prepared.report)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(points:Vec<Point>, kind:&str, data:Vec<Vec<usize>>) -> R<Prepared> {
        extract(serde_json::from_value(json!({"schema":SCHEMA,"points":points,"cells":[{"type":kind,"data":data}]})).unwrap())
    }
    fn tetra() -> Vec<Point> {vec![[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]}

    #[test]
    fn extracts_only_outer_faces_of_adjacent_tetrahedra() {
        let mut points=tetra();points.push([0.,0.,-1.]);
        let result=run(points,"tetra",vec![vec![0,1,2,3],vec![0,2,1,4]]).unwrap();
        assert_eq!(result.triangles.len(),6);assert_eq!(result.report["internal_faces_removed"],1);
        assert_eq!(result.report["topology"]["closed"],true);
    }

    #[test]
    fn quadratic_cells_are_explicitly_approximated_using_primary_corners() {
        let mut points=tetra();points.extend([[0.5,0.,0.],[0.5,0.5,0.],[0.,0.5,0.],[0.,0.,0.5],[0.5,0.,0.5],[0.,0.5,0.5]]);
        let result=run(points,"tetra10",vec![(0..10).collect()]).unwrap();
        assert_eq!(result.triangles.len(),4);assert_eq!(result.report["output_nodes"],4);
        assert_eq!(result.report["higher_order_geometry_linearized"],true);
        assert_eq!(result.report["source_nodes_not_used_in_skin"],6);
        let result=run(vec![[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.5,0.,0.1],[0.5,0.5,0.1],[0.,0.5,0.1]],"triangle6",vec![(0..6).collect()]).unwrap();
        assert_eq!(result.triangles.len(),1);assert_eq!(result.report["topology"]["open_edges"],3);
    }

    #[test]
    fn triangulates_hexahedron_wedge_pyramid_and_quadratic_quad() {
        let hex=vec![[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.],[0.,0.,1.],[1.,0.,1.],[1.,1.,1.],[0.,1.,1.]];
        assert_eq!(run(hex,"hexahedron",vec![(0..8).collect()]).unwrap().triangles.len(),12);
        let wedge=vec![[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.],[1.,0.,1.],[0.,1.,1.]];
        assert_eq!(run(wedge,"wedge",vec![(0..6).collect()]).unwrap().triangles.len(),8);
        let pyramid=vec![[-1.,-1.,0.],[1.,-1.,0.],[1.,1.,0.],[-1.,1.,0.],[0.,0.,1.]];
        assert_eq!(run(pyramid,"pyramid",vec![(0..5).collect()]).unwrap().triangles.len(),6);
        let quad=vec![[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.],[0.5,0.,0.],[1.,0.5,0.],[0.5,1.,0.],[0.,0.5,0.]];
        let result=run(quad,"quad8",vec![(0..8).collect()]).unwrap();
        assert_eq!(result.triangles.len(),2);assert_eq!(result.report["higher_order_elements"],1);
    }

    #[test]
    fn refuses_inverted_duplicate_and_nonmanifold_volumes() {
        assert!(run(tetra(),"tetra",vec![vec![0,2,1,3]]).err().unwrap().contains("inversée"));
        assert!(run(tetra(),"tetra",vec![vec![0,1,2,3],vec![0,1,2,3]]).err().unwrap().contains("dupliquées"));
        let mut points=tetra();points.extend([[0.,0.,-1.],[0.2,0.2,-2.]]);
        assert!(run(points,"tetra",vec![vec![0,1,2,3],vec![0,2,1,4],vec![0,2,1,5]]).is_err());
    }

    #[test]
    fn refuses_nonmanifold_edges_and_vertex_fans_and_degenerate_faces() {
        let points=vec![[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,-1.,0.],[0.,0.,1.]];
        assert!(run(points.clone(),"triangle",vec![vec![0,1,2],vec![1,0,3],vec![0,1,4]]).err().unwrap().contains("arête"));
        assert!(run(points,"triangle",vec![vec![0,1,2],vec![0,3,4]]).err().unwrap().contains("sommet"));
        assert!(run(vec![[0.,0.,0.],[1.,0.,0.],[2.,0.,0.]],"triangle",vec![vec![0,1,2]]).is_err());
        assert!(run(vec![[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]],"quad",vec![vec![0,1,3,2]]).err().unwrap().contains("croisé"));
    }

    #[test]
    fn normalizes_surface_orientation_without_changing_coordinates() {
        let points=vec![[0.,0.,0.],[1.,0.,0.],[1.,1.,0.],[0.,1.,0.]];
        let result=run(points.clone(),"triangle",vec![vec![0,1,2],vec![0,3,2]]).unwrap();
        assert_eq!(result.report["topology"]["triangles_reoriented"],1);
        assert_eq!(result.points,points);
    }

    #[test]
    fn applies_object_transform_chain_then_positive_unit_scale() {
        let translate=[1.,0.,0.,4.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.];
        let scale=[2.,0.,0.,0.,0.,2.,0.,0.,0.,0.,2.,0.,0.,0.,0.,1.];
        let input=serde_json::from_value(json!({"schema":SCHEMA,"coordinate_scale":0.001,"objects":[{
            "points":[[1.,2.,3.],[2.,2.,3.],[1.,3.,3.]],"cells":[{"type":"triangle","data":[[0,1,2]]}],"transforms":[translate,scale]}]})).unwrap();
        let result=extract(input).unwrap();assert_eq!(result.points[0],[0.01,0.004,0.006]);
        assert_eq!(result.report["affine_transforms_applied"],2);
        let reflection=[-1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.];
        let input=serde_json::from_value(json!({"schema":SCHEMA,"objects":[{
            "points":tetra(),"cells":[{"type":"tetra","data":[[0,1,2,3]]}],"transforms":[reflection]}]})).unwrap();
        assert_eq!(extract(input).unwrap().report["topology"]["closed"],true);
    }

    #[test]
    fn refuses_unsupported_types_invalid_indices_and_singular_transforms() {
        assert!(run(tetra(),"polyhedron",vec![vec![0,1,2,3]]).is_err());
        assert!(run(tetra(),"tetra",vec![vec![0,1,2,99]]).is_err());
        assert!(run(tetra(),"tetra10",vec![vec![0,1,2,3]]).is_err());
        assert!(transform_sign(&[0.;16]).is_err());
        let input=serde_json::from_value(json!({"schema":SCHEMA,"coordinate_scale":0.,"points":tetra(),"cells":[]})).unwrap();
        assert!(extract(input).is_err());
    }

    #[test]
    fn conservatively_refuses_touching_object_boxes_when_positive_union_is_required() {
        let object=json!({"points":tetra(),"cells":[{"type":"tetra","data":[[0,1,2,3]]}]});
        let input=serde_json::from_value(json!({"schema":SCHEMA,"objects":[object.clone(),object],"reject_overlapping_objects":true})).unwrap();
        assert!(extract(input).err().unwrap().contains("union positive 3MF"));
        let object=json!({"points":tetra(),"cells":[{"type":"tetra","data":[[0,1,2,3]]}]});
        let shifted=json!({"points":tetra(),"cells":[{"type":"tetra","data":[[0,1,2,3]]}],
            "transforms":[[1.,0.,0.,2.,0.,1.,0.,0.,0.,0.,1.,0.,0.,0.,0.,1.]]});
        let input=serde_json::from_value(json!({"schema":SCHEMA,"objects":[object,shifted],"reject_overlapping_objects":true})).unwrap();
        let result=extract(input).unwrap();assert_eq!(result.report["topology"]["connected_components"],2);
        assert_eq!(result.report["overlapping_objects_policy"],"reject_touching_or_overlapping_aabb_conservative");
    }

    #[test]
    fn publishes_prepared_mesh_once_without_modifying_input_or_existing_output() {
        let stamp=SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos();
        let directory=std::env::temp_dir().join(format!("atelier-import-test-{}-{stamp}",std::process::id()));
        fs::create_dir(&directory).unwrap();
        let source=directory.join("mesh.json");let output=directory.join("skin.msh");
        let bytes=serde_json::to_vec(&json!({"schema":SCHEMA,"points":tetra(),"cells":[{"type":"tetra","data":[[0,1,2,3]]}]})).unwrap();
        fs::write(&source,&bytes).unwrap();
        let request=json!({"mesh_data":source,"output_mesh":output});
        assert_eq!(prepare(&request).unwrap()["output_triangles"],4);
        let original=fs::read(&output).unwrap();assert!(original.starts_with(b"$MeshFormat\n2.2"));
        assert!(prepare(&request).is_err());assert_eq!(fs::read(&output).unwrap(),original);
        assert_eq!(fs::read(&source).unwrap(),bytes);
        let report_source=directory.join("report.json");fs::write(&report_source,&bytes).unwrap();
        let collision=json!({"mesh_data":report_source,"output_mesh":directory.join("other.msh"),"output_dir":directory});
        assert!(prepare(&collision).err().unwrap().contains("distinct du fichier source"));
        assert_eq!(fs::read(&report_source).unwrap(),bytes);
        fs::remove_dir_all(directory).unwrap();
    }
}
