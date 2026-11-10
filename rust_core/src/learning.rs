//! Native self-generated supervised training of the Gmsh portfolio.
//!
//! Geometry evaluation, quality labels, grouping, fitting and policy comparison
//! all execute in this Rust process. Python is only a caller of this operation.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

const ALGORITHMS: [&str;3] = ["automatic","delaunay","frontal"];
const FEATURES: [&str;8] = ["mode","thin_extent","middle_extent","relative_size","hole_fraction","curved","relative_corner_size","boundary_complexity"];
const ENGINE: &str = "4.15.2";
const WIDTH: usize = 12;

type Result<T> = std::result::Result<T,String>;

#[derive(Clone)]
struct Row {
    project: Value, features:[f64;8], family:Option<String>, mode:String,
    geometry_id:String, state_id:String, name:String,
    rewards:[f64;3], evaluations:Vec<Value>,
}

struct Rng(u64);
impl Rng {
    fn new(seed:u64)->Self {Self(seed ^ 0x9e3779b97f4a7c15)}
    fn next(&mut self)->u64 {
        let mut x=self.0; x^=x>>12; x^=x<<25; x^=x>>27; self.0=x;
        x.wrapping_mul(0x2545f4914f6cdd1d)
    }
    fn unit(&mut self)->f64 {(self.next()>>11) as f64 / 9007199254740992.0}
    fn shuffle<T>(&mut self,items:&mut[T]) {
        for i in (1..items.len()).rev() {let j=(self.next()%(i as u64+1)) as usize;items.swap(i,j);}
    }
}

fn finite(v:&Value,name:&str)->Result<f64> {
    v.as_f64().filter(|x|x.is_finite()).ok_or_else(||format!("{name} : nombre fini attendu"))
}
fn num(object:&Value,key:&str,default:f64)->Result<f64> {
    match object.get(key) {Some(v)=>finite(v,key),None=>Ok(default)}
}
fn positive(x:f64,name:&str)->Result<f64> {
    if x>0.0&&x.is_finite() {Ok(x)} else {Err(format!("{name} doit être strictement positif"))}
}
fn flag(object:&Value,key:&str,default:bool)->Result<bool> {
    match object.get(key) {Some(v)=>v.as_bool().ok_or_else(||format!("{key} : booléen attendu")),None=>Ok(default)}
}
fn integer(object:&Value,key:&str,default:u64,min:u64,max:u64)->Result<u64> {
    let n=match object.get(key) {Some(v)=>v.as_u64().ok_or_else(||format!("{key} : entier attendu"))?,None=>default};
    if (min..=max).contains(&n) {Ok(n)} else {Err(format!("{key} doit être compris entre {min} et {max}"))}
}
fn unit(name:&str)->Result<f64> {
    match name {"m"=>Ok(1.0),"cm"=>Ok(0.01),"mm"=>Ok(0.001),"in"|"inch"=>Ok(0.0254),_=>Err("Unité d'apprentissage inconnue".to_string())}
}
fn sum(values:impl Iterator<Item=f64>)->f64 {
    let (mut total,mut correction)=(0.0,0.0);
    for value in values {let adjusted=value-correction;let next=total+adjusted;correction=(next-total)-adjusted;total=next;}
    total
}
fn canonical(value:&Value)->Result<Value> {
    match value {
        Value::Number(_)=>{let x=finite(value,"identité")?;Ok(json!(if x==0.0 {0.0}else{x}))},
        Value::Array(items)=>items.iter().map(canonical).collect::<Result<Vec<_>>>().map(Value::Array),
        Value::Object(items)=>{
            let mut result=serde_json::Map::new();
            for (key,value) in items {result.insert(key.clone(),canonical(value)?);}
            Ok(Value::Object(result))
        },
        _=>Ok(value.clone()),
    }
}
fn digest(value:&Value)->Result<String> {
    let bytes=serde_json::to_vec(&canonical(value)?).map_err(|e|e.to_string())?;
    Ok(format!("{:x}",Sha256::digest(bytes)))
}

fn canonical_ring(value:&Value)->Result<Value> {
    let rows=value.as_array().filter(|x|(3..=5001).contains(&x.len())).ok_or("Contour polygonal invalide")?;
    let mut points=Vec::<[f64;2]>::with_capacity(rows.len());
    for row in rows {
        let xy=row.as_array().filter(|x|x.len()==2).ok_or("Un sommet doit contenir [x,y]")?;
        points.push([finite(&xy[0],"x")?,finite(&xy[1],"y")?]);
    }
    if points.len()>1&&points.first()==points.last() {points.pop();}
    if points.len()<3 {return Err("Contour dégénéré".to_string());}
    let xmin=points.iter().map(|p|p[0]).fold(f64::INFINITY,f64::min);
    let ymin=points.iter().map(|p|p[1]).fold(f64::INFINITY,f64::min);
    let extent=points.iter().map(|p|(p[0]-xmin).max(p[1]-ymin)).fold(0.0_f64,f64::max);
    if extent<=0.0||!extent.is_finite() {return Err("Contour dégénéré".to_string());}
    let area=sum((0..points.len()).map(|i|{
        let a=[(points[i][0]-xmin)/extent,(points[i][1]-ymin)/extent];
        let b=[(points[(i+1)%points.len()][0]-xmin)/extent,(points[(i+1)%points.len()][1]-ymin)/extent];
        a[0]*b[1]-a[1]*b[0]
    }));
    if area.abs()<=2e-14 {return Err("Contour d'aire nulle ou non résolue".to_string());}
    if area<0.0 {points.reverse();}
    let first=(0..points.len()).min_by(|&a,&b|points[a][0].total_cmp(&points[b][0]).then(points[a][1].total_cmp(&points[b][1]))).unwrap();
    points.rotate_left(first);
    Ok(json!(points))
}

fn geometry(g:&Value,mode:&str)->Result<Value> {
    let kind=g.get("kind").and_then(Value::as_str).unwrap_or("plate");
    let mut result=match kind {
        "plate"=>json!({"kind":kind,"width":num(g,"width",10.0)?,"height":num(g,"height",6.0)?,"corner_radius":num(g,"corner_radius",0.0)?,"chamfer":num(g,"chamfer",0.0)?}),
        "box"=>json!({"kind":kind,"width":num(g,"width",10.0)?,"height":num(g,"height",6.0)?,"depth":num(g,"depth",4.0)?}),
        "sphere"=>json!({"kind":kind,"radius":num(g,"radius",3.0)?}),
        "bracket"=>json!({"kind":kind,"width":num(g,"width",10.0)?,"height":num(g,"height",8.0)?,"depth":num(g,"depth",4.0)?,"wall":num(g,"wall",2.0)?,"throughhole_radius":num(g,"throughhole_radius",0.0)?}),
        "polygon"=>json!({"kind":kind,"outer":canonical_ring(&g["outer"])?}),
        _=>return Err("L'apprentissage est limité aux géométries natives ; les CAO importées sont hors domaine".to_string()),
    };
    if ["plate","polygon"].contains(&kind)&&mode=="volume" {result["thickness"]=json!(num(g,"thickness",2.0)?);}
    if kind=="plate" {
        let mut holes=Vec::new();
        if let Some(rows)=g.get("holes") {
            for row in rows.as_array().filter(|rows|rows.len()<=1000).ok_or("Liste des perçages invalide")? {
                holes.push(json!({"x":finite(&row["x"],"x du trou")?,"y":finite(&row["y"],"y du trou")?,"radius":positive(finite(&row["radius"],"rayon du trou")?,"rayon du trou")?}));
            }
        }
        holes.sort_by_key(|hole|serde_json::to_string(hole).unwrap());result["holes"]=json!(holes);
    }
    if kind=="polygon" {
        let mut holes=Vec::new();
        if let Some(rows)=g.get("holes") {
            for row in rows.as_array().filter(|rows|rows.len()<=1000).ok_or("Liste des trous invalide")? {holes.push(canonical_ring(row)?);}
        }
        holes.sort_by_key(|hole|serde_json::to_string(hole).unwrap());result["holes"]=json!(holes);
    }
    Ok(result)
}

fn scale_geometry(g:&mut Value,factor:f64)->Result<()> {
    let kind=g["kind"].as_str().unwrap_or("").to_string();
    for key in ["width","height","depth","thickness","radius","wall","corner_radius","chamfer","throughhole_radius"] {
        if let Some(value)=g.get(key) {
            let x=finite(value,key)?*factor;
            if !x.is_finite() {return Err("Géométrie hors plage après conversion d'unité".to_string());}
            g[key]=json!(x);
        }
    }
    if kind=="polygon" {
        let scale_ring=|ring:&mut Value|->Result<()> {
            for point in ring.as_array_mut().ok_or("Contour absent")? {
                for coordinate in point.as_array_mut().ok_or("Coordonnée absente")? {
                    let x=finite(coordinate,"coordonnée")?*factor;
                    if !x.is_finite() {return Err("Conversion d'unité hors plage".to_string());}
                    *coordinate=json!(x);
                }
            }
            Ok(())
        };
        scale_ring(&mut g["outer"])?;
        for hole in g["holes"].as_array_mut().ok_or("Trous absents")? {scale_ring(hole)?;}
    } else if kind=="plate" {
        for hole in g["holes"].as_array_mut().ok_or("Perçages absents")? {
            for key in ["x","y","radius"] {
                let x=finite(&hole[key],key)?*factor;
                if !x.is_finite() {return Err("Perçage hors plage après conversion d'unité".to_string());}
                hole[key]=json!(x);
            }
        }
    }
    Ok(())
}

fn signature(project:&Value)->Result<Value> {
    let mesh=&project["mesh"];
    Ok(json!({"order":integer(mesh,"order",1,1,2)?,"threads":integer(mesh,"threads",1,1,64)?,
        "recombine":flag(mesh,"recombine",false)?,"min_size":positive(num(mesh,"min_size",0.1)?,"min_size")?,"repair_enabled":false}))
}

pub fn default_cases()->Vec<Value> {
    let templates=vec![
        ("plate_holes","planar",json!({"kind":"plate","width":4,"height":3,"holes":[{"x":2,"y":1.5,"radius":0.45}]})),
        ("plate_fillet","planar",json!({"kind":"plate","width":4,"height":3,"corner_radius":0.5})),
        ("concave","planar",json!({"kind":"polygon","outer":[[0,0],[4,0],[4,1],[1,1],[1,3],[0,3]]})),
        ("box_surface","surface",json!({"kind":"box","width":3,"height":2,"depth":1.5})),
        ("sphere_surface","surface",json!({"kind":"sphere","radius":1.5})),
        ("box_volume","volume",json!({"kind":"box","width":3,"height":2,"depth":1.5})),
        ("sphere_volume","volume",json!({"kind":"sphere","radius":1.5})),
        ("bracket_volume","volume",json!({"kind":"bracket","width":3,"height":3,"depth":1.2,"wall":0.8})),
    ];
    let mut result=Vec::new();
    for (family,mode,geometry) in templates {
        for variant in 0..2 {
            result.push(json!({"family":family,"project":{"schema_version":1,"name":format!("{family}_{variant}"),"mode":mode,"units":{"input":"mm","internal":"m"},"geometry":geometry,
                "mesh":{"size":0.8+0.2*variant as f64,"min_size":0.1,"order":1,"algorithm":"automatic","recombine":false,"threads":1,"max_elements":5000},"repair":{"enabled":false}}}));
        }
    }
    result
}

fn has_controls(project:&Value)->bool {
    let nonempty=|value:&Value|match value {Value::Array(a)=>!a.is_empty(),Value::Object(o)=>!o.is_empty(),Value::Null=>false,_=>true};
    if nonempty(&project["local_sizes"])||nonempty(&project["correspondences"])
        ||nonempty(&project["refinement_regions"])||nonempty(&project["critical_zones"])
        ||nonempty(&project["quality_policy"]) {return true;}
    if project["mesh"].get("smoothing").is_some_and(|v|v.as_u64()!=Some(1))
        ||project["mesh"].get("optimize").is_some_and(|v|v.as_bool()!=Some(true))
        ||project["mesh"].get("curvature_elements").is_some_and(|v|v.as_u64()!=Some(16)) {return true;}
    if project["selection"]["scope"].as_str().is_some_and(|scope|scope!="all") {return true;}
    let t=&project["geometry"]["transform"];
    if t.is_null() {return false;}
    if !t.is_object() {return true;}
    if t.get("scale").is_some_and(|v|v.as_f64()!=Some(1.0)) {return true;}
    for key in ["rotation_degrees","angle_degrees"] {
        if t.get(key).is_some_and(|v|v.as_f64()!=Some(0.0)) {return true;}
    }
    t.get("translation").is_some_and(|v|v.as_array().filter(|a|a.len()==3).map(|a|a.iter().any(|x|x.as_f64()!=Some(0.0))).unwrap_or(true))
}

fn normalize_cases(request:&Value)->Result<Vec<Row>> {
    let generated=request.get("cases").is_none();
    let cases=if let Some(cases)=request.get("cases") {cases.as_array().filter(|v|(2..=64).contains(&v.len())).ok_or("Entre 2 et 64 cas d'apprentissage sont requis")?.clone()}else{default_cases()};
    let target_unit=request.get("training_units").and_then(Value::as_str).unwrap_or("mm");
    let target_factor=unit(target_unit)?;
    let overrides=&request["training_mesh"];
    if !overrides.is_null()&&!overrides.is_object() {return Err("training_mesh doit être un objet".to_string());}
    let mut rows=Vec::new();
    for (index,item) in cases.iter().enumerate() {
        if !item.is_object() {return Err("Chaque cas doit être un objet".to_string());}
        let original=item.get("project").unwrap_or(item);
        if !original.is_object() {return Err("Projet d'apprentissage invalide".to_string());}
        let mode=original.get("mode").and_then(Value::as_str).unwrap_or("planar");
        if !["planar","surface","volume"].contains(&mode) {return Err("Mode d'apprentissage inconnu".to_string());}
        if has_controls(original) {return Err("L'apprentissage ne couvre pas les tailles locales, correspondances, sélections partielles ou transformations".to_string());}
        if flag(&original["repair"],"enabled",false)? {return Err("Désactiver la réparation dans les cas d'apprentissage natifs".to_string());}
        let source_unit=original["units"]["input"].as_str().unwrap_or("mm");
        let conversion=unit(source_unit)?/target_factor;
        let mut g=geometry(&original["geometry"],mode)?;
        scale_geometry(&mut g,conversion)?;
        // Converting units can change a lexical ordering of numeric strings.
        // Canonicalize again in the common target unit, and execute that exact
        // ordering so equal identities always correspond to equal native input.
        g=geometry(&g,mode)?;
        let mut mesh=original.get("mesh").cloned().unwrap_or(json!({}));
        if !mesh.is_object() {return Err("Paramètres de maillage invalides".to_string());}
        let size=positive(num(&mesh,"size",0.5)?,"size")?*conversion;
        let min_size=positive(num(&mesh,"min_size",(0.1_f64).min(size/conversion))?,"min_size")?*conversion;
        mesh["size"]=json!(size);mesh["min_size"]=json!(min_size);
        for key in ["order","threads","recombine","min_size","max_elements"] {
            if let Some(value)=overrides.get(key) {mesh[key]=value.clone();}
        }
        let min_size=positive(num(&mesh,"min_size",0.1)?,"min_size")?;
        mesh["size"]=json!(positive(size.max(min_size),"size")?);
        mesh["order"]=json!(integer(&mesh,"order",1,1,2)?);
        mesh["threads"]=json!(integer(&mesh,"threads",1,1,64)?);
        mesh["recombine"]=json!(flag(&mesh,"recombine",false)?);
        mesh["max_elements"]=json!(integer(&mesh,"max_elements",5000,10,2_000_000)?.min(5000));
        mesh["algorithm"]=json!("automatic");
        // A surface-only quadrangular request has no compatible tetrahedral
        // default volume family. Custom cases remain visible as failure labels.
        if generated&&mode=="volume"&&mesh["recombine"]==true {continue;}
        let name=original.get("name").and_then(Value::as_str).unwrap_or("Cas d'apprentissage");
        if name.trim().is_empty()||name.len()>200 {return Err("Nom de cas invalide".to_string());}
        let family=item.get("family").or_else(||original.get("family")).map(|v|v.as_str().filter(|s|!s.trim().is_empty()&&s.len()<=200).map(str::to_string).ok_or("Nom de famille invalide".to_string())).transpose()?;
        let mut project=json!({"schema_version":1,"name":name,"mode":mode,"units":{"input":target_unit,"internal":"m"},"geometry":g,"mesh":mesh,"repair":{"enabled":false,"tolerance":1e-7,"sew":true,"small_edges":true,"small_faces":false,"make_solids":false}});
        let features=crate::advisor::extract_features(&project)?;
        let mut si_geometry=project["geometry"].clone();scale_geometry(&mut si_geometry,target_factor)?;
        let geometry_id=digest(&json!({"mode":mode,"geometry_si":si_geometry}))?;
        let mut state_mesh=project["mesh"].clone();
        state_mesh.as_object_mut().unwrap().remove("algorithm");
        state_mesh["size"]=json!(finite(&state_mesh["size"],"size")?*target_factor);
        state_mesh["min_size"]=json!(finite(&state_mesh["min_size"],"min_size")?*target_factor);
        let state_id=digest(&json!({"schema_version":1,"gmsh_version":ENGINE,"geometry_id":geometry_id,"mesh_si":state_mesh,"repair_enabled":false}))?;
        project["name"]=json!(format!("{}",name));
        rows.push(Row{project,features,family,mode:mode.to_string(),geometry_id,state_id,name:if name=="Cas d'apprentissage" {format!("case_{index}")}else{name.to_string()},rewards:[0.0;3],evaluations:Vec::new()});
    }
    if rows.len()<2 {return Err("Il faut au moins deux cas d'apprentissage compatibles".to_string());}
    let expected=signature(&rows[0].project)?;
    if rows.iter().any(|row|signature(&row.project).ok()!=Some(expected.clone())) {return Err("Les cas doivent partager ordre, taille minimale, fils, recombinaison et état de réparation".to_string());}
    Ok(rows)
}

fn find(parents:&mut HashMap<String,String>,key:&str)->String {
    parents.entry(key.to_string()).or_insert_with(||key.to_string());
    let mut current=key.to_string();let mut visited=Vec::new();
    loop {let parent=parents[&current].clone();if parent==current {break;}visited.push(current);current=parent;}
    for key in visited {parents.insert(key,current.clone());}current
}
fn join(parents:&mut HashMap<String,String>,a:&str,b:&str) {
    let a=find(parents,a);let b=find(parents,b);
    if a!=b {let (smaller,larger)=if a<b {(a,b)}else{(b,a)};parents.insert(larger,smaller);}
}
fn split(rows:&[Row],seed:u64)->Result<(Vec<usize>,Vec<usize>)> {
    let mut parents=HashMap::new();
    for row in rows {
        let geom=format!("geometry:{}",row.geometry_id);find(&mut parents,&geom);
        if let Some(family)=&row.family {join(&mut parents,&geom,&format!("family:{family}"));}
    }
    let mut components:BTreeMap<String,Vec<usize>>=BTreeMap::new();
    for (index,row) in rows.iter().enumerate() {components.entry(find(&mut parents,&format!("geometry:{}",row.geometry_id))).or_default().push(index);}
    if components.len()<2 {return Err("Au moins deux familles ou géométries indépendantes sont nécessaires pour valider".to_string());}
    let mut rng=Rng::new(seed);let mut reserved=BTreeSet::new();
    for mode in ["planar","surface","volume"] {
        let mut eligible:Vec<String>=components.iter().filter(|(_,indices)|indices.iter().all(|i|rows[*i].mode==mode)).map(|(key,_)|key.clone()).collect();
        if eligible.len()>=2 {rng.shuffle(&mut eligible);reserved.insert(eligible[0].clone());}
    }
    if reserved.is_empty() {let keys:Vec<_>=components.keys().cloned().collect();reserved.insert(keys[(rng.next()%keys.len() as u64)as usize].clone());}
    if reserved.len()==components.len() {if let Some(key)=reserved.iter().next_back().cloned(){reserved.remove(&key);}}
    let mut training=Vec::new();let mut validation=Vec::new();
    for (key,indices) in components {if reserved.contains(&key) {validation.extend(indices);}else{training.extend(indices);}}
    Ok((training,validation))
}

fn reward(report:&Value)->Result<f64> {
    let minimum=finite(&report["quality"]["minimum"],"Qualité minimale")?;
    let mean=finite(&report["quality"]["mean"],"Qualité moyenne")?;
    if minimum<=0.0||mean<=0.0||minimum>1.0+1e-8||mean>1.0+1e-8 {return Err("Qualité non positive ou hors plage".to_string());}
    Ok(0.5*minimum+0.5*mean)
}

fn collect(rows:&mut[Row],directory:&Path,library:&str)->Result<usize> {
    let mut cache:HashMap<String,Vec<Value>>=HashMap::new();
    for (index,row) in rows.iter_mut().enumerate() {
        if !cache.contains_key(&row.state_id) {
            let mut evaluations=Vec::new();
            for algorithm in ALGORITHMS {
                let mut project=row.project.clone();project["mesh"]["algorithm"]=json!(algorithm);
                let output=directory.join(format!("case_{index:03}")).join(algorithm);
                let started=Instant::now();
                let request=json!({"operation":"mesh","gmsh_library":library,"project":project,"output_dir":output});
                let evaluated=crate::backend::run(&request).and_then(|report|reward(&report).map(|score|(report,score)));
                let record=match evaluated {
                    Ok((report,score))=>json!({"algorithm":algorithm,"reward":score,"success":true,"seconds":started.elapsed().as_secs_f64(),"quality":report["quality"],"algorithms":report["algorithms"],"mesh_settings":report["mesh_settings"],"backend":report["backend"],"units":report["units"]}),
                    Err(error)=>json!({"algorithm":algorithm,"reward":0.0,"success":false,"seconds":started.elapsed().as_secs_f64(),"error":error}),
                };
                evaluations.push(record);
            }
            cache.insert(row.state_id.clone(),evaluations);
        }
        row.evaluations=cache[&row.state_id].clone();
        for j in 0..3 {row.rewards[j]=finite(&row.evaluations[j]["reward"],"récompense")?;}
    }
    Ok(cache.len()*3)
}

#[derive(Clone)]
struct Network {input:[[f64;8];WIDTH],hidden:[f64;WIDTH],output:[[f64;WIDTH];3],bias:[f64;3]}
impl Network {
    fn forward(&self,features:&[f64;8])->([f64;WIDTH],[f64;3]) {
        let mut hidden=[0.0;WIDTH];let mut scores=[0.0;3];
        for k in 0..WIDTH {hidden[k]=(self.hidden[k]+sum(self.input[k].iter().zip(features).map(|(w,x)|w*x))).tanh();}
        for j in 0..3 {scores[j]=self.bias[j]+sum(self.output[j].iter().zip(hidden.iter()).map(|(w,x)|w*x));}
        (hidden,scores)
    }
    fn value(&self)->Value {json!({"input_weights":self.input,"hidden_bias":self.hidden,"output_weights":self.output,"output_bias":self.bias})}
}
fn best(values:&[f64;3])->usize {let mut chosen=0;for j in 1..3 {if values[j]>values[chosen] {chosen=j;}}chosen}
fn fit(rows:&[Row],training:&[usize],epochs:usize,seed:u64)->Result<(Network,usize)> {
    if training.is_empty() {return Err("Entraînement vide".to_string());}
    let mut averages=[0.0;3];
    for j in 0..3 {averages[j]=sum(training.iter().map(|i|rows[*i].rewards[j]))/training.len()as f64;}
    let mut rng=Rng::new(seed);
    let mut model=Network{input:[[0.0;8];WIDTH],hidden:[0.0;WIDTH],output:[[0.0;WIDTH];3],bias:averages};
    for row in &mut model.input {for weight in row {*weight=-0.35+0.7*rng.unit();}}
    for row in &mut model.output {for weight in row {*weight=-0.04+0.08*rng.unit();}}
    let mut order=training.to_vec();
    for epoch in 0..epochs {
        rng.shuffle(&mut order);let rate=0.06/(1.0+epoch as f64/epochs as f64);
        for index in &order {
            let row=&rows[*index];let (hidden,predicted)=model.forward(&row.features);
            let mut errors=[0.0;3];for j in 0..3 {errors[j]=2.0*(predicted[j]-row.rewards[j])/3.0;}
            let mut hidden_errors=[0.0;WIDTH];
            for k in 0..WIDTH {hidden_errors[k]=sum((0..3).map(|j|errors[j]*model.output[j][k]))*(1.0-hidden[k]*hidden[k]);}
            for j in 0..3 {
                model.bias[j]-=rate*errors[j];
                for k in 0..WIDTH {model.output[j][k]-=rate*(errors[j]*hidden[k]+1e-5*model.output[j][k]);}
            }
            for k in 0..WIDTH {
                model.hidden[k]-=rate*hidden_errors[k];
                for j in 0..8 {model.input[k][j]-=rate*(hidden_errors[k]*row.features[j]+1e-5*model.input[k][j]);}
            }
        }
    }
    Ok((model,best(&averages)))
}
fn mse(model:&Network,rows:&[Row],indices:&[usize])->f64 {
    sum(indices.iter().flat_map(|i|{let prediction=model.forward(&rows[*i].features).1;(0..3).map(move|j|(prediction[j]-rows[*i].rewards[j]).powi(2))}))/(indices.len()*3)as f64
}
fn strings(rows:&[Row],indices:&[usize],field:&str)->Vec<String> {
    let mut set=BTreeSet::new();
    for i in indices {let row=&rows[*i];match field {
        "geometry"=>{set.insert(row.geometry_id.clone());},"family"=>if let Some(family)=&row.family {set.insert(family.clone());},
        "mode"=>{set.insert(row.mode.clone());},"kind"=>{set.insert(row.project["geometry"]["kind"].as_str().unwrap().to_string());},_=>{},
    }}set.into_iter().collect()
}
fn atomic_json(path:&Path,value:&Value)->Result<()> {
    let data=serde_json::to_vec_pretty(value).map_err(|e|e.to_string())?;
    let temporary=path.with_extension(format!("{}.tmp",std::process::id()));
    fs::write(&temporary,data).map_err(|e|e.to_string())?;
    fs::rename(&temporary,path).map_err(|e|e.to_string())
}

/// Evaluate, train and validate a real native portfolio without Python fallback.
pub fn train(request:&Value)->Result<Value> {
    let started=Instant::now();
    let output=PathBuf::from(request["output_dir"].as_str().ok_or("Dossier d'entraînement absent")?);
    let library=request["gmsh_library"].as_str().ok_or("Bibliothèque Gmsh absente")?;
    let epochs=integer(request,"epochs",200,1,200)? as usize;
    let seed=integer(request,"seed",42,0,u32::MAX as u64)?;
    let mut rows=normalize_cases(request)?;
    let (training,validation)=split(&rows,seed)?;
    fs::create_dir_all(&output).map_err(|e|e.to_string())?;
    let evaluation_started=Instant::now();let executed_actions=collect(&mut rows,&output.join("evaluations"),library)?;
    let evaluation_seconds=evaluation_started.elapsed().as_secs_f64();
    if !training.iter().any(|i|rows[*i].evaluations.iter().any(|e|e["success"]==true)) {
        return Err("Aucun algorithme n'a produit d'exemple d'entraînement valide".to_string());
    }
    let fitting_started=Instant::now();let (network,baseline)=fit(&rows,&training,epochs,seed)?;
    let fitting_seconds=fitting_started.elapsed().as_secs_f64();
    let chosen:Vec<usize>=validation.iter().map(|i|best(&network.forward(&rows[*i].features).1)).collect();
    let guide_reward=sum(validation.iter().zip(chosen.iter()).map(|(i,j)|rows[*i].rewards[*j]))/validation.len()as f64;
    let fixed_reward=sum(validation.iter().map(|i|rows[*i].rewards[baseline]))/validation.len()as f64;
    let ideal_reward=sum(validation.iter().map(|i|rows[*i].rewards[best(&rows[*i].rewards)]))/validation.len()as f64;
    let improvement=guide_reward-fixed_reward;
    let selected_successful=validation.iter().zip(chosen.iter()).all(|(i,j)|rows[*i].evaluations[*j]["success"]==true);
    let baseline_successful=rows.iter().all(|row|row.evaluations[baseline]["success"]==true);
    let enabled=improvement>1e-4&&selected_successful;
    let ranges:Vec<[f64;2]>=(0..8).map(|j|[training.iter().map(|i|rows[*i].features[j]).fold(f64::INFINITY,f64::min),training.iter().map(|i|rows[*i].features[j]).fold(f64::NEG_INFINITY,f64::max)]).collect();
    let mut model=network.value();
    let fields=json!({"schema":"atelier.advisor.v1","schema_version":1,"algorithms":ALGORITHMS,"features":FEATURES,
        "gmsh_version":ENGINE,"epochs":epochs,"seed":seed,"input_unit":rows[0].project["units"]["input"],"mesh_signature":signature(&rows[0].project)?,
        "baseline_algorithm":ALGORITHMS[baseline],"baseline_successful":baseline_successful,"deploy_enabled":enabled,
        "validated_modes":strings(&rows,&validation,"mode"),"trained_kinds":strings(&rows,&training,"kind"),"feature_ranges":ranges,
        "training_geometry_ids":strings(&rows,&training,"geometry"),"validation_geometry_ids":strings(&rows,&validation,"geometry"),
        "training_families":strings(&rows,&training,"family"),"validation_families":strings(&rows,&validation,"family"),
        "training_configs":training.iter().map(|i|rows[*i].project.clone()).collect::<Vec<_>>(),
        "validation_guide_reward":guide_reward,"validation_fixed_reward":fixed_reward,"improvement":improvement,
        "computation_backend":"rust","training_method":"native_supervised_mlp_sgd","rng":"xorshift64star_v1"});
    model.as_object_mut().unwrap().extend(fields.as_object().unwrap().iter().map(|(k,v)|(k.clone(),v.clone())));
    // Reuse deployment validation before committing the model. A disabled guide
    // is still schema checked; its prediction is not forced into production.
    crate::advisor::choose(&rows[training[0]].project,&model)?;
    let evaluations:Vec<Value>=rows.iter().map(|row|json!({"name":row.name,"family":row.family,"mode":row.mode,"geometry_id":row.geometry_id,"state_id":row.state_id,
        "project":row.project,"features":row.features,"rewards":row.rewards,"evaluations":row.evaluations})).collect();
    let successful=rows.iter().flat_map(|r|r.evaluations.iter()).filter(|v|v["success"]==true).count();
    let mut notes=vec!["Toutes les évaluations et l'optimisation des poids sont exécutées dans le noyau Rust.",
        "Le réseau choisit parmi des algorithmes Gmsh existants ; il ne génère pas directement des éléments.",
        "Les géométries exactes et familles déclarées restent ensemble avant la séparation.",
        "Les récompenses réservées ne modifient ni les poids, ni le nombre d'époques, ni le choix de référence fixe.",
        "La validation sert de garde de déploiement ; elle ne remplace pas un test final indépendant.",
        "Le corpus synthétique livré est réduit ; aucune généralisation industrielle n'est garantie.",
        "Les échecs ont une récompense nulle et restent présents dans les résultats.",
        "Les temps sont mesurés et ne font pas partie de la récompense.",
        "Des noms d'algorithmes peuvent utiliser le même moteur effectif ; les identifiants réels sont conservés."];
    if !enabled {notes.push("Le guide n'est pas activé : aucun avantage réservé suffisant et valide n'a été démontré.");}
    let report=json!({"schema":"atelier.advisor.report.v1","method":"native_supervised_mlp_portfolio","computation_backend":"rust",
        "training_cases":training.len(),"validation_cases":validation.len(),"algorithms":ALGORITHMS,"features":FEATURES,
        "families":{"training":model["training_families"],"validation":model["validation_families"]},
        "training_geometry_ids":model["training_geometry_ids"],"validation_geometry_ids":model["validation_geometry_ids"],
        "validation_guide_reward":guide_reward,"validation_fixed_reward":fixed_reward,"validation_best_reward":ideal_reward,
        "improvement":improvement,"deploy_enabled":enabled,"baseline_algorithm":ALGORITHMS[baseline],"baseline_successful":baseline_successful,
        "selected_validation_actions_successful":selected_successful,"training_mse":mse(&network,&rows,&training),"validation_mse":mse(&network,&rows,&validation),
        "epochs":epochs,"seed":seed,"rng":"xorshift64star_v1","gmsh_version":ENGINE,"input_unit":model["input_unit"],"mesh_signature":model["mesh_signature"],
        "model_path":output.join("guide.json"),"reward":"0.5 * minSICN + 0.5 * meanSICN; échec = 0",
        "evaluated_actions":rows.len()*3,"executed_backend_evaluations":executed_actions,"reused_action_labels":rows.len()*3-executed_actions,"successful_actions":successful,"failed_actions":rows.len()*3-successful,
        "evaluations":evaluations,"notes":notes,"timings":{"evaluation_seconds":evaluation_seconds,"fitting_seconds":fitting_seconds,"total_seconds":started.elapsed().as_secs_f64()}});
    atomic_json(&output.join("guide.json"),&model)?;atomic_json(&output.join("report.json"),&report)?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;
    fn rows()->Vec<Row> {normalize_cases(&json!({})).unwrap()}
    fn label(rows:&mut[Row]) {
        for (i,row) in rows.iter_mut().enumerate() {
            let x=row.features[1];row.rewards=[0.2+0.5*x,0.8-0.3*x,0.5+0.02*(i%3)as f64];
            row.evaluations=row.rewards.iter().map(|q|json!({"success":true,"reward":q})).collect();
        }
    }
    #[test]
    fn defaults_are_bounded_and_stratified_without_geometry_or_family_leakage() {
        let rows=rows();assert_eq!(rows.len(),16);let (train,validation)=split(&rows,42).unwrap();
        assert!(!train.is_empty()&&!validation.is_empty());
        for field in ["geometry","family"] {
            let a:HashSet<_>=strings(&rows,&train,field).into_iter().collect();
            assert!(strings(&rows,&validation,field).iter().all(|id|!a.contains(id)));
        }
        assert_eq!(strings(&rows,&validation,"mode"),vec!["planar","surface","volume"]);
        for row in rows {assert!(row.features.iter().all(|x|x.is_finite()&&(0.0..=1.0).contains(x)));assert!(row.project["mesh"]["max_elements"].as_u64().unwrap()<=5000);}
    }
    #[test]
    fn same_geometry_under_two_family_labels_unites_components() {
        let base=default_cases()[0]["project"].clone();let other=default_cases()[2]["project"].clone();
        let rows=normalize_cases(&json!({"cases":[{"family":"first","project":base},{"family":"second","project":base},{"family":"second","project":other}]})).unwrap();
        assert!(split(&rows,1).is_err());
    }
    #[test]
    fn identities_include_shape_and_mesh_parameters_but_ignore_ring_start() {
        let p=json!({"mode":"planar","geometry":{"kind":"polygon","outer":[[0,0],[4,0],[4,3],[0,3]]},"mesh":{"size":0.8}});
        let mut shift=p.clone();shift["geometry"]["outer"]=json!([[4,3],[4,0],[0,0],[0,3],[4,3]]);
        let mut changed=p.clone();changed["geometry"]["outer"][1]=json!([5,0]);
        let rows=normalize_cases(&json!({"cases":[{"project":p},{"project":shift},{"project":changed}]})).unwrap();
        assert_eq!(rows[0].geometry_id,rows[1].geometry_id);assert_eq!(rows[0].state_id,rows[1].state_id);
        assert_eq!(rows[0].project["geometry"],rows[1].project["geometry"]);
        assert_ne!(rows[0].geometry_id,rows[2].geometry_id);
        let mut request=json!({"cases":[{"project":rows[0].project},{"project":rows[0].project}]});request["cases"][1]["project"]["mesh"]["size"]=json!(1.0);
        let sized=normalize_cases(&request).unwrap();assert_eq!(sized[0].geometry_id,sized[1].geometry_id);assert_ne!(sized[0].state_id,sized[1].state_id);
    }
    #[test]
    fn gui_mesh_configuration_and_units_are_used_without_changing_physical_default_geometry() {
        let rows=normalize_cases(&json!({"training_units":"m","training_mesh":{"min_size":0.00018,"order":2,"threads":2,"recombine":false,"max_elements":9000}})).unwrap();
        assert_eq!(rows[0].project["units"]["input"],"m");
        assert!((rows[0].project["geometry"]["width"].as_f64().unwrap()-0.004).abs()<1e-15);
        assert_eq!(rows[0].project["mesh"]["min_size"],0.00018);assert_eq!(rows[0].project["mesh"]["order"],2);
        assert_eq!(rows[0].project["mesh"]["threads"],2);assert_eq!(rows[0].project["mesh"]["max_elements"],5000);
    }
    #[test]
    fn equivalent_unit_representations_share_identity_and_executed_hole_order() {
        let mm=json!({"mode":"planar","units":{"input":"mm"},"geometry":{"kind":"plate","width":20,"height":10,"holes":[{"x":3,"y":5,"radius":1},{"x":10,"y":5,"radius":1}]},"mesh":{"size":2,"min_size":0.5}});
        let m=json!({"mode":"planar","units":{"input":"m"},"geometry":{"kind":"plate","width":0.02,"height":0.01,"holes":[{"x":0.003,"y":0.005,"radius":0.001},{"x":0.01,"y":0.005,"radius":0.001}]},"mesh":{"size":0.002,"min_size":0.0005}});
        let rows=normalize_cases(&json!({"cases":[{"project":mm},{"project":m}]})).unwrap();
        assert_eq!(rows[0].geometry_id,rows[1].geometry_id);assert_eq!(rows[0].state_id,rows[1].state_id);
        assert_eq!(rows[0].project["geometry"],rows[1].project["geometry"]);
    }
    #[test]
    fn heldout_label_changes_cannot_change_fitted_weights_or_baseline() {
        let mut rows=rows();label(&mut rows);let (train,validation)=split(&rows,42).unwrap();
        let first=fit(&rows,&train,50,42).unwrap();
        for i in validation {rows[i].rewards=[0.99,0.01,0.3];}
        let second=fit(&rows,&train,50,42).unwrap();assert_eq!(first.0.value(),second.0.value());assert_eq!(first.1,second.1);
    }
    #[test]
    fn fitting_is_deterministic_and_improves_training_error() {
        let mut rows=rows();label(&mut rows);let (train,_)=split(&rows,42).unwrap();
        let before=fit(&rows,&train,1,9).unwrap();let after=fit(&rows,&train,120,9).unwrap();let repeated=fit(&rows,&train,120,9).unwrap();
        assert_eq!(after.0.value(),repeated.0.value());assert!(mse(&after.0,&rows,&train)<mse(&before.0,&rows,&train));
    }
    #[test]
    fn malformed_cases_and_unrepresented_controls_are_rejected() {
        assert!(normalize_cases(&json!({"cases":[]})).is_err());
        let mut p=default_cases()[0]["project"].clone();p["local_sizes"]=json!([{"dim":2,"tag":1,"size":0.1}]);
        assert!(normalize_cases(&json!({"cases":[{"project":p},{"project":p}]})).is_err());
        assert!(normalize_cases(&json!({"training_mesh":{"order":3}})).is_err());
        assert!(normalize_cases(&json!({"training_units":"feet"})).is_err());
    }
    #[test]
    fn reward_does_not_turn_invalid_elements_into_good_labels() {
        assert!(reward(&json!({"quality":{"minimum":-0.1,"mean":0.8}})).is_err());
        assert!(reward(&json!({"quality":{"minimum":0.1,"mean":1.1}})).is_err());
        assert!((reward(&json!({"quality":{"minimum":0.2,"mean":0.8}})).unwrap()-0.5).abs()<1e-15);
    }
}
