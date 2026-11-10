//! Checked inference of the legacy strategy MLP, entirely in the Rust kernel.
//!
//! This module does not train a network. A model is used only if its recorded
//! held-out comparison enabled deployment and the current request is inside
//! its supported geometric and numerical domain.

use serde_json::{json, Value};
use std::collections::HashSet;

const ALGORITHMS: [&str;3] = ["automatic","delaunay","frontal"];
const FEATURES: [&str;8] = ["mode","thin_extent","middle_extent","relative_size","hole_fraction","curved","relative_corner_size","boundary_complexity"];
const GMSH_VERSION: &str = "4.15.2";
const MIN_IMPROVEMENT: f64 = 1e-4;

fn number(value: &Value, label: &str) -> Result<f64,String> {
    value.as_f64().filter(|x| x.is_finite()).ok_or_else(|| format!("{label} : nombre fini attendu"))
}

fn number_or(object: &Value, key: &str, default: f64) -> Result<f64,String> {
    match object.get(key) { Some(value) => number(value,key), None => Ok(default) }
}

fn positive(value: f64, label: &str) -> Result<f64,String> {
    if value > 0.0 && value.is_finite() { Ok(value) }
    else { Err(format!("{label} : nombre fini strictement positif attendu")) }
}

fn integer_or(object: &Value, key: &str, default: u64, maximum: u64) -> Result<u64,String> {
    let result = match object.get(key) {
        Some(value) => value.as_u64().ok_or_else(|| format!("{key} : entier positif attendu"))?,
        None => default,
    };
    if result == 0 || result > maximum { return Err(format!("{key} hors plage")); }
    Ok(result)
}

fn boolean_or(object: &Value, key: &str, default: bool) -> Result<bool,String> {
    match object.get(key) {
        Some(value) => value.as_bool().ok_or_else(|| format!("{key} : booléen attendu")),
        None => Ok(default),
    }
}

fn vector(value: Option<&Value>, length: usize, label: &str) -> Result<Vec<f64>,String> {
    let values = value.and_then(Value::as_array).filter(|x| x.len()==length)
        .ok_or_else(|| format!("{label} : dimensions invalides"))?;
    values.iter().map(|value| {
        let x = number(value,label)?;
        if x.abs() > 1e6 { Err(format!("{label} : poids hors plage")) } else { Ok(x) }
    }).collect()
}

fn string_list(model: &Value, key: &str) -> Result<Vec<String>,String> {
    let rows = model.get(key).and_then(Value::as_array).ok_or_else(|| format!("Provenance {key} absente"))?;
    rows.iter().map(|item| item.as_str().map(str::to_string).ok_or_else(|| format!("Provenance {key} invalide"))).collect()
}

struct Model {
    input_weights: Vec<Vec<f64>>,
    hidden_bias: Vec<f64>,
    output_weights: Vec<Vec<f64>>,
    output_bias: Vec<f64>,
    ranges: Vec<[f64;2]>,
    modes: Vec<String>,
    kinds: Vec<String>,
    baseline: String,
    enabled: bool,
}

fn validate_model(model: &Value) -> Result<Model,String> {
    if !model.is_object() || model.get("schema").and_then(Value::as_str) != Some("atelier.advisor.v1")
        || model.get("schema_version").and_then(Value::as_u64) != Some(1) {
        return Err("Format de guide incompatible".to_string());
    }
    if model.get("algorithms") != Some(&json!(ALGORITHMS)) || model.get("features") != Some(&json!(FEATURES)) {
        return Err("Entrées ou algorithmes du guide incompatibles".to_string());
    }
    let width = model.get("hidden_bias").and_then(Value::as_array).map(Vec::len).filter(|n| (1..=128).contains(n))
        .ok_or("Taille du réseau invalide")?;
    let hidden_bias = vector(model.get("hidden_bias"),width,"hidden_bias")?;
    let output_bias = vector(model.get("output_bias"),3,"output_bias")?;
    let matrix = |key:&str,height:usize,length:usize| -> Result<Vec<Vec<f64>>,String> {
        let rows = model.get(key).and_then(Value::as_array).filter(|x| x.len()==height)
            .ok_or_else(|| format!("{key} : dimensions invalides"))?;
        rows.iter().map(|row| vector(Some(row),length,key)).collect()
    };
    let input_weights = matrix("input_weights",width,8)?;
    let output_weights = matrix("output_weights",3,width)?;
    let baseline = model.get("baseline_algorithm").and_then(Value::as_str).filter(|x| ALGORITHMS.contains(x))
        .ok_or("Algorithme fixe inconnu")?.to_string();
    let enabled = model.get("deploy_enabled").and_then(Value::as_bool).ok_or("Indicateur de déploiement invalide")?;
    model.get("baseline_successful").and_then(Value::as_bool).ok_or("Indicateur de référence invalide")?;
    let signature = model.get("mesh_signature").filter(|x| x.is_object()).ok_or("Configuration du guide absente")?;
    validate_signature(signature)?;
    let rows = model.get("feature_ranges").and_then(Value::as_array).filter(|x| x.len()==8).ok_or("Domaine de caractéristiques invalide")?;
    let mut ranges = Vec::with_capacity(8);
    for row in rows {
        let limits = vector(Some(row),2,"feature_ranges")?;
        if limits[0] < 0.0 || limits[0] > limits[1] || limits[1] > 1.0 {
            return Err("Domaine de caractéristiques invalide".to_string());
        }
        ranges.push([limits[0],limits[1]]);
    }
    let training = string_list(model,"training_geometry_ids")?;
    let validation = string_list(model,"validation_geometry_ids")?;
    for id in training.iter().chain(validation.iter()) {
        if id.len()!=64 || !id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
            return Err("Identité géométrique du guide invalide".to_string());
        }
    }
    let train_families = string_list(model,"training_families")?;
    let valid_families = string_list(model,"validation_families")?;
    let separate = |a:&[String],b:&[String]| {
        let first: HashSet<&str> = a.iter().map(String::as_str).collect();
        !b.iter().any(|x| first.contains(x.as_str()))
    };
    if !separate(&training,&validation) || !separate(&train_families,&valid_families) {
        return Err("Fuite entre apprentissage et validation dans le guide".to_string());
    }
    let modes = string_list(model,"validated_modes")?;
    let kinds = string_list(model,"trained_kinds")?;
    if modes.iter().any(|mode| !["planar","surface","volume"].contains(&mode.as_str())) {
        return Err("Mode de validation inconnu".to_string());
    }
    if kinds.iter().any(|kind| !["plate","polygon","box","sphere","bracket"].contains(&kind.as_str())) {
        return Err("Géométrie d'apprentissage inconnue".to_string());
    }
    if model.get("gmsh_version").and_then(Value::as_str).filter(|x| !x.is_empty()).is_none() {
        return Err("Version du moteur absente".to_string());
    }
    let guide_reward = number(&model["validation_guide_reward"],"validation_guide_reward")?;
    let fixed_reward = number(&model["validation_fixed_reward"],"validation_fixed_reward")?;
    let improvement = number(&model["improvement"],"improvement")?;
    if (improvement-(guide_reward-fixed_reward)).abs()>1e-12 {
        return Err("Comparaison de validation incohérente".to_string());
    }
    if enabled && improvement <= MIN_IMPROVEMENT {
        return Err("Déploiement non justifié par la comparaison réservée".to_string());
    }
    Ok(Model {input_weights,hidden_bias,output_weights,output_bias,ranges,modes,kinds,baseline,enabled})
}

fn validate_signature(signature:&Value) -> Result<(),String> {
    let order = integer_or(signature,"order",1,2)?;
    if order != 1 && order != 2 { return Err("Ordre du guide invalide".to_string()); }
    integer_or(signature,"threads",1,64)?;
    positive(number_or(signature,"min_size",0.1)?,"min_size")?;
    boolean_or(signature,"recombine",false)?;
    boolean_or(signature,"repair_enabled",false)?;
    for key in ["order","threads","min_size","recombine","repair_enabled"] {
        if signature.get(key).is_none() { return Err(format!("Paramètre {key} absent du guide")); }
    }
    if signature.as_object().map(|x| x.len()) != Some(5) {
        return Err("Paramètres supplémentaires non reconnus dans la configuration du guide".to_string());
    }
    Ok(())
}

fn mesh_signature(project:&Value) -> Result<Value,String> {
    let mesh = &project["mesh"];
    if !mesh.is_null() && !mesh.is_object() { return Err("Réglages de maillage invalides".to_string()); }
    let repair = &project["repair"];
    if !repair.is_null() && !repair.is_object() { return Err("Réglages de réparation invalides".to_string()); }
    let size = positive(number_or(mesh,"size",0.5)?,"size")?;
    let min_size = positive(number_or(mesh,"min_size",0.1_f64.min(size))?,"min_size")?;
    if min_size > size { return Err("La taille minimale dépasse la taille cible".to_string()); }
    Ok(json!({"order":integer_or(mesh,"order",1,2)?,"threads":integer_or(mesh,"threads",1,64)?,
              "min_size":min_size,"recombine":boolean_or(mesh,"recombine",false)?,
              "repair_enabled":boolean_or(repair,"enabled",false)?}))
}

fn same_signature(a:&Value,b:&Value) -> bool {
    ["order","threads","min_size"].iter().all(|key| a[*key].as_f64()==b[*key].as_f64())
        && ["recombine","repair_enabled"].iter().all(|key| a[*key].as_bool()==b[*key].as_bool())
}

fn ring(value:&Value) -> Result<Vec<[f64;2]>,String> {
    let rows = value.as_array().filter(|x| (3..=5001).contains(&x.len())).ok_or("Contour polygonal invalide")?;
    let mut points = Vec::with_capacity(rows.len());
    for row in rows {
        let coords = row.as_array().filter(|x|x.len()==2).ok_or("Chaque sommet demande deux coordonnées")?;
        points.push([number(&coords[0],"x")?,number(&coords[1],"y")?]);
    }
    if points.len()>1 && points.first()==points.last() { points.pop(); }
    if points.len()<3 { return Err("Contour dégénéré".to_string()); }
    Ok(points)
}

pub(crate) fn extract_features(project:&Value) -> Result<[f64;8],String> {
    let mode = project.get("mode").and_then(Value::as_str).unwrap_or("planar");
    let g = &project["geometry"];
    let kind = g.get("kind").and_then(Value::as_str).unwrap_or("plate");
    let mut holes = 0_usize;
    let mut complexity = 4_usize;
    let (width,height,depth) = match kind {
        "polygon" => {
            let outer = ring(&g["outer"])?;
            let xmin = outer.iter().map(|p|p[0]).fold(f64::INFINITY,f64::min);
            let xmax = outer.iter().map(|p|p[0]).fold(f64::NEG_INFINITY,f64::max);
            let ymin = outer.iter().map(|p|p[1]).fold(f64::INFINITY,f64::min);
            let ymax = outer.iter().map(|p|p[1]).fold(f64::NEG_INFINITY,f64::max);
            complexity=outer.len();
            if let Some(rows) = g.get("holes") {
                let rows=rows.as_array().ok_or("Liste de trous attendue")?;
                holes=rows.len();
                for row in rows { complexity+=ring(row)?.len(); }
            }
            (positive(xmax-xmin,"largeur")?,positive(ymax-ymin,"hauteur")?,positive(number_or(g,"thickness",2.0)?,"épaisseur")?)
        },
        "sphere" => {
            let diameter=2.0*positive(number_or(g,"radius",3.0)?,"rayon")?;
            complexity=1;
            (diameter,diameter,diameter)
        },
        "plate" | "box" | "bracket" => {
            if kind=="plate" {
                if let Some(rows)=g.get("holes") {
                    let rows=rows.as_array().ok_or("Liste de trous attendue")?;
                    if rows.iter().any(|row|!row.is_object()) { return Err("Définition de perçage invalide".to_string()); }
                    holes=rows.len();
                }
                complexity+=holes*4;
            }
            if kind=="bracket" {
                let radius=number_or(g,"throughhole_radius",0.0)?;
                if radius<0.0 { return Err("Rayon de perçage négatif".to_string()); }
                holes=usize::from(radius>0.0); complexity=8+4*holes;
            }
            (positive(number_or(g,"width",10.0)?,"largeur")?,
             positive(number_or(g,"height",if kind=="bracket" {8.0} else {6.0})?,"hauteur")?,
             positive(number_or(g,if kind=="plate" {"thickness"} else {"depth"},if kind=="plate" {2.0} else {4.0})?,"profondeur")?)
        },
        _ => return Err("Géométrie native non prise en charge par le guide".to_string()),
    };
    let mut dimensions=vec![width,height];
    if mode=="volume" || ["box","sphere","bracket"].contains(&kind) { dimensions.push(depth); }
    dimensions.sort_by(|a,b|a.total_cmp(b));
    let extent=*dimensions.last().unwrap();
    if !extent.is_finite() || extent<=0.0 { return Err("Étendue géométrique hors plage pour le guide".to_string()); }
    let size=positive(number_or(&project["mesh"],"size",0.5)?,"taille")?;
    let corner=if kind=="plate" { number_or(g,"corner_radius",0.0)? } else {0.0};
    let chamfer=if kind=="plate" { number_or(g,"chamfer",0.0)? } else {0.0};
    if corner<0.0 || chamfer<0.0 { return Err("Congé ou chanfrein négatif".to_string()); }
    let ratio=size/extent;
    let mode_feature=match mode {"planar"=>0.0,"surface"=>0.5,"volume"=>1.0,_=>return Err("Mode inconnu".to_string())};
    let mut result=[mode_feature,dimensions[0]/extent,dimensions[dimensions.len()-2]/extent,
        if ratio.is_finite() {ratio/(1.0+ratio)} else {1.0},
        holes as f64/(holes as f64+4.0),
        if kind=="sphere" || corner>0.0 || holes>0 && kind!="polygon" {1.0} else {0.0},
        (corner.max(chamfer)/extent).min(1.0),complexity as f64/(complexity as f64+12.0)];
    for value in &mut result { *value=value.clamp(0.0,1.0); }
    Ok(result)
}

fn precise_sum(values: impl Iterator<Item=f64>) -> f64 {
    let (mut sum,mut correction)=(0.0,0.0);
    for value in values {
        let adjusted=value-correction; let next=sum+adjusted;
        correction=(next-sum)-adjusted; sum=next;
    }
    sum
}

fn predict(model:&Model,features:&[f64;8]) -> [f64;3] {
    let hidden:Vec<f64>=model.input_weights.iter().zip(model.hidden_bias.iter())
        .map(|(row,bias)| (bias+precise_sum(row.iter().zip(features).map(|(w,x)|w*x))).tanh()).collect();
    let mut output=[0.0;3];
    for j in 0..3 {
        output[j]=model.output_bias[j]+precise_sum(model.output_weights[j].iter().zip(hidden.iter()).map(|(w,x)|w*x));
    }
    output
}

fn active(value:Option<&Value>) -> bool {
    match value {None|Some(Value::Null)=>false,Some(Value::Array(x))=>!x.is_empty(),Some(Value::Object(x))=>!x.is_empty(),_=>true}
}

fn transformed(value:Option<&Value>) -> bool {
    let Some(value)=value else{return false};
    if value.is_null(){return false;}
    let Some(object)=value.as_object() else{return true};
    if object.keys().any(|k| !["scale","translation","rotation_degrees","rotation_axis"].contains(&k.as_str())) {return true;}
    if value.get("scale").is_some_and(|v|v.as_f64()!=Some(1.0)) {return true;}
    if value.get("rotation_degrees").is_some_and(|v|v.as_f64()!=Some(0.0)) {return true;}
    if let Some(delta)=value.get("translation") {
        if !delta.as_array().is_some_and(|a|a.len()==3&&a.iter().all(|v|v.as_f64()==Some(0.0))) {return true;}
    }
    false
}

/// Preserve explicit user settings unless all deployment and domain checks pass.
pub fn choose(project:&Value,model:&Value) -> Result<Value,String> {
    if !project.is_object() { return Err("Projet JSON attendu".to_string()); }
    let requested=project["mesh"].get("algorithm").and_then(Value::as_str).unwrap_or("automatic");
    if !ALGORITHMS.contains(&requested) { return Err("Algorithme demandé inconnu".to_string()); }
    let mode=project.get("mode").and_then(Value::as_str).unwrap_or("planar");
    if !["planar","surface","volume"].contains(&mode) { return Err("Mode inconnu".to_string()); }
    let kind=project["geometry"].get("kind").and_then(Value::as_str).unwrap_or("plate");
    let mut result=json!({"algorithm":requested,"use_model":false,"reason":"guide_unavailable","scores":{},"baseline_algorithm":null,"computation_backend":"rust"});
    let reason=if kind=="import" {Some("imported_geometry_outside_training_domain")}
        else if !["plate","polygon","box","sphere","bracket"].contains(&kind) {Some("geometry_family_outside_training_domain")}
        else if project.get("operation").and_then(Value::as_str)==Some("remesh") || active(project.get("remesh")) {Some("remeshing_outside_training_domain")}
        else if project["selection"].get("scope").and_then(Value::as_str).is_some_and(|x|x!="all") {Some("scoped_meshing_outside_training_domain")}
        else if active(project.get("local_sizes")) || active(project.get("correspondences"))
            || active(project.get("refinement_regions")) || active(project.get("critical_zones"))
            || active(project.get("quality_policy")) {Some("local_controls_outside_training_domain")}
        else if project["mesh"].get("smoothing").is_some_and(|v|v.as_u64()!=Some(1))
            || project["mesh"].get("optimize").is_some_and(|v|v.as_bool()!=Some(true))
            || project["mesh"].get("curvature_elements").is_some_and(|v|v.as_u64()!=Some(16)) {Some("meshing_configuration_outside_training_domain")}
        else if transformed(project["geometry"].get("transform")) {Some("geometry_transform_outside_training_domain")}
        else {None};
    if let Some(reason)=reason {result["reason"]=json!(reason);return Ok(result);}
    if model.is_null() {return Ok(result);}
    let checked=validate_model(model)?;
    if let Some(unit)=model.get("input_unit") {
        let unit=unit.as_str().filter(|v|["m","cm","mm","in"].contains(v)).ok_or("Unité du guide invalide")?;
        if project["units"].get("input").and_then(Value::as_str).unwrap_or("mm")!=unit {
            result["reason"]=json!("unit_system_outside_training_domain");return Ok(result);
        }
    }
    result["baseline_algorithm"]=json!(checked.baseline);
    if model["gmsh_version"].as_str()!=Some(GMSH_VERSION) {result["reason"]=json!("backend_version_changed");return Ok(result);}
    let signature=mesh_signature(project)?;
    if !same_signature(&signature,&model["mesh_signature"]) {result["reason"]=json!("meshing_configuration_outside_training_domain");return Ok(result);}
    if !checked.modes.iter().any(|x|x==mode) || !checked.kinds.iter().any(|x|x==kind) {
        result["reason"]=json!("geometry_family_outside_training_domain");return Ok(result);
    }
    if !checked.enabled {result["reason"]=json!("heldout_improvement_not_demonstrated");return Ok(result);}
    let features=extract_features(project)?;
    if features.iter().zip(checked.ranges.iter()).any(|(x,range)|*x<range[0]-0.05 || *x>range[1]+0.05) {
        result["reason"]=json!("shape_features_outside_training_domain");return Ok(result);
    }
    let scores=predict(&checked,&features);
    let mut best=0;
    for index in 1..3 {if scores[index]>scores[best] {best=index;}}
    result["algorithm"]=json!(ALGORITHMS[best]);result["use_model"]=json!(true);
    result["reason"]=json!("learned_policy_passed_heldout_gate");
    result["scores"]=json!({"automatic":scores[0],"delaunay":scores[1],"frontal":scores[2]});
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture()->Value {
        json!({"schema":"atelier.advisor.v1","schema_version":1,"algorithms":ALGORITHMS,"features":FEATURES,
            "input_weights":vec![vec![0.1;8],vec![-0.2;8]],"hidden_bias":[0.05,-0.1],
            "output_weights":[[0.2,0.3],[-0.4,0.1],[0.1,-0.5]],"output_bias":[0.2,0.6,0.1],
            "feature_ranges":vec![[0.0,1.0];8],"baseline_algorithm":"delaunay","baseline_successful":true,"deploy_enabled":true,
            "mesh_signature":{"order":1,"recombine":false,"min_size":0.1,"threads":1,"repair_enabled":false},
            "training_geometry_ids":["a".repeat(64)],"validation_geometry_ids":["b".repeat(64)],
            "training_families":["training"],"validation_families":["validation"],
            "validated_modes":["planar","surface","volume"],"trained_kinds":["plate","polygon","box","sphere","bracket"],
            "gmsh_version":"4.15.2","validation_guide_reward":0.8,"validation_fixed_reward":0.5,"improvement":0.3})
    }
    fn project()->Value {json!({"mode":"planar","geometry":{"kind":"plate","width":4,"height":3,"corner_radius":0.5,"holes":[{"x":2,"y":1.5,"radius":0.4}]},"mesh":{"size":0.8,"min_size":0.1,"algorithm":"automatic"}})}
    #[test]
    fn features_match_legacy_python_fixture() {
        let features=extract_features(&project()).unwrap();
        let expected=[0.0,0.75,0.75,1.0/6.0,0.2,1.0,0.125,0.4];
        for (a,b) in features.iter().zip(expected) {assert!((a-b).abs()<1e-14,"{a} != {b}");}
    }
    #[test]
    fn scores_match_independent_python_mlp_formula() {
        let result=choose(&project(),&fixture()).unwrap();
        // Captured independently from atelier.advisor.extract_features and
        // Python math.fsum/tanh for this serialized two-neuron fixture.
        let expected=[0.078603606660122,0.3865684574204373,0.46293868049942843];
        for (name,value) in ALGORITHMS.iter().zip(expected) {assert!((result["scores"][*name].as_f64().unwrap()-value).abs()<1e-14);}
        assert_eq!(result["algorithm"],"frontal");assert_eq!(result["use_model"],true);
    }
    #[test]
    fn disabled_model_preserves_explicit_algorithm() {
        let mut model=fixture();model["deploy_enabled"]=json!(false);
        let mut p=project();p["mesh"]["algorithm"]=json!("delaunay");
        let result=choose(&p,&model).unwrap();assert_eq!(result["algorithm"],"delaunay");
        assert_eq!(result["use_model"],false);assert_eq!(result["reason"],"heldout_improvement_not_demonstrated");
    }
    #[test]
    fn imported_and_local_requests_keep_user_choice() {
        for (field,value,reason) in [
            ("geometry",json!({"kind":"import"}),"imported_geometry_outside_training_domain"),
            ("selection",json!({"scope":"entities","tags":[2]}),"scoped_meshing_outside_training_domain"),
            ("local_sizes",json!([{"dim":2,"tag":1,"size":0.1}]),"local_controls_outside_training_domain"),
            ("correspondences",json!([{"master":1,"slave":2}]),"local_controls_outside_training_domain"),
            ("operation",json!("remesh"),"remeshing_outside_training_domain"),
        ] {
            let mut p=project();p[field]=value;let result=choose(&p,&fixture()).unwrap();
            assert_eq!(result["use_model"],false);assert_eq!(result["reason"],reason);
        }
        let mut p=project();p["geometry"]["transform"]=json!({"translation":[1,0,0]});
        assert_eq!(choose(&p,&fixture()).unwrap()["reason"],"geometry_transform_outside_training_domain");
    }
    #[test]
    fn changed_engine_or_configuration_does_not_extrapolate() {
        let mut model=fixture();model["gmsh_version"]=json!("4.16.0");
        assert_eq!(choose(&project(),&model).unwrap()["reason"],"backend_version_changed");
        for (key,value) in [("order",json!(2)),("threads",json!(2)),("min_size",json!(0.2)),("recombine",json!(true))] {
            let mut p=project();p["mesh"][key]=value;
            assert_eq!(choose(&p,&fixture()).unwrap()["reason"],"meshing_configuration_outside_training_domain");
        }
    }
    #[test]
    fn malformed_weights_and_provenance_are_rejected() {
        for (key,value) in [("hidden_bias",json!([])),("output_bias",json!([0,0])),("input_weights",json!([[1,2]])),
            ("baseline_algorithm",json!("unknown")),("deploy_enabled",json!(1)),("improvement",json!(0.2)),
            ("validation_geometry_ids",json!(["a".repeat(64)])),("training_families",json!(["validation"])),
            ("feature_ranges",json!(vec![[1,0];8]))] {
            let mut model=fixture();model[key]=value;assert!(choose(&project(),&model).is_err(),"key {key}");
        }
        let mut model=fixture();model["input_weights"][0][0]=json!(1000001);assert!(choose(&project(),&model).is_err());
    }
    #[test]
    fn no_validation_gain_cannot_enable_a_model() {
        let mut model=fixture();model["validation_guide_reward"]=json!(0.5);model["improvement"]=json!(0.0);
        assert!(choose(&project(),&model).is_err());
    }
    #[test]
    fn closed_polygon_ring_has_same_features_as_open_ring() {
        let mut p=project();p["geometry"]=json!({"kind":"polygon","outer":[[0,0],[4,0],[4,3],[0,3]],"holes":[[[1,1],[2,1],[2,2],[1,2]]]});
        let first=extract_features(&p).unwrap();
        p["geometry"]["outer"].as_array_mut().unwrap().push(json!([0,0]));
        p["geometry"]["holes"][0].as_array_mut().unwrap().push(json!([1,1]));
        assert_eq!(extract_features(&p).unwrap(),first);
    }
    #[test]
    fn three_dimensional_features_match_python_native_parameters() {
        for (mode,geometry,expected) in [
            ("surface",json!({"kind":"box","width":3,"height":2,"depth":1.5}),[0.5,0.5,2.0/3.0,4.0/19.0,0.0,0.0,0.0,0.25]),
            ("volume",json!({"kind":"bracket","width":3,"height":3,"depth":1.2,"wall":0.8,"throughhole_radius":0.2}),[1.0,0.4,1.0,4.0/19.0,0.2,1.0,0.0,0.5]),
            ("volume",json!({"kind":"sphere","radius":1.5}),[1.0,1.0,1.0,4.0/19.0,0.0,1.0,0.0,1.0/13.0]),
        ] {
            let p=json!({"mode":mode,"geometry":geometry,"mesh":{"size":0.8}});
            let actual=extract_features(&p).unwrap();
            for (a,b) in actual.iter().zip(expected) {assert!((a-b).abs()<1e-14,"{a} != {b}");}
        }
    }
    #[test]
    fn equal_scores_choose_first_algorithm_like_python() {
        let mut model=fixture();model["output_weights"]=json!([[0,0],[0,0],[0,0]]);model["output_bias"]=json!([0.5,0.5,0.5]);
        assert_eq!(choose(&project(),&model).unwrap()["algorithm"],"automatic");
    }
}
