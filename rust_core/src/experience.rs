//! Continuous, geometry-specific exploration and persistent native experience.
//!
//! The learned model only orders real meshing experiments. A prediction never
//! substitutes for a new mesh or for the geometric and regional quality checks.

#[path = "experience_model.rs"]
mod model;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

type R<T> = Result<T, String>;
const MEMORY_SCHEMA: &str = "atelier.experience.v1";
const ENGINE: &str = "4.15.2";
// rc7 enables correctly rounded JSON f64 parsing and OCC tessellated bounds.
// Both can change CAD decisions and meshing; do not reuse rc4/rc5/rc6 exact-context
// aliases. The old contexts and their observations remain archived separately.
const CONTEXT_NUMERICS_VERSION: &str = "1.0.0-rc.7";
const MAX_MEMORY_BYTES: u64 = 128 * 1024 * 1024;
const MAX_EVENT_BYTES: u64 = 512 * 1024 * 1024;
const MAX_CONTEXTS: usize = 1000;
const MAX_OBSERVATIONS: usize = 32000;

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

fn finite(value: &Value, label: &str) -> R<f64> {
    value
        .as_f64()
        .filter(|n| n.is_finite())
        .ok_or_else(|| format!("{label} doit être un nombre fini"))
}

fn integer(value: &Value, key: &str, default: u64, min: u64, max: u64) -> R<u64> {
    let n = match value.get(key) {
        Some(v) => v
            .as_u64()
            .ok_or_else(|| format!("{key} doit être entier"))?,
        None => default,
    };
    if (min..=max).contains(&n) {
        Ok(n)
    } else {
        Err(format!("{key} doit être compris entre {min} et {max}"))
    }
}

fn boolean(value: &Value, key: &str, default: bool) -> R<bool> {
    match value.get(key) {
        Some(v) => v
            .as_bool()
            .ok_or_else(|| format!("{key} doit être booléen")),
        None => Ok(default),
    }
}

fn canonical(value: &Value) -> R<Value> {
    match value {
        Value::Number(number) => {
            // Normalize 1 and 1.0 without rounding distinct large integer IDs.
            if number
                .as_i64()
                .is_some_and(|n| n.unsigned_abs() > 9_007_199_254_740_991)
                || number.as_u64().is_some_and(|n| n > 9_007_199_254_740_991)
            {
                return Ok(value.clone());
            }
            let n = finite(value, "Nombre de l'identité")?;
            Ok(json!(if n == 0. { 0. } else { n }))
        }
        Value::Array(a) => a
            .iter()
            .map(canonical)
            .collect::<R<Vec<_>>>()
            .map(Value::Array),
        Value::Object(o) => {
            let mut result = serde_json::Map::new();
            for (key, item) in o {
                result.insert(key.clone(), canonical(item)?);
            }
            Ok(Value::Object(result))
        }
        _ => Ok(value.clone()),
    }
}

fn digest(value: &Value) -> R<String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(&canonical(value)?).map_err(|e| e.to_string())?)
    ))
}

fn source_digest(path: &Path) -> R<(String, u64)> {
    let mut file = File::open(path).map_err(|e| format!("Source CAO : {e}"))?;
    let size = file.metadata().map_err(|e| e.to_string())?.len();
    if size > 128 * 1024 * 1024 {
        return Err("Source trop volumineuse pour la mémoire d'apprentissage (128 Mio)".into());
    }
    let mut state = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let count = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        state.update(&buffer[..count]);
    }
    Ok((format!("{:x}", state.finalize()), size))
}

/// Streaming output avoids a second complete serialized memory index in RAM.
/// A sibling file is published only after the bounded writer and fsync succeed.
struct LimitedFile {
    file: File,
    limit: Option<u64>,
    written: u64,
}
impl Write for LimitedFile {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if self
            .limit
            .is_some_and(|limit| bytes.len() as u64 > limit.saturating_sub(self.written))
        {
            return Err(std::io::Error::new(std::io::ErrorKind::Other,
                "La mémoire atteindrait 128 Mio ; l'historique existant est conservé sans suppression automatique"));
        }
        let count = self.file.write(bytes)?;
        self.written += count as u64;
        Ok(count)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.file.flush()
    }
}

fn atomic_json(path: &Path, value: &Value) -> R<()> {
    let memory_index = path.file_name().and_then(|p| p.to_str()) == Some("memory.json");
    atomic_json_bounded(
        path,
        value,
        memory_index.then_some(MAX_MEMORY_BYTES),
        memory_index,
    )
}

fn atomic_json_bounded(path: &Path, value: &Value, limit: Option<u64>, compact: bool) -> R<()> {
    let parent = path.parent().ok_or("Dossier de sauvegarde absent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id()
    ));
    let result = (|| {
        let file = File::create(&temporary).map_err(|e| e.to_string())?;
        let output = LimitedFile {
            file,
            limit,
            written: 0,
        };
        let mut writer = BufWriter::with_capacity(64 * 1024, output);
        if compact {
            serde_json::to_writer(&mut writer, value)
        } else {
            serde_json::to_writer_pretty(&mut writer, value)
        }
        .map_err(|e| e.to_string())?;
        writer.write_all(b"\n").map_err(|e| e.to_string())?;
        writer.flush().map_err(|e| e.to_string())?;
        writer
            .get_ref()
            .file
            .sync_all()
            .map_err(|e| e.to_string())?;
        drop(writer);
        fs::rename(&temporary, path)
            .map_err(|e| format!("Sauvegarde atomique {} : {e}", path.display()))
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

/// OS-owned locks are released if the GUI cancels or kills the native process.
struct MemoryLock {
    _file: File,
}

impl MemoryLock {
    fn acquire(directory: &Path) -> R<Self> {
        fs::create_dir_all(directory).map_err(|e| e.to_string())?;
        let path = directory.join("memory.lock");
        #[cfg(windows)]
        let file = {
            use std::os::windows::fs::OpenOptionsExt;
            OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .share_mode(0)
                .open(path)
                .map_err(|e| format!("Mémoire occupée par un autre apprentissage : {e}"))?
        };
        #[cfg(not(windows))]
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)
            .map_err(|e| e.to_string())?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            extern "C" {
                fn flock(fd: i32, operation: i32) -> i32;
            }
            // LOCK_EX | LOCK_NB have the same values on supported Unix systems.
            if unsafe { flock(file.as_raw_fd(), 2 | 4) } != 0 {
                return Err(
                    "La mémoire est occupée par un autre apprentissage ; réessayer après sa fin"
                        .into(),
                );
            }
        }
        Ok(Self { _file: file })
    }
}

fn load_memory(directory: &Path) -> R<Value> {
    let path = directory.join("memory.json");
    let mut memory: Value = if path.exists() {
        if fs::metadata(&path).map_err(|e| e.to_string())?.len() > MAX_MEMORY_BYTES {
            return Err("Mémoire trop volumineuse ; l'archive existante a été conservée".into());
        }
        serde_json::from_slice::<Value>(&fs::read(path).map_err(|e| e.to_string())?)
            .map_err(|e| format!("Mémoire illisible, conservée sans remplacement : {e}"))?
    } else {
        json!({"schema":MEMORY_SCHEMA,"engine":ENGINE,"generation":0,"contexts":[]})
    };
    if memory["schema"] != MEMORY_SCHEMA || !memory["contexts"].is_array() {
        return Err("Format de mémoire incompatible ; le fichier existant a été conservé".into());
    }
    if memory["contexts"].as_array().unwrap().len() > MAX_CONTEXTS {
        return Err("La mémoire dépasse la limite de contextes".into());
    }
    recover_journal(directory, &mut memory)?;
    Ok(memory)
}

/// The journal is committed before its index. Recover complete events after an
/// interrupted index write; an incomplete last line is never treated as a test.
fn recover_journal(directory: &Path, memory: &mut Value) -> R<()> {
    let path = directory.join("events.jsonl");
    if !path.exists() {
        return Ok(());
    }
    let mut file = File::open(&path).map_err(|e| e.to_string())?;
    let length = file.metadata().map_err(|e| e.to_string())?.len();
    if length > MAX_EVENT_BYTES {
        return Err("Le journal dépasse 512 Mio ; conserver et archiver explicitement cet historique avant de poursuivre".into());
    }
    let mut position = memory["journal_position"].as_u64().unwrap_or(0);
    if position > length {
        return Err(
            "Le journal d'apprentissage a été tronqué ; historique conservé, apprentissage arrêté"
                .into(),
        );
    }
    file.seek(SeekFrom::Start(position))
        .map_err(|e| e.to_string())?;
    let mut reader = BufReader::new(file);
    loop {
        let mut line = Vec::new();
        // The index's complete context and one observation fit within its
        // bounded size; a small allowance covers the event envelope.
        let line_limit = MAX_MEMORY_BYTES + 64 * 1024;
        let count = reader
            .by_ref()
            .take(line_limit + 1)
            .read_until(b'\n', &mut line)
            .map_err(|e| e.to_string())?;
        if count as u64 > line_limit {
            return Err(
                "Événement du journal trop volumineux ; historique conservé sans remplacement"
                    .into(),
            );
        }
        if count == 0 {
            break;
        }
        if !line.ends_with(b"\n") {
            memory["journal_incomplete_tail"] = json!(true);
            break;
        }
        let event: Value = serde_json::from_slice(&line).map_err(|e| format!("Journal d'apprentissage corrompu à l'octet {position}, conservé sans remplacement : {e}"))?;
        if event["schema"] != "atelier.experience.event.v1"
            || !event["context"].is_object()
            || !event["observation"].is_object()
        {
            return Err("Événement d'apprentissage incompatible ; journal conservé".into());
        }
        let generation = event["generation"]
            .as_u64()
            .ok_or("Génération d'événement absente")?;
        if generation > memory["generation"].as_u64().unwrap_or(0) {
            let rows = memory["contexts"].as_array_mut().unwrap();
            let index = if let Some(i) = rows
                .iter()
                .position(|r| r["context_id"] == event["context"]["context_id"])
            {
                i
            } else {
                if rows.len() >= MAX_CONTEXTS {
                    return Err("Récupération du journal : trop de contextes".into());
                }
                let mut context = event["context"].clone();
                context["observations"] = json!([]);
                rows.push(context);
                rows.len() - 1
            };
            if !event["context"]["last_geometry_report"].is_null() {
                memory["contexts"][index]["last_geometry_report"] =
                    event["context"]["last_geometry_report"].clone();
            }
            save_observation(memory, index, event["observation"].clone())?;
            memory["generation"] = json!(generation);
            memory["journal_events"] = json!(memory["journal_events"].as_u64().unwrap_or(0) + 1);
        }
        position += count as u64;
    }
    memory["journal_position"] = json!(position);
    Ok(())
}

fn append_event(
    directory: &Path,
    memory: &mut Value,
    context_index: usize,
    observation: &Value,
) -> R<()> {
    // Copy only the persisted context metadata, never its replay history.
    let context = Value::Object(
        memory["contexts"][context_index]
            .as_object()
            .ok_or("Contexte de journal invalide")?
            .iter()
            .filter(|(key, _)| key.as_str() != "observations")
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    );
    let event = json!({"schema":"atelier.experience.event.v1","generation":memory["generation"],"timestamp":now(),"context":context,"observation":observation});
    let mut bytes = serde_json::to_vec(&event).map_err(|e| e.to_string())?;
    bytes.push(b'\n');
    let path = directory.join("events.jsonl");
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .write(true)
        .open(&path)
        .map_err(|e| e.to_string())?;
    let mut size = file.metadata().map_err(|e| e.to_string())?.len();
    if memory["journal_incomplete_tail"] == true {
        size = memory["journal_position"]
            .as_u64()
            .ok_or("Position de récupération absente")?;
        file.set_len(size).map_err(|e| e.to_string())?;
        memory
            .as_object_mut()
            .unwrap()
            .remove("journal_incomplete_tail");
    }
    if size + bytes.len() as u64 > MAX_EVENT_BYTES {
        return Err("Le journal atteindrait 512 Mio ; l'historique existant est conservé sans suppression automatique".into());
    }
    file.write_all(&bytes).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())?;
    memory["journal_position"] = json!(size + bytes.len() as u64);
    memory["journal_events"] = json!(memory["journal_events"].as_u64().unwrap_or(0) + 1);
    Ok(())
}

fn observation_count(memory: &Value) -> usize {
    memory["contexts"].as_array().map_or(0, |rows| {
        rows.iter()
            .map(|r| r["observations"].as_array().map_or(0, Vec::len))
            .sum()
    })
}

/// Read-only metadata, also available without loading the Gmsh library.
pub fn memory_info(request: &Value) -> R<Value> {
    let path = PathBuf::from(
        request["memory_dir"]
            .as_str()
            .ok_or("Dossier de mémoire absent")?,
    );
    let memory = load_memory(&path)?;
    let contexts = memory["contexts"].as_array().unwrap();
    let mut geometries = BTreeSet::new();
    let mut total_trials = 0u64;
    let mut accepted = 0usize;
    let mut failed = 0usize;
    for context in contexts {
        if let Some(id) = context["geometry_id"].as_str() {
            geometries.insert(id.to_string());
        }
        if let Some(rows) = context["observations"].as_array() {
            for row in rows {
                total_trials += row["attempts"].as_u64().unwrap_or(1);
                if row["accepted"] == true {
                    accepted += 1;
                } else {
                    failed += 1;
                }
            }
        }
    }
    let model_path = path.join("model.json");
    let learned = if model_path.is_file() {
        serde_json::from_slice::<Value>(&fs::read(model_path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?
    } else {
        Value::Null
    };
    let versions = if path.join("models").is_dir() {
        fs::read_dir(path.join("models"))
            .map_err(|e| e.to_string())?
            .filter_map(Result::ok)
            .filter(|e| {
                e.file_name().to_string_lossy().starts_with("model-")
                    && e.path().extension().and_then(|x| x.to_str()) == Some("json")
            })
            .count()
    } else {
        0
    };
    Ok(
        json!({"schema":"atelier.experience.info.v1","contexts":contexts.len(),"geometry_count":geometries.len(),"observations":accepted+failed,"total_trials":total_trials,"journal_events":memory["journal_events"].as_u64().unwrap_or(0),"accepted_observations":accepted,"failed_observations":failed,"model_versions":versions,"model":learned,"memory_path":path}),
    )
}

fn context_identity(project: &Value, source: &Value) -> R<(String, String)> {
    let geometry_id = digest(
        &json!({"geometry":project["geometry"],"mode":project["mode"],"units":project["units"],"source":source}),
    )?;
    let mut contextual = project.clone();
    let object = contextual
        .as_object_mut()
        .ok_or("Projet d'apprentissage invalide")?;
    for key in ["name", "learning", "operation"] {
        object.remove(key);
    }
    let id = digest(
        &json!({"engine":ENGINE,"core":CONTEXT_NUMERICS_VERSION,"project":contextual,"source":source}),
    )?;
    Ok((id, geometry_id))
}

#[derive(Clone)]
struct Candidate {
    recipe: Value,
    project: Value,
    from_memory: bool,
}

fn recipe(project: &Value, action: &str) -> Value {
    json!({"action_id":action,"mesh":project["mesh"],"local_sizes":project["local_sizes"],"refinement_regions":project["refinement_regions"]})
}

fn candidate(original: &Value, recipe: Value, from_memory: bool) -> R<Candidate> {
    let mut project = original.clone();
    if !recipe["mesh"].is_object() {
        return Err("Recette mémorisée invalide".into());
    }
    project["mesh"] = recipe["mesh"].clone();
    for key in ["local_sizes", "refinement_regions"] {
        if recipe[key].is_null() {
            project.as_object_mut().unwrap().remove(key);
        } else {
            project[key] = recipe[key].clone();
        }
    }
    if !project.get("quality_policy").is_some_and(Value::is_object) {
        project["quality_policy"] = json!({"minimum_quality":0.05,"poor_threshold":0.2});
    }
    project["quality_policy"]["strict"] = json!(false);
    Ok(Candidate {
        recipe,
        project,
        from_memory,
    })
}

fn positive_mesh(project: &Value, key: &str, default: f64) -> R<f64> {
    let value = match project["mesh"].get(key) {
        Some(v) => finite(v, key)?,
        None => default,
    };
    if value > 0. {
        Ok(value)
    } else {
        Err(format!("{key} doit être strictement positif"))
    }
}

fn input_factor(project: &Value) -> R<f64> {
    match project["units"]["input"].as_str().unwrap_or("mm") {
        "m" => Ok(1.),
        "cm" => Ok(0.01),
        "mm" => Ok(0.001),
        "in" => Ok(0.0254),
        _ => Err("Unité d'apprentissage inconnue".into()),
    }
}

fn add_unique(
    queue: &mut VecDeque<Candidate>,
    seen: &mut BTreeSet<String>,
    candidate: Candidate,
    front: bool,
) -> R<()> {
    // Action labels are explanatory, not part of the actual numerical recipe.
    let mut numerical = candidate.recipe.clone();
    numerical.as_object_mut().unwrap().remove("action_id");
    if seen.insert(digest(&numerical)?) {
        if front {
            queue.push_front(candidate);
        } else {
            queue.push_back(candidate);
        }
    }
    Ok(())
}

fn initial_candidates(original: &Value, remembered: Option<&Value>) -> R<VecDeque<Candidate>> {
    let mut queue = VecDeque::new();
    let mut seen = BTreeSet::new();
    if let Some(saved) = remembered {
        add_unique(
            &mut queue,
            &mut seen,
            candidate(original, saved.clone(), true)?,
            false,
        )?;
    }
    let volume = original["mode"] == "volume";
    let has_local = ["local_sizes", "refinement_regions", "critical_zones"]
        .iter()
        .any(|k| original[*k].as_array().is_some_and(|a| !a.is_empty()));
    let requested = original["mesh"]["algorithm"]
        .as_str()
        .unwrap_or("automatic");
    if !["automatic", "delaunay", "frontal"].contains(&requested) {
        return Err("Algorithme inconnu".into());
    }
    let algorithms: Vec<&str> = [requested, "delaunay", "frontal", "automatic"]
        .into_iter()
        .filter(|a| !(volume && has_local && *a == "frontal"))
        .collect();
    let base = positive_mesh(original, "size", 0.5)?;
    let minimum = positive_mesh(original, "min_size", 0.1)?;
    if minimum > base {
        return Err("La taille minimale doit être inférieure à la taille globale".into());
    }
    let mut volume_actions = BTreeSet::new();
    for scale in [1., 0.85, 0.65, 0.5] {
        for algorithm in &algorithms {
            let effective = if volume && *algorithm == "automatic" {
                "delaunay"
            } else {
                algorithm
            };
            if !volume_actions.insert(format!("{effective}:{scale}")) {
                continue;
            }
            let mut project = original.clone();
            project["mesh"]["algorithm"] = json!(algorithm);
            project["mesh"]["size"] = json!((base * scale).max(minimum));
            let action = format!("{effective}:size:{scale}");
            add_unique(
                &mut queue,
                &mut seen,
                candidate(original, recipe(&project, &action), false)?,
                false,
            )?;
        }
    }
    // A separate, deterministic strategy refines the selected protected entities.
    if let Some(zones) = original["critical_zones"]
        .as_array()
        .filter(|z| !z.is_empty())
    {
        let mut project = original.clone();
        project["mesh"]["algorithm"] = json!("delaunay");
        let mut local = original["local_sizes"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        for zone in zones {
            if let (Some(dim), Some(tags)) = (zone["dimension"].as_i64(), zone["tags"].as_array()) {
                for tag in tags {
                    local.push(json!({"dimension":dim,"tag":tag,"size":(base*0.5).max(minimum)}));
                }
            }
        }
        project["local_sizes"] = json!(local);
        add_unique(
            &mut queue,
            &mut seen,
            candidate(
                original,
                recipe(&project, "delaunay:protected_entities"),
                false,
            )?,
            true,
        )?;
    }
    // Keep a remembered recipe first, even when adding protected-entity trials.
    if let Some(index) = queue.iter().position(|c| c.from_memory) {
        let saved = queue.remove(index).unwrap();
        queue.push_front(saved);
    }
    Ok(queue)
}

fn features(
    original: &Value,
    candidate: &Candidate,
    report: Option<&Value>,
) -> [f64; model::INPUTS] {
    crate::representation::features(original, &candidate.project, report)
}

fn reference_report(report: &Value, project: &Value) -> Value {
    let mut reference = json!({"geometry_diagnostics":report["geometry_diagnostics"],"entity_catalog":report["entity_catalog"]});
    reference["geometry_descriptor"] = crate::representation::descriptor(project, Some(&reference));
    reference
}

fn adaptive_candidate(
    original: &Value,
    previous: &Candidate,
    report: &Value,
    iteration: usize,
) -> R<Option<Candidate>> {
    let Some(samples) = report["quality"]["poor_samples"]
        .as_array()
        .filter(|s| !s.is_empty())
    else {
        return Ok(None);
    };
    let factor = input_factor(original)?;
    let minimum = positive_mesh(original, "min_size", 0.1)?;
    let size = positive_mesh(&previous.project, "size", 0.5)?;
    let target = (size * 0.5).max(minimum);
    let mut project = previous.project.clone();
    // General background fields are supported by Delaunay in 3D, not Netgen.
    project["mesh"]["algorithm"] = json!("delaunay");
    project["mesh"]["smoothing"] = json!(8);
    project["mesh"]["optimize"] = json!(true);
    let mut regions = project["refinement_regions"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    for sample in samples.iter().take(8) {
        let Some(center) = sample["center"].as_array().filter(|a| a.len() == 3) else {
            continue;
        };
        let center = [
            finite(&center[0], "Centre adaptatif")? / factor,
            finite(&center[1], "Centre adaptatif")? / factor,
            finite(&center[2], "Centre adaptatif")? / factor,
        ];
        let radius = 1.8 * size;
        if regions.iter().any(|r| {
            let distance = (0..3)
                .map(|i| (r["center"][i].as_f64().unwrap_or(f64::INFINITY) - center[i]).powi(2))
                .sum::<f64>();
            distance < (radius * 0.25).powi(2)
                && r["size"].as_f64().unwrap_or(f64::INFINITY) <= target
        }) {
            continue;
        }
        regions.push(json!({"center":center,"radius":radius,"size":target}));
        if regions.len() >= 128 {
            break;
        }
    }
    if regions
        == previous.project["refinement_regions"]
            .as_array()
            .cloned()
            .unwrap_or_default()
    {
        return Ok(None);
    }
    project["refinement_regions"] = json!(regions);
    Ok(Some(candidate(
        original,
        recipe(&project, &format!("delaunay:adaptive_samples:{iteration}")),
        false,
    )?))
}

fn model_examples(memory: &Value) -> Vec<model::Example> {
    let mut result = Vec::new();
    for context in memory["contexts"].as_array().unwrap() {
        let Some(group) = context["geometry_id"].as_str() else {
            continue;
        };
        for row in context["observations"].as_array().into_iter().flatten() {
            if row["feature_schema"] != crate::representation::SCHEMA {
                continue;
            }
            let Some(values) = row["features"]
                .as_array()
                .filter(|v| v.len() == model::INPUTS)
            else {
                continue;
            };
            let mut input = [0.; model::INPUTS];
            if values
                .iter()
                .enumerate()
                .any(|(i, x)| match x.as_f64().filter(|x| x.is_finite()) {
                    Some(x) => {
                        input[i] = x;
                        false
                    }
                    None => true,
                })
            {
                continue;
            }
            let Some(target) = row["score"]
                .as_f64()
                .filter(|v| v.is_finite() && (0. ..=1.).contains(v))
            else {
                continue;
            };
            result.push(model::Example {
                features: input,
                target,
                group: group.to_string(),
            });
        }
    }
    result
}

/// Re-encode archived labels using saved CAD context, never using their quality.
/// Raw rc3 inputs remain in the index and the append-only original journal.
fn migrate_feature_history(memory: &mut Value) -> Value {
    let mut preserved = 0usize;
    let mut reencoded = 0usize;
    let mut excluded = 0usize;
    let mut previously_reencoded = 0usize;
    let mut missing_graph = 0usize;
    let mut reasons = BTreeSet::new();
    for context in memory["contexts"].as_array_mut().into_iter().flatten() {
        let original = context["project"].clone();
        let reference = context
            .get("last_geometry_report")
            .cloned()
            .filter(|r| r["entity_catalog"].is_array());
        let enriched = reference.as_ref().map(|r| reference_report(r, &original));
        if let Some(r) = &enriched {
            context["last_geometry_report"] = r.clone();
        }
        for row in context["observations"].as_array_mut().into_iter().flatten() {
            if row["legacy_features"]
                .as_array()
                .is_some_and(|a| a.len() == 16)
            {
                preserved += 1;
                previously_reencoded += 1;
            }
            let old_len = row["features"].as_array().map_or(0, Vec::len);
            if old_len == model::INPUTS && row["feature_schema"] == crate::representation::SCHEMA {
                continue;
            }
            preserved += 1;
            if old_len != 16 || !original.is_object() || !row["recipe"]["mesh"].is_object() {
                excluded += 1;
                reasons.insert(
                    "Contexte ou recette absents, ou ancien vecteur de schéma inconnu".to_string(),
                );
                continue;
            }
            if original["geometry"]["kind"] == "import" && enriched.is_none() {
                excluded += 1;
                reasons.insert("Géométrie importée sans catalogue CAD sauvegardé : positions non reconstructibles".to_string());
                continue;
            }
            match candidate(&original, row["recipe"].clone(), false) {
                Ok(action) => {
                    let input = features(&original, &action, enriched.as_ref());
                    let old = row["features"].clone();
                    row["legacy_features"] = old;
                    row["features"] = json!(input.to_vec());
                    row["feature_schema"] = json!(crate::representation::SCHEMA);
                    let graph_available = enriched.as_ref().is_some_and(|r| {
                        r["geometry_descriptor"]["entity_graph"]["relation_source"]
                            == "native_cad_boundary"
                    });
                    if !graph_available {
                        missing_graph += 1;
                    }
                    row["feature_migration"] = json!({"from_inputs":16,"to_inputs":model::INPUTS,"method":"saved_project_recipe_and_geometry_catalogue","target_quality_or_connectivity_used":false,"cad_boundary_graph_available":graph_available,"note":if graph_available{"Relations CAD sauvegardées utilisées"}else{"Ancien catalogue sans relations CAD : le canal de topologie inconnue reste explicite"}});
                    reencoded += 1;
                }
                Err(error) => {
                    excluded += 1;
                    reasons.insert(format!("Recette ancienne non reconstructible : {error}"));
                }
            }
        }
    }
    json!({"legacy_examples_preserved":preserved,"legacy_examples_reencoded":reencoded,"legacy_examples_reencoded_total":previously_reencoded+reencoded,"legacy_examples_excluded_from_new_training":excluded,"reencoded_without_cad_boundary_graph":missing_graph,"exclusion_reasons":reasons.into_iter().collect::<Vec<_>>(),"message":if preserved>0{"Les anciennes observations, recettes et entrées brutes sont conservées. Les caractéristiques spatiales sont reconstruites depuis les contextes CAD disponibles ; seules les observations non reconstructibles sont exclues. Les poids incompatibles à 16 entrées sont réinitialisés explicitement."}else{"Représentation spatiale courante ; aucune ancienne observation incompatible."}})
}

fn save_observation(memory: &mut Value, context_index: usize, observation: Value) -> R<()> {
    let rows = memory["contexts"][context_index]["observations"]
        .as_array_mut()
        .ok_or("Historique invalide")?;
    if let Some(index) = rows
        .iter()
        .position(|r| r["recipe_id"] == observation["recipe_id"])
    {
        let attempts = rows[index]["attempts"].as_u64().unwrap_or(1) + 1;
        let legacy = rows[index].get("legacy_features").cloned().or_else(|| {
            rows[index]["features"]
                .as_array()
                .filter(|v| v.len() == 16)
                .map(|_| rows[index]["features"].clone())
        });
        let migration = rows[index].get("feature_migration").cloned();
        rows[index] = observation;
        rows[index]["attempts"] = json!(attempts);
        if let Some(raw) = legacy {
            if rows[index].get("legacy_features").is_none() {
                rows[index]["legacy_features"] = raw;
            }
        }
        if let Some(details) = migration {
            if rows[index].get("feature_migration").is_none() {
                rows[index]["feature_migration"] = details;
            }
        }
    } else {
        rows.push(observation);
    }
    let best = rows
        .iter()
        .filter(|r| r["accepted"] == true)
        .max_by(|a, b| crate::quality::compare_reports(a, b))
        .map(|r| r["recipe_id"].clone())
        .unwrap_or(Value::Null);
    memory["contexts"][context_index]["best_recipe_id"] = best;
    memory["contexts"][context_index]["updated_at"] = json!(now());
    memory["generation"] = json!(memory["generation"].as_u64().unwrap_or(0) + 1);
    Ok(())
}

fn remembered_recipe(memory: &Value, index: usize) -> Option<Value> {
    let context = &memory["contexts"][index];
    if let Some(recipe) = context["observations"]
        .as_array()?
        .iter()
        .find(|r| r["recipe_id"] == context["best_recipe_id"] && r["accepted"] == true)
        .map(|r| r["recipe"].clone())
    {
        return Some(recipe);
    }
    // The interface reloads the winning numerical settings. Their complete
    // context identity is an explicit alias, not an approximate geometry match.
    memory["contexts"]
        .as_array()?
        .iter()
        .flat_map(|c| c["observations"].as_array().into_iter().flatten())
        .filter(|r| r["accepted"] == true && r["resulting_context_id"] == context["context_id"])
        .max_by(|a, b| crate::quality::compare_reports(a, b))
        .map(|r| r["recipe"].clone())
}

fn chosen_project(original: &Value, current: &Candidate) -> Value {
    let mut chosen = current.project.clone();
    chosen["quality_policy"]["strict"] = json!(original["quality_policy"]
        .get("strict")
        .and_then(Value::as_bool)
        .unwrap_or(true));
    if chosen.get("learning").is_some_and(Value::is_object) {
        chosen["learning"]["enabled"] = json!(false);
    }
    chosen
}

fn copy_winner(source: &Path, destination: &Path, chosen: &Value) -> R<()> {
    for name in [
        "mesh.msh",
        "mesh.state.msh",
        "model.brep",
        "preview.json",
        "replay.json",
        "project.normalized.json",
    ] {
        let input = source.join(name);
        if input.is_file() {
            fs::copy(input, destination.join(name))
                .map_err(|e| format!("Copie du maillage retenu : {e}"))?;
        } else if ["mesh.msh", "preview.json"].contains(&name) {
            return Err(format!("Résultat gagnant incomplet : {name}"));
        }
    }
    atomic_json(&destination.join("project.normalized.json"), chosen)?;
    let replay_path = destination.join("replay.json");
    if replay_path.is_file() {
        let mut replay: Value =
            serde_json::from_slice(&fs::read(&replay_path).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?;
        replay["base_project"] = chosen.clone();
        atomic_json(&replay_path, &replay)?;
    }
    Ok(())
}

fn absolute_path(path: &Path) -> R<PathBuf> {
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(path)
    };
    let mut result = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                result.pop();
            }
            other => result.push(other.as_os_str()),
        }
    }
    Ok(result)
}

/// A direct Rust caller may reuse an output directory. Never let a failed new
/// search leave a previous accepted mesh at the public output path.
fn clear_previous_output(output: &Path, source: Option<&Path>, memory: &Path) -> R<()> {
    let canonical_output = output.canonicalize().map_err(|e| e.to_string())?;
    let canonical_source = source
        .map(|p| {
            p.canonicalize()
                .map_err(|e| format!("Source CAO absente : {e}"))
        })
        .transpose()?;
    let canonical_memory = memory.canonicalize().unwrap_or(absolute_path(memory)?);
    let names = [
        "mesh.msh",
        "mesh.state.msh",
        "model.brep",
        "preview.json",
        "replay.json",
        "project.normalized.json",
    ];
    // Validate every collision before removing anything.
    for name in names {
        let target = canonical_output.join(name);
        let resolved = target.canonicalize().unwrap_or(target.clone());
        if canonical_source.as_ref() == Some(&resolved) || canonical_memory.starts_with(&resolved) {
            return Err(format!("Le dossier de sortie écraserait la source ou la mémoire ({name}) ; choisir un autre dossier"));
        }
        if target.is_dir() {
            return Err(format!(
                "Un dossier occupe l'emplacement de sortie {name} ; choisir un autre dossier"
            ));
        }
    }
    for name in names {
        let target = output.join(name);
        if fs::symlink_metadata(&target).is_ok() {
            fs::remove_file(&target)
                .map_err(|e| format!("Suppression de l'ancien résultat {name} : {e}"))?;
        }
    }
    Ok(())
}

/// Explore and retain the actual requested geometry. No synthetic corpus is used.
pub fn learn(request: &Value) -> R<Value> {
    let started = Instant::now();
    let output = PathBuf::from(
        request["output_dir"]
            .as_str()
            .ok_or("Dossier de sortie absent")?,
    );
    let original = request
        .get("project")
        .filter(|p| p.is_object())
        .ok_or("Projet d'apprentissage absent")?;
    if !original["mesh"].is_object() {
        return Err("Paramètres de maillage absents".into());
    }
    if !["planar", "surface", "volume"].contains(&original["mode"].as_str().unwrap_or("planar")) {
        return Err("Mode de maillage inconnu".into());
    }
    let settings = &request["search"];
    let max_trials = integer(settings, "max_trials", 12, 1, 64)? as usize;
    let epochs = integer(settings, "epochs", 80, 1, 200)? as usize;
    let seed = integer(settings, "seed", 42, 0, u32::MAX as u64)?;
    let max_seconds = integer(settings, "max_seconds", 240, 1, 86400)? as f64;
    let use_memory = boolean(settings, "use_memory", true)?;
    let memory_path = if use_memory {
        PathBuf::from(
            request["memory_dir"]
                .as_str()
                .ok_or("Dossier de mémoire absent")?,
        )
    } else {
        output.join("isolated_memory")
    };
    fs::create_dir_all(&output).map_err(|e| e.to_string())?;
    let _lock = MemoryLock::acquire(&memory_path)?;
    let mut memory = load_memory(&memory_path)?;
    let representation_migration = migrate_feature_history(&mut memory);
    clear_previous_output(
        &output,
        request["source"].as_str().map(Path::new),
        &memory_path,
    )?;
    let source_path = request["source"].as_str().map(PathBuf::from);
    if original["geometry"]["kind"] == "import" && source_path.is_none() {
        return Err("Joindre une source CAO pour apprendre la géométrie importée".into());
    }
    let mut source = Value::Null;
    let mut source_snapshot = None;
    if let Some(path) = &source_path {
        let (hash, bytes) = source_digest(path)?;
        let extension = path
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("cad")
            .to_ascii_lowercase();
        if extension.len() > 12 || !extension.chars().all(|c| c.is_ascii_alphanumeric()) {
            return Err("Extension source invalide".into());
        }
        source = json!({"sha256":hash,"bytes":bytes,"extension":extension});
        let directory = memory_path.join("sources");
        fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
        let saved = directory.join(format!("{hash}.{extension}"));
        if !saved.is_file() {
            let temporary = directory.join(format!(".{hash}.{}.tmp", std::process::id()));
            fs::copy(path, &temporary).map_err(|e| format!("Conservation de la source : {e}"))?;
            if source_digest(&temporary)?.0 != hash {
                let _ = fs::remove_file(&temporary);
                return Err("La source a changé pendant sa copie ; reprendre l'importation avant l'apprentissage".into());
            }
            fs::rename(&temporary, &saved).map_err(|e| e.to_string())?;
        } else if source_digest(&saved)?.0 != hash {
            return Err("La copie source mémorisée a été modifiée ; apprentissage arrêté sans écraser l'historique".into());
        }
        if source_digest(&saved)?.0 != hash {
            return Err("La source a changé pendant sa copie ; reprendre l'importation avant l'apprentissage".into());
        }
        source_snapshot = Some(saved);
    }
    let (context_id, geometry_id) = context_identity(original, &source)?;
    let contexts = memory["contexts"].as_array_mut().unwrap();
    let context_index = if let Some(index) =
        contexts.iter().position(|r| r["context_id"] == context_id)
    {
        index
    } else {
        if contexts.len() >= MAX_CONTEXTS {
            return Err("La mémoire a atteint 1000 contextes ; archiver explicitement les expériences avant de poursuivre".into());
        }
        contexts.push(json!({"context_id":context_id,"geometry_id":geometry_id,"numerics_version":CONTEXT_NUMERICS_VERSION,"project":original,"source":source,"created_at":now(),"updated_at":now(),"observations":[],"best_recipe_id":null}));
        contexts.len() - 1
    };
    let remembered = if use_memory {
        remembered_recipe(&memory, context_index)
    } else {
        None
    };
    let mut queue = initial_candidates(original, remembered.as_ref())?;
    let model_path = memory_path.join("model.json");
    let existing_model: Option<Value> = if use_memory && model_path.is_file() {
        Some(
            serde_json::from_slice(&fs::read(&model_path).map_err(|e| e.to_string())?)
                .map_err(|e| format!("Modèle mémorisé illisible : {e}"))?,
        )
    } else {
        None
    };
    // Extract CAD information once before predictions; target mesh quality and
    // target connectivity must never influence the candidate's input features.
    let mut historic_report = memory["contexts"][context_index]
        .get("last_geometry_report")
        .cloned();
    let mut representation_notes = Vec::new();
    if historic_report
        .as_ref()
        .is_none_or(|r| r["geometry_descriptor"]["feature_schema"] != crate::representation::SCHEMA)
    {
        let mut inspect = request.clone();
        inspect["operation"] = json!("inspect");
        inspect["output_dir"] = json!(output.join("geometry_reference"));
        if let Some(source) = &source_snapshot {
            inspect["source"] = json!(source);
        }
        match crate::backend::run(&inspect) {
            Ok(report) => {
                let reference = reference_report(&report, original);
                memory["contexts"][context_index]["last_geometry_report"] = reference.clone();
                historic_report = Some(reference);
            }
            Err(error) => {
                representation_notes.push(format!("Inspection de référence indisponible : {error}. Les entrées restent paramétriques ; aucun résultat de maillage cible n'est utilisé pour les remplacer."));
                historic_report = None;
            }
        }
    }
    // A corpus-trained model and the live replay model have separate weights.
    // Either may rank candidates, but both remain subordinate to native checks.
    let mut guidance_notes = Vec::new();
    let corpus_model: Option<Value> = if use_memory {
        request["corpus_model"].as_str().and_then(|path| {
            let loaded = fs::metadata(path)
                .map_err(|e| e.to_string())
                .and_then(|meta| {
                    if meta.len() > 16 * 1024 * 1024 {
                        return Err("Modèle corpus trop volumineux".to_string());
                    }
                    serde_json::from_slice::<Value>(&fs::read(path).map_err(|e| e.to_string())?)
                        .map_err(|e| e.to_string())
                });
            match loaded {
                Ok(value) if value["feature_schema"] == crate::representation::SCHEMA => Some(value),
                Ok(_) => { guidance_notes.push("Modèle du corpus ignoré : schéma spatial incompatible avec les 128 entrées de cette version".into()); None },
                Err(error) => {
                    guidance_notes.push(format!("Modèle du corpus ignoré : {error}"));
                    None
                }
            }
        })
    } else {
        None
    };
    let protected = queue.iter().take_while(|c| c.from_memory).count().max(1);
    let mut corpus_predictions = 0usize;
    let mut local_predictions = 0usize;
    let mut ranked: Vec<_> = queue
        .into_iter()
        .enumerate()
        .map(|(index, candidate)| {
            let input = features(original, &candidate, historic_report.as_ref());
            let corpus_prediction = corpus_model.as_ref().and_then(|model| {
                match crate::dataset_model::predict(model, &input) {
                    Ok(value) => value,
                    Err(error) => {
                        if guidance_notes.is_empty() {
                            guidance_notes.push(format!("Classement corpus refusé : {error}"));
                        }
                        None
                    }
                }
            });
            let prediction = if let Some(value) = corpus_prediction {
                corpus_predictions += 1;
                Some(value)
            } else {
                existing_model
                    .as_ref()
                    .and_then(|model| model::predict(model, &input))
                    .map(|value| {
                        local_predictions += 1;
                        value
                    })
            };
            (index, candidate, prediction.unwrap_or(0.5))
        })
        .collect();
    ranked.sort_by(|(i, _, a), (j, _, b)| {
        if *i < protected || *j < protected {
            i.cmp(j)
        } else {
            b.total_cmp(a).then(i.cmp(j))
        }
    });
    queue = ranked
        .into_iter()
        .map(|(_, candidate, _)| candidate)
        .collect();
    let guidance_source = if corpus_predictions > 0 {
        "corpus_streaming_model"
    } else if local_predictions > 0 {
        "continuous_replay_model"
    } else {
        "deterministic_search_no_validated_model"
    };
    let mut tried = BTreeSet::new();
    let mut trials = Vec::new();
    let mut best: Option<(Value, PathBuf, Value, String)> = None;
    let mut memory_reused = false;
    let mut adaptations = 0usize;
    let mut notes = vec!["Recherche bornée de stratégies et de tailles ; aucune optimalité globale n'est garantie".to_string(), "Les zones de fortes contraintes doivent être désignées ou provenir d'un calcul externe ; le mailleur ne résout pas la mécanique".to_string(), "memory.json indexe le dernier état de chaque recette ; events.jsonl conserve chaque essai terminé, succès et échecs, y compris les réemplois".to_string()];
    notes.extend(representation_notes);
    notes.extend(guidance_notes);
    if !use_memory {
        notes.push("Mémoire utilisateur désactivée ; les essais de cette exécution sont isolés dans son dossier de diagnostic".into());
    }
    while trials.len() < max_trials && !queue.is_empty() {
        if !trials.is_empty() && started.elapsed().as_secs_f64() >= max_seconds {
            notes.push("Budget de temps atteint entre deux essais ; le traitement d'un essai est borné par le délai maximal de l'interface".into());
            break;
        }
        let current = queue.pop_front().unwrap();
        let mut numerical = current.recipe.clone();
        numerical.as_object_mut().unwrap().remove("action_id");
        let recipe_id = digest(&numerical)?;
        if !tried.insert(recipe_id.clone()) {
            continue;
        }
        if observation_count(&memory) >= MAX_OBSERVATIONS
            && !memory["contexts"][context_index]["observations"]
                .as_array()
                .unwrap()
                .iter()
                .any(|r| r["recipe_id"] == recipe_id)
        {
            notes.push("Limite de 32000 observations atteinte ; historique conservé sans suppression automatique".into());
            break;
        }
        let index = trials.len() + 1;
        let directory = output.join("trials").join(format!("trial-{index:03}"));
        let mut native = request.clone();
        native["operation"] = json!("mesh");
        native["project"] = current.project.clone();
        native["output_dir"] = json!(directory);
        if let Some(source) = &source_snapshot {
            native["source"] = json!(source);
        }
        let trial_started = Instant::now();
        let evaluated = crate::backend::run(&native);
        let seconds = trial_started.elapsed().as_secs_f64();
        memory_reused |= current.from_memory;
        let mut trial = json!({"index":index,"recipe_id":recipe_id,"action_id":current.recipe["action_id"],"algorithm":current.project["mesh"]["algorithm"],"size":current.project["mesh"]["size"],"local_refinement":current.project["local_sizes"].as_array().map_or(0,Vec::len)+current.project["refinement_regions"].as_array().map_or(0,Vec::len),"from_memory":current.from_memory,"seconds":seconds});
        let input;
        match evaluated {
            Ok(report) => {
                let score = crate::quality::score(&report)?;
                let accepted = report["quality"]["accepted"].as_bool().unwrap_or(false);
                trial["success"] = json!(true);
                trial["accepted"] = json!(accepted);
                trial["quality"] = report["quality"].clone();
                trial["score"] = json!(score);
                trial["elements"] = report["elements"].clone();
                input = features(original, &current, historic_report.as_ref());
                if accepted
                    && best.as_ref().map_or(true, |(old, _, _, _)| {
                        crate::quality::compare_reports(&report, old).is_gt()
                    })
                {
                    let chosen = chosen_project(original, &current);
                    best = Some((report.clone(), directory.clone(), chosen, recipe_id.clone()));
                }
                if adaptations < 4 && (index == 1 || index % 3 == 0) {
                    if let Some(adapted) = adaptive_candidate(original, &current, &report, index)? {
                        queue.push_front(adapted);
                        adaptations += 1;
                    }
                }
            }
            Err(error) => {
                input = features(original, &current, historic_report.as_ref());
                trial["success"] = json!(false);
                trial["accepted"] = json!(false);
                trial["score"] = json!(0.);
                trial["error"] = json!(error);
            }
        }
        let mut observation = trial.clone();
        if observation["accepted"] == true {
            observation["resulting_context_id"] =
                json!(context_identity(&chosen_project(original, &current), &source)?.0);
        }
        observation["recipe"] = current.recipe;
        observation["features"] = json!(input.to_vec());
        observation["feature_schema"] = json!(crate::representation::SCHEMA);
        observation["attempts"] = json!(1);
        observation["updated_at"] = json!(now());
        save_observation(&mut memory, context_index, observation.clone())?;
        append_event(&memory_path, &mut memory, context_index, &observation)?;
        atomic_json(&memory_path.join("memory.json"), &memory)?;
        trials.push(trial);
        // Diagnostics survive cancellation after any completed experiment.
        atomic_json(
            &output.join("experience.progress.json"),
            &json!({"context_id":context_id,"geometry_id":geometry_id,"executed_trials":trials.len(),"max_trials":max_trials,"trials":trials}),
        )?;
    }
    let examples = model_examples(&memory);
    let learning = match model::update(existing_model.as_ref(), &examples, epochs, seed) {
        Ok(mut learned) => {
            let version = memory["model_version"].as_u64().unwrap_or(0) + 1;
            learned["model_version"] = json!(version);
            learned["updated_at"] = json!(now());
            fs::create_dir_all(memory_path.join("models")).map_err(|e| e.to_string())?;
            atomic_json(
                &memory_path
                    .join("models")
                    .join(format!("model-{version:06}.json")),
                &learned,
            )?;
            atomic_json(&model_path, &learned)?;
            memory["model_version"] = json!(version);
            atomic_json(&memory_path.join("memory.json"), &memory)?;
            learned
        }
        Err(error) => {
            notes.push(format!("Mise à jour du modèle refusée ; expériences et modèle précédent conservés : {error}"));
            existing_model.unwrap_or(Value::Null)
        }
    };
    let mut experience = json!({"schema":"atelier.experience.result.v1","context_id":context_id,"geometry_id":geometry_id,"memory_reused":memory_reused,"use_memory":use_memory,"executed_trials":trials.len(),"max_trials":max_trials,"max_seconds":max_seconds,"elapsed_seconds":started.elapsed().as_secs_f64(),"accepted":best.is_some(),"history_contexts":memory["contexts"].as_array().unwrap().len(),"history_observations":observation_count(&memory),"learning":learning,"trials":trials,"notes":notes,"chosen_project":null,"chosen_recipe_id":null});
    experience["guidance_source"] = json!(guidance_source);
    experience["numerics_version"] = json!(CONTEXT_NUMERICS_VERSION);
    experience["guidance"] = json!({"source":guidance_source,"corpus_predictions":corpus_predictions,"local_predictions":local_predictions,"memory_isolation":!use_memory,"never_bypasses_native_quality":true});
    experience["representation"] = json!({"feature_schema":crate::representation::SCHEMA,"input_count":model::INPUTS,"reference":historic_report.as_ref().map(|r|r["geometry_descriptor"].clone()),"target_quality_or_connectivity_used":false});
    experience["representation_migration"] = representation_migration;
    if let Some((mut report, directory, chosen, recipe_id)) = best {
        copy_winner(&directory, &output, &chosen)?;
        experience["chosen_project"] = chosen;
        experience["chosen_recipe_id"] = json!(recipe_id);
        report["operation"] = json!("learn");
        report["experience"] = experience.clone();
        report["quality"]["policy"]["strict"] =
            report["experience"]["chosen_project"]["quality_policy"]["strict"].clone();
        report["native_kernel"] = json!({"language":"rust","version":env!("CARGO_PKG_VERSION"),"geometry_mesher":"Gmsh/OpenCASCADE native C ABI","training":"Rust cumulative replay MLP; predictions only rank checked experiments"});
        atomic_json(&output.join("experience.json"), &experience)?;
        atomic_json(&output.join("report.json"), &report)?;
        Ok(report)
    } else {
        atomic_json(&output.join("experience.json"), &experience)?;
        atomic_json(
            &output.join("report.json"),
            &json!({"schema":"atelier.report.v1","operation":"learn","accepted":false,"quality":{"accepted":false},"experience":experience}),
        )?;
        Err(format!("Aucun maillage admissible trouvé en {} essais. Les échecs ont été mémorisés ; consulter experience.json pour les critères non satisfaits.",trials.len()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn project() -> Value {
        json!({"schema_version":1,"name":"A","mode":"planar","units":{"input":"mm","internal":"m"},"geometry":{"kind":"plate","width":4.,"height":3.},"mesh":{"size":0.8,"min_size":0.1,"algorithm":"automatic","order":1,"max_elements":5000},"critical_zones":[]})
    }

    #[test]
    fn precise_json_release_separates_legacy_context_without_erasing_observations() {
        let mut p = project();
        p["name"] = json!("Nom sans effet numérique");
        p["learning"] = json!({"max_trials":8});
        p["loads"] = json!({"force":125});
        let source = json!({"sha256":"saved-source","bytes":42});
        let mut numerical = p.clone();
        for key in ["name", "learning", "operation"] {
            numerical.as_object_mut().unwrap().remove(key);
        }
        let previous = digest(
            &json!({"engine":ENGINE,"core":"1.0.0-rc.4","project":numerical,"source":source}),
        )
        .unwrap();
        let current=context_identity(&p,&source).unwrap().0;
        assert_ne!(current,previous);
        let observations=json!([{"recipe_id":"legacy-recipe","accepted":true,"recipe":{"mesh":p["mesh"]},"resulting_context_id":previous}]);
        let memory=json!({"contexts":[{"context_id":previous,"observations":observations},
            {"context_id":current,"numerics_version":CONTEXT_NUMERICS_VERSION,"observations":[]}]});
        assert!(remembered_recipe(&memory,1).is_none());
        assert_eq!(memory["contexts"][0]["observations"],observations);
        assert!(memory["contexts"][1]["observations"].as_array().unwrap().is_empty());
        p["loads"]["force"] = json!(126);
        assert_ne!(context_identity(&p, &source).unwrap().0, previous);
    }

    #[test]
    fn legacy_inputs_are_reconstructed_without_erasing_the_raw_history() {
        let p = project();
        let old = vec![0.25; 16];
        let mut memory = json!({"contexts":[{"project":p,"geometry_id":"one","observations":[{"features":old,"recipe":{"mesh":p["mesh"]},"score":0.8}]}]});
        let migration = migrate_feature_history(&mut memory);
        assert_eq!(migration["legacy_examples_reencoded"], 1);
        assert_eq!(migration["legacy_examples_excluded_from_new_training"], 0);
        assert_eq!(
            memory["contexts"][0]["observations"][0]["legacy_features"]
                .as_array()
                .unwrap()
                .len(),
            16
        );
        assert_eq!(
            memory["contexts"][0]["observations"][0]["features"]
                .as_array()
                .unwrap()
                .len(),
            128
        );
        assert_eq!(model_examples(&memory).len(), 1);
        let second = migrate_feature_history(&mut memory);
        assert_eq!(second["legacy_examples_reencoded"], 0);
        assert_eq!(second["legacy_examples_reencoded_total"], 1);
    }

    #[test]
    fn imported_legacy_history_without_cad_catalogue_is_preserved_but_not_invented() {
        let mut p = project();
        p["geometry"] = json!({"kind":"import"});
        let mut memory = json!({"contexts":[{"project":p,"geometry_id":"import","observations":[{"features":vec![0.;16],"recipe":{"mesh":p["mesh"]},"score":0.8}]}]});
        let migration = migrate_feature_history(&mut memory);
        assert_eq!(migration["legacy_examples_excluded_from_new_training"], 1);
        assert_eq!(
            memory["contexts"][0]["observations"][0]["features"]
                .as_array()
                .unwrap()
                .len(),
            16
        );
        assert!(model_examples(&memory).is_empty());
    }

    #[test]
    fn identity_ignores_only_display_and_search_preferences() {
        let a = project();
        let mut b = a.clone();
        b["name"] = json!("B");
        b["learning"] = json!({"max_trials":1});
        assert_eq!(
            context_identity(&a, &Value::Null).unwrap(),
            context_identity(&b, &Value::Null).unwrap()
        );
        b["critical_zones"] =
            json!([{"name":"contact","dimension":1,"tags":[1],"minimum_quality":0.5}]);
        assert_ne!(
            context_identity(&a, &Value::Null).unwrap().0,
            context_identity(&b, &Value::Null).unwrap().0
        );
        b = a.clone();
        b["loads"] = json!({"force":100});
        assert_ne!(
            context_identity(&a, &Value::Null).unwrap().0,
            context_identity(&b, &Value::Null).unwrap().0
        );
        assert_eq!(
            context_identity(&a, &Value::Null).unwrap().1,
            context_identity(&b, &Value::Null).unwrap().1
        );
    }

    #[test]
    fn source_identity_depends_on_contents_not_filename() {
        let a = project();
        let first = json!({"sha256":"abc","extension":"step","bytes":3});
        let second = json!({"sha256":"def","extension":"step","bytes":3});
        assert_ne!(
            context_identity(&a, &first).unwrap(),
            context_identity(&a, &second).unwrap()
        );
    }

    #[test]
    fn candidates_obey_minimum_and_skip_unsupported_volume_frontal() {
        let mut p = project();
        p["mode"] = json!("volume");
        p["mesh"]["algorithm"] = json!("frontal");
        p["local_sizes"] = json!([{"dimension":2,"tag":1,"size":0.2}]);
        let rows = initial_candidates(&p, None).unwrap();
        assert!(!rows.is_empty());
        for row in rows {
            assert_ne!(row.project["mesh"]["algorithm"], "frontal");
            assert!(row.project["mesh"]["size"].as_f64().unwrap() >= 0.1);
            assert_eq!(row.project["quality_policy"]["strict"], false);
        }
    }

    #[test]
    fn adapting_uses_internal_to_input_units_and_never_crosses_minimum() {
        let p = project();
        let c = candidate(&p, recipe(&p, "baseline"), false).unwrap();
        let report =
            json!({"quality":{"poor_samples":[{"center":[0.002,0.001,0.],"quality":0.1}]}});
        let adapted = adaptive_candidate(&p, &c, &report, 1).unwrap().unwrap();
        assert_eq!(
            adapted.project["refinement_regions"][0]["center"],
            json!([2., 1., 0.])
        );
        assert!(
            adapted.project["refinement_regions"][0]["size"]
                .as_f64()
                .unwrap()
                >= 0.1
        );
        let f = features(&p, &adapted, None);
        assert!(f.iter().all(|v| v.is_finite() && (0. ..=1.).contains(v)));
    }

    #[test]
    fn repeated_recipe_retains_attempt_count_and_failed_experience() {
        let mut memory = json!({"contexts":[{"observations":[]}],"generation":0});
        let observation =
            json!({"recipe_id":"one","accepted":false,"attempts":1,"score":0.,"error":"failure"});
        save_observation(&mut memory, 0, observation.clone()).unwrap();
        save_observation(&mut memory, 0, observation).unwrap();
        assert_eq!(
            memory["contexts"][0]["observations"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(memory["contexts"][0]["observations"][0]["attempts"], 2);
        assert_eq!(memory["contexts"][0]["best_recipe_id"], Value::Null);
    }

    #[test]
    fn journal_recovers_after_index_interruption_and_incomplete_tail() {
        let directory = std::env::temp_dir().join(format!(
            "mailleur-journal-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&directory).unwrap();
        let mut memory = json!({"schema":MEMORY_SCHEMA,"generation":0,"contexts":[{"context_id":"context","geometry_id":"geometry","observations":[]}]});
        let first = json!({"recipe_id":"first","accepted":false,"attempts":1,"score":0.,"error":"échec conservé"});
        save_observation(&mut memory, 0, first.clone()).unwrap();
        append_event(&directory, &mut memory, 0, &first).unwrap();
        atomic_json(&directory.join("memory.json"), &memory).unwrap();
        let second = json!({"recipe_id":"second","accepted":false,"attempts":1,"score":0.,"error":"deuxième"});
        save_observation(&mut memory, 0, second.clone()).unwrap();
        append_event(&directory, &mut memory, 0, &second).unwrap();
        // Simulate cancellation between journal fsync and index rename.
        let mut recovered = load_memory(&directory).unwrap();
        assert_eq!(observation_count(&recovered), 2);
        assert_eq!(recovered["journal_events"], 2);
        let mut file = OpenOptions::new()
            .append(true)
            .open(directory.join("events.jsonl"))
            .unwrap();
        file.write_all(&[b'{', b'"', 0xc3]).unwrap();
        drop(file);
        recovered = load_memory(&directory).unwrap();
        assert_eq!(recovered["journal_incomplete_tail"], true);
        save_observation(&mut recovered, 0, first.clone()).unwrap();
        append_event(&directory, &mut recovered, 0, &first).unwrap();
        atomic_json(&directory.join("memory.json"), &recovered).unwrap();
        let final_state = load_memory(&directory).unwrap();
        assert_eq!(final_state["journal_events"], 3);
        assert_eq!(final_state["contexts"][0]["observations"][0]["attempts"], 2);
        let log = fs::read_to_string(directory.join("events.jsonl")).unwrap();
        assert_eq!(log.lines().count(), 3);
        assert!(log.contains("échec conservé"));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn bounded_atomic_write_preserves_previous_index_and_serializes_without_loss() {
        let directory = std::env::temp_dir().join(format!(
            "mailleur-bounded-index-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&directory).unwrap();
        let path = directory.join("memory.json");
        fs::write(&path, b"previous complete index").unwrap();
        let value = json!({"schema":MEMORY_SCHEMA,"contexts":[{"name":"équerre","observations":[{"quality":0.85,"text":"x".repeat(200)}]}]});
        assert!(atomic_json_bounded(&path, &value, Some(64), true).is_err());
        assert_eq!(fs::read(&path).unwrap(), b"previous complete index");
        assert_eq!(fs::read_dir(&directory).unwrap().count(), 1);
        atomic_json(&path, &value).unwrap();
        let bytes = fs::read(&path).unwrap();
        assert_eq!(serde_json::from_slice::<Value>(&bytes).unwrap(), value);
        assert_eq!(bytes.iter().filter(|&&b| b == b'\n').count(), 1);
        assert!(bytes.len() < serde_json::to_vec_pretty(&value).unwrap().len());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn integer_identifiers_are_not_rounded_into_one_identity() {
        assert_ne!(
            digest(&json!(9_007_199_254_740_992u64)).unwrap(),
            digest(&json!(9_007_199_254_740_993u64)).unwrap()
        );
        assert_eq!(digest(&json!(1)).unwrap(), digest(&json!(1.)).unwrap());
    }

    #[test]
    fn winning_settings_have_an_exact_context_alias_but_new_contacts_do_not() {
        let original = project();
        let mut changed = original.clone();
        changed["mesh"]["algorithm"] = json!("frontal");
        let current = candidate(&original, recipe(&changed, "frontal:size:1"), false).unwrap();
        let chosen = chosen_project(&original, &current);
        let id = context_identity(&chosen, &Value::Null).unwrap().0;
        let mut memory = json!({"contexts":[{"context_id":"original","observations":[{"recipe_id":"recipe","recipe":current.recipe,"accepted":true,"resulting_context_id":id,"quality":{"accepted":true,"minimum":0.8,"mean":0.9},"elements":12}]},{"context_id":id,"observations":[]}]});
        assert!(remembered_recipe(&memory, 1).is_some());
        let mut different = chosen;
        different["critical_zones"] =
            json!([{"name":"new contact","dimension":1,"tags":[1],"minimum_quality":0.5}]);
        memory["contexts"][1]["context_id"] =
            json!(context_identity(&different, &Value::Null).unwrap().0);
        assert!(remembered_recipe(&memory, 1).is_none());
    }

    #[test]
    fn exclusive_lock_is_released_on_drop() {
        let path =
            std::env::temp_dir().join(format!("mailleur-memory-lock-{}", std::process::id()));
        let first = MemoryLock::acquire(&path).unwrap();
        assert!(MemoryLock::acquire(&path).is_err());
        drop(first);
        assert!(MemoryLock::acquire(&path).is_ok());
        let _ = fs::remove_dir_all(path);
    }

    #[test]
    fn replacing_output_clears_mesh_but_never_source_or_memory() {
        let directory = std::env::temp_dir().join(format!(
            "mailleur-output-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(directory.join("memory")).unwrap();
        fs::write(directory.join("mesh.msh"), b"previous mesh").unwrap();
        fs::write(directory.join("model.brep"), b"source CAD").unwrap();
        assert!(clear_previous_output(
            &directory,
            Some(&directory.join("model.brep")),
            &directory.join("memory")
        )
        .is_err());
        assert!(directory.join("mesh.msh").is_file());
        fs::write(directory.join("source.step"), b"CAD").unwrap();
        clear_previous_output(
            &directory,
            Some(&directory.join("source.step")),
            &directory.join("memory"),
        )
        .unwrap();
        assert!(!directory.join("mesh.msh").exists());
        assert!(!directory.join("model.brep").exists());
        assert!(directory.join("source.step").is_file());
        assert!(directory.join("memory").is_dir());
        fs::remove_dir_all(directory).unwrap();
    }
}
