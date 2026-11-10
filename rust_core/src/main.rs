#![recursion_limit = "256"]

mod backend;
mod gmsh;
mod metrics;
mod advisor;
mod learning;
mod quality;
mod experience;
mod representation;
mod corpus;
mod dataset_model;
mod mesh_import;

use serde_json::{json, Value};
use std::{env, fs, path::PathBuf};

fn save_operation_report(request: &Value, report: Value) -> Result<Value,String> {
    if let Some(path) = request.get("output_dir").and_then(Value::as_str) {
        let directory=PathBuf::from(path);
        fs::create_dir_all(&directory).map_err(|e|e.to_string())?;
        fs::write(directory.join("report.json"),serde_json::to_vec_pretty(&report).map_err(|e|e.to_string())?)
            .map_err(|e|e.to_string())?;
    }
    Ok(report)
}

fn process(request: &Value) -> Result<Value, String> {
    let operation = request.get("operation").and_then(Value::as_str).unwrap_or("mesh");
    if operation == "prepare_mesh_import" {
        return save_operation_report(request,mesh_import::prepare(request)?);
    }
    if operation == "metrics" {
        return save_operation_report(request,metrics::analyze_preview(&request["preview"])?);
    }
    if operation == "advisor_features" {
        return save_operation_report(request,json!({"features":advisor::extract_features(&request["project"])?}));
    }
    if operation == "advisor_choose" {
        return save_operation_report(request,advisor::choose(&request["project"],&request["model"])?);
    }
    if operation == "advisor_default_cases" {
        return save_operation_report(request,json!({"cases":learning::default_cases()}));
    }
    if operation == "corpus_train" {
        let integrity=corpus::training_integrity(request)?;
        let mut report=dataset_model::train(request)?;
        report["source_integrity"]=integrity.report.clone();
        return save_operation_report(request,report);
    }
    if operation.starts_with("corpus_") {
        return save_operation_report(request,corpus::process(request)?);
    }
    if operation == "memory_info" {
        let report = experience::memory_info(request)?;
        let directory = PathBuf::from(request["output_dir"].as_str().ok_or("Dossier de résultat absent")?);
        fs::create_dir_all(&directory).map_err(|e|e.to_string())?;
        fs::write(directory.join("report.json"), serde_json::to_vec_pretty(&report).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
        return Ok(report);
    }
    if operation == "train" {
        let mut report = learning::train(request)?;
        report["native_kernel"] = json!({"language":"rust","version":env!("CARGO_PKG_VERSION"),"training":"rust SGD and native evaluations"});
        let directory = PathBuf::from(request["output_dir"].as_str().ok_or("Dossier d'entraînement absent")?);
        fs::write(directory.join("report.json"), serde_json::to_vec_pretty(&report).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
        return Ok(report);
    }
    if operation == "export_unv" {
        let library = request["gmsh_library"].as_str().ok_or("Bibliothèque Gmsh absente")?;
        let source = PathBuf::from(request["source_mesh"].as_str().ok_or("Maillage source absent")?);
        let output = PathBuf::from(request["output_mesh"].as_str().ok_or("Destination UNV absente")?);
        let api = gmsh::Gmsh::new(library)?;
        api.open(&source)?;
        if api.elements(0,-1)?.iter().any(|b| !b.tags.is_empty()) {
            return Err("L'export UNV de mailles ponctuelles 0D n'est pas pris en charge ; utiliser MED ou régénérer le maillage public sans cellules 0D".into());
        }
        let before: usize = api.elements(-1,-1)?.iter().map(|b| b.tags.len()).sum();
        api.set_number("Mesh.SaveGroupsOfNodes",1.0)?;
        api.set_number("Mesh.SaveGroupsOfElements",1.0)?;
        api.write(&output)?;
        drop(api);
        let api = gmsh::Gmsh::new(library)?;
        api.open(&output)?;
        let after: usize = api.elements(-1,-1)?.iter().map(|b| b.tags.len()).sum();
        if before != after { return Err(format!("La relecture UNV a changé le nombre d'éléments : {before} → {after}")); }
        return Ok(json!({"readback":true,"groups_readback":false,"reader":"Rust / Gmsh UNV","elements_read":after,"physical_names_read":[],"native_kernel":"rust"}));
    }
    if operation == "rescale" {
        let library = request.get("gmsh_library").and_then(Value::as_str).ok_or("Bibliothèque Gmsh absente")?;
        let source = PathBuf::from(request.get("source_mesh").and_then(Value::as_str).ok_or("Maillage source absent")?);
        let output = PathBuf::from(request.get("output_mesh").and_then(Value::as_str).ok_or("Maillage de sortie absent")?);
        if source.canonicalize().ok() == output.canonicalize().ok() && output.exists() {
            return Err("La sortie doit être distincte du maillage source".into());
        }
        let factor = request.get("factor").and_then(Value::as_f64).ok_or("Facteur d'unité absent")?;
        if !factor.is_finite() || factor <= 0.0 { return Err("Facteur d'unité invalide".into()); }
        let api = gmsh::Gmsh::new(library)?;
        api.open(&source)?;
        let mut periodic = Vec::new();
        for dimension in 1..=2 {
            for (_, tag) in api.entities(dimension)? {
                let (master, slaves, _, mut affine) = api.mesh_get_periodic_nodes(dimension, tag)?;
                if master != tag && !slaves.is_empty() && affine.len() == 16 {
                    for index in [3,7,11] { affine[index] *= factor; }
                    periodic.push((dimension,tag,master,affine));
                }
            }
        }
        let (tags, points) = api.nodes()?;
        for point in &points {
            let scaled = [point[0]*factor, point[1]*factor, point[2]*factor];
            if scaled.iter().any(|x| !x.is_finite()) { return Err("Coordonnées exportées hors plage".into()); }
        }
        // Transform all nodes in one native call instead of crossing the C ABI
        // once per node. Validate the complete coordinate range before mutation.
        let affine = [factor,0.,0.,0., 0.,factor,0.,0., 0.,0.,factor,0., 0.,0.,0.,1.];
        api.mesh_affine_transform(&affine, &[])?;
        for (dimension,slave,master,affine) in periodic {
            api.mesh_set_periodic(dimension,&[slave],&[master],&affine)?;
        }
        if let Some(parent) = output.parent() { fs::create_dir_all(parent).map_err(|e| e.to_string())?; }
        api.set_number("Mesh.MshFileVersion", 4.1)?;
        api.write(&output)?;
        return Ok(json!({"operation":"rescale","backend":"rust","nodes":tags.len(),"factor":factor,"file":output}));
    }
    let mut native_request = request.clone();
    let mut advice = None;
    if operation == "mesh" {
        if let Some(path) = request.get("advisor_model").and_then(Value::as_str) {
            let result = fs::read(path).map_err(|e| e.to_string())
                .and_then(|bytes| {
                    if bytes.len() > 2*1024*1024 { return Err("Guide trop volumineux".into()); }
                    serde_json::from_slice::<Value>(&bytes).map_err(|e| e.to_string())
                })
                .and_then(|model| advisor::choose(&native_request["project"], &model));
            let outcome = match result {
                Ok(value) => value,
                Err(error) => json!({"algorithm":native_request["project"]["mesh"]["algorithm"],"use_model":false,"reason":format!("Guide refusé : {error}")}),
            };
            if outcome["use_model"].as_bool() == Some(true) {
                native_request["project"]["mesh"]["algorithm"] = outcome["algorithm"].clone();
            }
            advice = Some(outcome);
        }
    }
    let mut report = if operation == "learn" { experience::learn(&native_request)? }
                     else { backend::run(&native_request)? };
    if operation == "learn" { report["operation"] = json!("learn"); }
    if let Some(value) = advice { report["advisor"] = value; }
    if let Some(output) = request.get("output_dir").and_then(Value::as_str) {
        let directory = PathBuf::from(output);
        let preview_path = directory.join("preview.json");
        if preview_path.is_file() {
            let preview: Value = serde_json::from_slice(&fs::read(&preview_path).map_err(|e| e.to_string())?).map_err(|e|e.to_string())?;
            report["rust_metrics"] = metrics::analyze_preview(&preview)?;
        }
        report["native_kernel"] = json!({"language":"rust","version":env!("CARGO_PKG_VERSION"),"geometry_mesher":"Gmsh/OpenCASCADE native C ABI"});
        fs::write(directory.join("report.json"), serde_json::to_vec_pretty(&report).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    }
    Ok(report)
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    if args == ["--version"] {
        println!("mailleur-core {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    let path = match args.as_slice() {
        [flag, path] if flag == "--request" => path,
        [path] => path,
        _ => { eprintln!("Usage : mailleur-core --request requete.json"); std::process::exit(2); }
    };
    let request = fs::read(path).map_err(|e| e.to_string())
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).map_err(|e|e.to_string()));
    // The GUI reads the authoritative artifacts. Avoid serializing the same
    // potentially large result a second time into its diagnostic log.
    let summary_response = request.as_ref().is_ok_and(|value| value["response_mode"] == "summary");
    let result = request.and_then(|request| process(&request));
    match result {
        Ok(_) if summary_response => println!("{}", json!({"ok":true})),
        Ok(report) => println!("{}", json!({"ok":true,"result":report})),
        Err(error) => { eprintln!("{}", json!({"ok":false,"error":error})); std::process::exit(2); }
    }
}
