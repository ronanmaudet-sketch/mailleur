//! Bounded native corpus production with O(1) planning and segmented journals.
//! A plan never claims that its configurations have been evaluated. Numerical
//! labels always come from real Gmsh/OpenCASCADE calls through the Rust backend.
#[path = "corpus_generator.rs"]
pub mod generator;

use generator::{case_at, hash_json, Plan, GENERATOR_VERSION};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

type R<T> = Result<T, String>;
const SCHEMA: &str = "atelier.corpus.v1";
const MAX_LINE: usize = 8 * 1024 * 1024;

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
fn integer(v: &Value, key: &str, default: u64, min: u64, max: u64) -> R<u64> {
    let n = v
        .get(key)
        .map(|x| {
            x.as_u64()
                .ok_or_else(|| format!("{key} doit être un entier"))
        })
        .transpose()?
        .unwrap_or(default);
    if !(min..=max).contains(&n) {
        Err(format!("{key} doit être compris entre {min} et {max}"))
    } else {
        Ok(n)
    }
}
fn boolean(v: &Value, key: &str, default: bool) -> R<bool> {
    v.get(key)
        .map(|x| {
            x.as_bool()
                .ok_or_else(|| format!("{key} doit être booléen"))
        })
        .transpose()
        .map(|x| x.unwrap_or(default))
}

fn atomic_json(path: &Path, value: &Value) -> R<()> {
    let parent = path.parent().ok_or("Dossier absent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id()
    ));
    let mut file = File::create(&temporary).map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut file, value).map_err(|e| e.to_string())?;
    file.write_all(b"\n").map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())?;
    fs::rename(temporary, path).map_err(|e| e.to_string())
}

struct Lock {
    _file: File,
}
impl Lock {
    fn acquire(directory: &Path) -> R<Self> {
        Self::try_acquire(directory)?.ok_or_else(|| {
            "Le corpus est déjà en cours de modification ; réessayer après la fin du lot".into()
        })
    }
    fn try_acquire(directory: &Path) -> R<Option<Self>> {
        fs::create_dir_all(directory).map_err(|e| e.to_string())?;
        let path = directory.join("corpus.lock");
        if path.is_symlink() {
            return Err("Le verrou du corpus ne doit pas être un lien symbolique".into());
        }
        #[cfg(windows)]
        let file = {
            use std::os::windows::fs::OpenOptionsExt;
            match OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .share_mode(0)
                .open(path)
            {
                Ok(file) => file,
                Err(error) if matches!(error.raw_os_error(), Some(32 | 33)) => return Ok(None),
                Err(error) => return Err(format!("Verrou du corpus inaccessible : {error}")),
            }
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
            if unsafe { flock(file.as_raw_fd(), 2 | 4) } != 0 {
                let error = std::io::Error::last_os_error();
                if error.kind() == std::io::ErrorKind::WouldBlock {
                    return Ok(None);
                }
                return Err(format!("Verrou du corpus inaccessible : {error}"));
            }
        }
        Ok(Some(Self { _file: file }))
    }
}

fn read_json(path: &Path) -> R<Value> {
    if path.is_symlink() {
        return Err(format!("Lien symbolique interdit pour {}", path.display()));
    }
    const LIMIT: u64 = 4 * 1024 * 1024;
    let file = File::open(path).map_err(|e| e.to_string())?;
    if file.metadata().map_err(|e| e.to_string())?.len() > LIMIT {
        return Err(format!(
            "Métadonnées du corpus trop volumineuses : {}",
            path.display()
        ));
    }
    let mut bytes = Vec::new();
    file.take(LIMIT + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > LIMIT {
        return Err("Les métadonnées du corpus ont dépassé 4 Mio pendant leur lecture".into());
    }
    serde_json::from_slice(&bytes).map_err(|e| format!("Métadonnées de corpus invalides : {e}"))
}
fn load_manifest(root: &Path) -> R<(Value, Plan)> {
    let value = read_json(&root.join("manifest.json"))?;
    if value["schema"] != SCHEMA
        || value["generator_version"] != GENERATOR_VERSION
        || value["feature_schema"] != crate::representation::SCHEMA
    {
        return Err(
            "Version de corpus incompatible ; les données existantes sont conservées".into(),
        );
    }
    let mut stored = value["plan"].clone();
    if stored.is_object() && stored.get("profile").is_none() {
        stored["profile"] = json!("general");
    }
    let plan = Plan::parse(&stored)?;
    if value["planned_total"].as_u64() != Some(plan.total()) {
        return Err("Nombre de configurations du manifeste incohérent".into());
    }
    if value["plan_id"] != hash_json(&json!({"generator":GENERATOR_VERSION,"plan":plan})) {
        return Err("L'identité du plan ne correspond pas à ses paramètres ; les données existantes sont conservées".into());
    }
    Ok((value, plan))
}
fn fresh_checkpoint() -> Value {
    json!({"schema":"atelier.corpus.checkpoint.v1","next_index":0,"completed_cases":0,"generated_cases":0,"meshed_cases":0,"admissible_cases":0,"accepted_actions":0,"failed_actions":0,"backend_evaluations":0,"split_counts":{"train":0,"validation":0,"test":0},"origin_counts":{"structured":0,"random":0},"family_counts":{},"updated_at":now()})
}
fn checkpoint(root: &Path, plan: &Plan) -> R<Value> {
    let path = root.join("checkpoint.json");
    if !path.exists() {
        return Ok(fresh_checkpoint());
    }
    let value = read_json(&path)?;
    if value["schema"] != "atelier.corpus.checkpoint.v1"
        || value["next_index"].as_u64().is_none()
        || value["next_index"].as_u64().unwrap() > plan.total()
        || value["completed_cases"] != value["next_index"]
    {
        return Err("Point de reprise du corpus incohérent ; aucun résultat n'est supprimé".into());
    }
    for key in ["accepted_actions", "failed_actions", "backend_evaluations"] {
        if value[key].as_u64().is_none() {
            return Err(format!("Compteur {key} du corpus invalide"));
        }
    }
    for key in ["split_counts", "origin_counts", "family_counts"] {
        if !value[key].is_object() {
            return Err(format!("Compteur {key} du corpus invalide"));
        }
    }
    Ok(value)
}
fn increment(value: &mut Value, key: &str, by: u64) {
    value[key] = json!(value[key].as_u64().unwrap_or(0) + by);
}
fn apply_record(cp: &mut Value, record: &Value) -> R<()> {
    let index = record["index"]
        .as_u64()
        .ok_or("Indice de cas absent du journal")?;
    if index != cp["next_index"].as_u64().unwrap_or(0) {
        return Err("Le journal n'est pas un préfixe contigu du plan".into());
    }
    let actions = record["actions"]
        .as_array()
        .ok_or("Actions absentes du journal")?;
    let accepted = actions.iter().filter(|a| a["accepted"] == true).count() as u64;
    increment(cp, "generated_cases", 1);
    if actions.iter().any(|a| a["success"] == true) {
        increment(cp, "meshed_cases", 1);
    }
    if accepted > 0 {
        increment(cp, "admissible_cases", 1);
    }
    increment(cp, "accepted_actions", accepted);
    increment(cp, "failed_actions", actions.len() as u64 - accepted);
    increment(
        cp,
        "backend_evaluations",
        record["backend_evaluations"].as_u64().unwrap_or(0),
    );
    let split = record["split"]
        .as_str()
        .ok_or("Partition absente du journal")?;
    if !["train", "validation", "test"].contains(&split) {
        return Err("Partition de corpus inconnue".into());
    }
    increment(&mut cp["split_counts"], split, 1);
    let origin = record["origin"]
        .as_str()
        .ok_or("Origine absente du journal")?;
    if !["structured", "random"].contains(&origin) {
        return Err("Origine de corpus inconnue".into());
    }
    increment(&mut cp["origin_counts"], origin, 1);
    let family = record["family"]
        .as_str()
        .ok_or("Famille absente du journal")?;
    increment(&mut cp["family_counts"], family, 1);
    cp["next_index"] = json!(index + 1);
    cp["completed_cases"] = json!(index + 1);
    cp["last_case_id"] = record["case_id"].clone();
    cp["updated_at"] = json!(now());
    Ok(())
}
fn shard(root: &Path, index: u64, plan: &Plan) -> PathBuf {
    root.join("shards")
        .join(format!("cases-{:06}.jsonl", index / plan.shard_size))
}

/// A cursor never substitutes an unchecked byte offset for journal validation.
/// Its prefix digest is checked before any new row is decoded or any partial
/// tail is truncated. Compared with rc4 this avoids rebuilding every JSON Value
/// in the already committed part of the active shard.
struct JournalCursor {
    number: u64,
    bytes: u64,
    records: u64,
    digest: Sha256,
}
impl JournalCursor {
    fn value(&self, last_case_id: &Value) -> Value {
        json!({"schema":"atelier.corpus.cursor.v1","shard":self.number,
            "offset":self.bytes,"records":self.records,
            "prefix_sha256":format!("{:x}",self.digest.clone().finalize()),
            "last_case_id":last_case_id})
    }
    fn stamp(&self, cp: &mut Value) {
        cp["journal_cursor"] = self.value(&cp["last_case_id"]);
    }
}
struct Recovery {
    cases: u64,
    decoded_records: u64,
    verified_bytes: u64,
    upgraded: bool,
    cursor: Option<JournalCursor>,
}
impl Recovery {
    fn value(&self) -> Value {
        json!({"recovered_cases":self.cases,"decoded_records":self.decoded_records,
            "verified_bytes":self.verified_bytes,"legacy_checkpoint_upgraded":self.upgraded,
            "validation":"sha256_durable_prefix_then_decode_uncommitted_tail"})
    }
}

fn cursor_prefix(root: &Path, plan: &Plan, cp: &Value) -> R<Option<JournalCursor>> {
    let Some(raw) = cp.get("journal_cursor") else {
        return Ok(None);
    };
    let next = cp["next_index"]
        .as_u64()
        .ok_or("Indice de reprise absent")?;
    if next == 0
        || raw["schema"] != "atelier.corpus.cursor.v1"
        || raw["shard"].as_u64() != Some((next - 1) / plan.shard_size)
        || raw["records"].as_u64() != Some((next - 1) % plan.shard_size + 1)
        || raw["last_case_id"] != cp["last_case_id"]
    {
        return Err("Curseur de reprise incohérent ; aucun résultat n'est supprimé".into());
    }
    let bytes = raw["offset"]
        .as_u64()
        .filter(|v| *v > 0)
        .ok_or("Offset du curseur de reprise invalide")?;
    let records = raw["records"].as_u64().unwrap();
    if bytes > records.saturating_mul(MAX_LINE as u64) {
        return Err("Offset du curseur hors du segment borné".into());
    }
    let expected = raw["prefix_sha256"]
        .as_str()
        .filter(|s| {
            s.len() == 64
                && s.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        })
        .ok_or("Empreinte du curseur de reprise invalide")?;
    let number = raw["shard"].as_u64().unwrap();
    let path = shard(root, next - 1, plan);
    if path.is_symlink() {
        return Err("Un segment du corpus est un lien symbolique".into());
    }
    let mut file = File::open(&path).map_err(|e| format!("Segment de reprise absent : {e}"))?;
    if file.metadata().map_err(|e| e.to_string())?.len() < bytes {
        return Err("Le point de reprise dépasse les données durables du segment".into());
    }
    let mut digest = Sha256::new();
    let mut remaining = bytes;
    let mut buffer = [0u8; 65536];
    let mut last = 0u8;
    while remaining > 0 {
        let take = remaining.min(buffer.len() as u64) as usize;
        file.read_exact(&mut buffer[..take])
            .map_err(|e| e.to_string())?;
        digest.update(&buffer[..take]);
        last = buffer[take - 1];
        remaining -= take as u64;
    }
    if last != b'\n' || format!("{:x}", digest.clone().finalize()) != expected {
        return Err(
            "Le préfixe durable du segment a été modifié ; reprise refusée sans suppression".into(),
        );
    }
    Ok(Some(JournalCursor {
        number,
        bytes,
        records,
        digest,
    }))
}

/// Existing v1 checkpoints are upgraded after one ordinary rc4 scan. A crash
/// can leave durable rows after the saved cursor; only those rows are decoded.
fn recover(root: &Path, plan: &Plan, cp: &mut Value) -> R<Recovery> {
    if root.join("shards").is_symlink() {
        return Err("Le dossier des segments ne doit pas être un lien symbolique".into());
    }
    let saved = cursor_prefix(root, plan, cp)?;
    let upgraded = saved.is_none() && cp["next_index"].as_u64().unwrap_or(0) > 0;
    let mut verified_bytes = saved.as_ref().map_or(0, |c| c.bytes);
    let mut number = saved.as_ref().map_or_else(
        || cp["next_index"].as_u64().unwrap_or(0).saturating_sub(1) / plan.shard_size,
        |c| c.number,
    );
    let mut first = saved;
    let mut latest = None;
    let mut recovered = 0;
    let mut decoded_records = 0;
    loop {
        let path = root.join("shards").join(format!("cases-{number:06}.jsonl"));
        if !path.exists() {
            if recovered == 0 && cp["next_index"].as_u64().unwrap_or(0) > number * plan.shard_size {
                return Err("Le segment attendu par le point de reprise a disparu ; le corpus est conservé sans ajouter de trou".into());
            }
            break;
        }
        if path.is_symlink() {
            return Err("Un segment du corpus est un lien symbolique".into());
        }
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .open(&path)
            .map_err(|e| e.to_string())?;
        let mut cursor = first.take().unwrap_or_else(|| JournalCursor {
            number,
            bytes: 0,
            records: 0,
            digest: Sha256::new(),
        });
        file.seek(SeekFrom::Start(cursor.bytes))
            .map_err(|e| e.to_string())?;
        let mut reader = BufReader::new(file.try_clone().map_err(|e| e.to_string())?);
        let mut bytes = Vec::new();
        let mut valid = cursor.bytes;
        let mut count = cursor.records;
        loop {
            bytes.clear();
            let n = reader
                .by_ref()
                .take((MAX_LINE + 1) as u64)
                .read_until(b'\n', &mut bytes)
                .map_err(|e| e.to_string())?;
            if n == 0 {
                break;
            }
            if bytes.len() > MAX_LINE {
                return Err("Une ligne de corpus dépasse 8 Mio".into());
            }
            if bytes.last() != Some(&b'\n') {
                file.set_len(valid).map_err(|e| e.to_string())?;
                file.seek(SeekFrom::Start(valid))
                    .map_err(|e| e.to_string())?;
                file.sync_all().map_err(|e| e.to_string())?;
                break;
            }
            let row: Value = serde_json::from_slice(&bytes)
                .map_err(|e| format!("Segment {} invalide : {e}", path.display()))?;
            decoded_records += 1;
            let expected = number * plan.shard_size + count;
            if row["schema"] != "atelier.corpus.case.v1"
                || row["index"].as_u64() != Some(expected)
                || expected >= plan.total()
            {
                return Err("Segment du corpus non contigu ou incompatible".into());
            }
            if expected >= cp["next_index"].as_u64().unwrap_or(0) {
                apply_record(cp, &row)?;
                recovered += 1;
            }
            count += 1;
            if count > plan.shard_size {
                return Err("Un segment contient trop de configurations".into());
            }
            valid += n as u64;
            verified_bytes += n as u64;
            cursor.digest.update(&bytes);
        }
        if cp["next_index"].as_u64().unwrap_or(0) > number * plan.shard_size + count {
            return Err("Le point de reprise dépasse les données durables du segment".into());
        }
        cursor.bytes = valid;
        cursor.records = count;
        if count > 0 {
            latest = Some(cursor);
        }
        if count < plan.shard_size {
            break;
        }
        number += 1;
    }
    if let Some(cursor) = &latest {
        cursor.stamp(cp);
    }
    if recovered > 0 || upgraded {
        atomic_json(&root.join("checkpoint.json"), cp)?;
    }
    Ok(Recovery {
        cases: recovered,
        decoded_records,
        verified_bytes,
        upgraded,
        cursor: latest,
    })
}

/// Keep one append handle and digest state during a batch. Every case still
/// reaches sync_all before the atomic checkpoint advances.
struct JournalWriter {
    cursor: Option<JournalCursor>,
    file: Option<File>,
    bytes: Vec<u8>,
}
impl JournalWriter {
    fn new(cursor: Option<JournalCursor>) -> Self {
        Self {
            cursor,
            file: None,
            bytes: Vec::new(),
        }
    }
    fn append(&mut self, root: &Path, plan: &Plan, record: &Value) -> R<()> {
        let index = record["index"].as_u64().ok_or("Indice de cas absent")?;
        let number = index / plan.shard_size;
        if self.cursor.as_ref().is_some_and(|c| c.number != number) {
            self.cursor = None;
            self.file = None;
        }
        let cursor = self.cursor.get_or_insert_with(|| JournalCursor {
            number,
            bytes: 0,
            records: 0,
            digest: Sha256::new(),
        });
        if cursor.records != index % plan.shard_size {
            return Err("Le curseur d'écriture ne correspond pas au prochain cas".into());
        }
        if self.file.is_none() {
            if root.join("shards").is_symlink() {
                return Err("Le dossier des segments ne doit pas être un lien symbolique".into());
            }
            let path = shard(root, index, plan);
            fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
            if path.is_symlink() {
                return Err("Un segment du corpus est un lien symbolique".into());
            }
            let file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(path)
                .map_err(|e| e.to_string())?;
            if file.metadata().map_err(|e| e.to_string())?.len() != cursor.bytes {
                return Err("Le segment a changé depuis sa validation ; écriture refusée".into());
            }
            self.file = Some(file);
        }
        self.bytes.clear();
        serde_json::to_writer(&mut self.bytes, record).map_err(|e| e.to_string())?;
        self.bytes.push(b'\n');
        if self.bytes.len() > MAX_LINE {
            return Err("Un cas de corpus dépasserait 8 Mio".into());
        }
        let file = self.file.as_mut().unwrap();
        file.write_all(&self.bytes).map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        cursor.digest.update(&self.bytes);
        cursor.bytes += self.bytes.len() as u64;
        cursor.records += 1;
        Ok(())
    }
    fn stamp(&self, cp: &mut Value) {
        if let Some(cursor) = &self.cursor {
            cursor.stamp(cp);
        }
    }
}

#[cfg(test)]
fn append_case(root: &Path, plan: &Plan, record: &Value) -> R<()> {
    let index = record["index"].as_u64().ok_or("Indice de cas absent")?;
    let path = shard(root, index, plan);
    fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    if path.is_symlink() {
        return Err("Un segment du corpus est un lien symbolique".into());
    }
    let mut bytes = serde_json::to_vec(record).map_err(|e| e.to_string())?;
    bytes.push(b'\n');
    if bytes.len() > MAX_LINE {
        return Err(
            "Un cas de corpus dépasserait 8 Mio ; augmenter la segmentation des observations"
                .into(),
        );
    }
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    file.write_all(&bytes).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())
}

fn report(root: &Path, manifest: &Value, plan: &Plan, cp: &Value, operation: &str) -> Value {
    let mut value = cp.clone();
    let next = cp["next_index"].as_u64().unwrap_or(0);
    value["schema"] = json!("atelier.corpus.report.v1");
    value["operation"] = json!(operation);
    value["corpus_dir"] = json!(root);
    value["planned_total"] = json!(plan.total());
    value["structured"] = json!(plan.structured);
    value["random"] = json!(plan.random);
    value["strategies"] = json!(plan.strategies);
    value["shard_size"] = json!(plan.shard_size);
    value["family_count"] = json!(generator::families(plan).len());
    value["profile"] = json!(plan.profile);
    value["remaining_cases"] = json!(plan.total() - next);
    value["done"] = json!(next == plan.total());
    value["manifest_path"] = json!(root.join("manifest.json"));
    value["plan_id"] = manifest["plan_id"].clone();
    value["planned_is_evaluated"] = json!(false);
    value["computation_backend"] = json!("rust");
    value["generator_version"] = json!(GENERATOR_VERSION);
    value["feature_schema"] = json!(crate::representation::SCHEMA);
    value["planned_split_counts"] = manifest["planned_split_counts"].clone();
    value["planned_mesh_evaluations"] = json!(plan.total() * plan.strategies);
    value
}

fn status_report(root: &Path, manifest: &Value, plan: &Plan, operation: &str) -> R<Value> {
    let lock = Lock::try_acquire(root)?;
    let mut cp = checkpoint(root, plan)?;
    let mut value;
    if lock.is_some() {
        let recovered = recover(root, plan, &mut cp)?;
        value = report(root, manifest, plan, &cp, operation);
        value["recovered_cases"] = json!(recovered.cases);
        value["recovery"] = recovered.value();
        value["in_progress"] = json!(false);
        value["checkpoint_may_lag"] = json!(false);
    } else {
        value = report(root, manifest, plan, &cp, operation);
        value["in_progress"] = json!(true);
        value["checkpoint_may_lag"] = json!(true);
        value["status_note"] =
            json!("Un lot est actif ; les compteurs affichent son dernier point de reprise publié");
    }
    Ok(value)
}

fn require_quad_hex(plan: &Plan) -> R<()> {
    if plan.profile != generator::QUAD_HEX {
        return Err("Ce profil historique est consultable. Pour générer ou entraîner, créer un corpus direct_quad_hex dans un nouveau dossier.".into());
    }
    Ok(())
}

fn check_training_manifest(root: &Path) -> R<()> {
    if root.join("manifest.json").exists() {
        let (_, plan) = load_manifest(root)?;
        require_quad_hex(&plan)?;
    }
    Ok(())
}

fn create_plan(root: &Path, request: &Value) -> R<Value> {
    let _lock = Lock::acquire(root)?;
    let plan = Plan::parse(request.get("plan").unwrap_or(&Value::Null))?;
    require_quad_hex(&plan)?;
    if root.join("manifest.json").exists() {
        let (m, p) = load_manifest(root)?;
        if p != plan {
            return Err("Ce dossier contient un autre plan ; choisir un nouveau dossier plutôt que remplacer le corpus".into());
        }
        let mut cp = checkpoint(root, &p)?;
        recover(root, &p, &mut cp)?;
        return Ok(report(root, &m, &p, &cp, "corpus_plan"));
    }
    if root.join("shards").exists()
        && fs::read_dir(root.join("shards"))
            .map_err(|e| e.to_string())?
            .next()
            .is_some()
    {
        return Err("Des segments existent sans manifeste ; aucun plan n'est écrasé".into());
    }
    let mut planned_splits = json!({"train":0,"validation":0,"test":0});
    let family_names = generator::families(&plan);
    let families: Vec<Value> = family_names
        .iter()
        .enumerate()
        .map(|(i, f)| {
            let structured=plan.structured.saturating_sub(i as u64).div_ceil(family_names.len() as u64);
            let random=plan.random.saturating_sub(i as u64).div_ceil(family_names.len() as u64);
            increment(&mut planned_splits,generator::split_for_plan(&plan,i),structured+random);
            json!({"name":f,"split":generator::split_for_plan(&plan,i),"structured":structured,"random":random,"total":structured+random})
        })
        .collect();
    let manifest = json!({"schema":SCHEMA,"generator_version":GENERATOR_VERSION,"feature_schema":crate::representation::SCHEMA,"core_version":env!("CARGO_PKG_VERSION"),"gmsh_version":"4.15.2","plan":plan,"planned_total":plan.total(),"families":families,"planned_split_counts":planned_splits,"plan_id":hash_json(&json!({"generator":GENERATOR_VERSION,"plan":plan})),"created_at":now(),"sampling":{"structured":"mixed-radix deterministic parameter grid with unique resolution coordinate","random":"family-stratified reproducible Latin hypercube with coprime affine permutations","split":"whole geometry families assigned before all parameter and meshing variations"},"notes":["Créer ce plan ne génère ni un million de fichiers ni un million de maillages","Les défauts synthétiques contrôlés ne couvrent pas toute la diversité des CAO industrielles","Les configurations de test sont séparées des familles d'apprentissage"]});
    atomic_json(&root.join("manifest.json"), &manifest)?;
    let cp = fresh_checkpoint();
    atomic_json(&root.join("checkpoint.json"), &cp)?;
    Ok(report(root, &manifest, &plan, &cp, "corpus_plan"))
}

fn compact_quality(report: &Value) -> Value {
    let q = &report["quality"];
    let mut v = serde_json::Map::new();
    for key in [
        "metric",
        "accepted",
        "minimum",
        "mean",
        "min_jacobian_determinant",
        "invalid_elements",
        "evaluated_elements",
        "poor_count",
        "poor_fraction",
        "poor_severity",
        "critical_bad_count",
        "critical_bad_top_count",
        "critical_bad_boundary_count",
        "critical_severity",
        "protected_elements",
        "violations",
    ] {
        if let Some(x) = q.get(key) {
            v.insert(key.into(), x.clone());
        }
    }
    let zones = q["critical_zones"]
        .as_array()
        .map(|rows| {
            rows.iter()
                .map(|zone| {
                    let mut z = serde_json::Map::new();
                    for key in [
                        "name",
                        "kind",
                        "dimension",
                        "tags",
                        "elements",
                        "minimum",
                        "mean",
                        "below_minimum",
                        "boundary_elements",
                        "boundary_minimum",
                        "boundary_below_minimum",
                        "accepted",
                    ] {
                        if let Some(v) = zone.get(key) {
                            z.insert(key.into(), v.clone());
                        }
                    }
                    Value::Object(z)
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    v.insert("critical_zones".into(), json!(zones));
    Value::Object(v)
}
fn failure_kind(error: &str, expected_invalid: bool) -> &'static str {
    let e = error.to_lowercase();
    if e.contains("bibliothèque")
        || e.contains("library")
        || e.contains("symbol")
        || e.contains("permission")
        || e.contains("no such file")
        || e.contains("version gmsh")
    {
        "environment_error"
    } else if e.contains("limite")
        || e.contains("estimation")
        || e.contains("dépasse")
        || e.contains("budget")
        || e.contains("temps")
        || e.contains("mémoire")
    {
        "budget_exceeded"
    } else if expected_invalid
        || e.contains("crois")
        || e.contains("intersect")
        || e.contains("géométr")
        || e.contains("contour")
        || e.contains("polyg")
        || e.contains("trou")
    {
        "invalid_geometry"
    } else {
        "meshing_failure"
    }
}

fn resolve_zone(case: &mut Value, geometry: &Value) -> R<()> {
    let template = &case["critical_zone_template"];
    if template.is_null() {
        return Ok(());
    }
    let dimension = template["dimension"]
        .as_i64()
        .ok_or("Dimension de zone synthétique absente")?;
    let mut entities = geometry["entity_catalog"]
        .as_array()
        .ok_or("Catalogue CAO absent")?
        .iter()
        .filter(|e| e["dimension"].as_i64() == Some(dimension))
        .collect::<Vec<_>>();
    entities.sort_by_key(|e| e["tag"].as_i64().unwrap_or(0));
    let selected = if template["selector"] == "last" {
        entities.last()
    } else {
        entities.first()
    }
    .ok_or("Aucune entité pour la zone synthétique")?;
    case["project"]["critical_zones"] = json!([{"name":"Zone synthétique contrôlée","kind":template["kind"],"dimension":dimension,"tags":[selected["tag"]],"buffer":template["buffer"],"minimum_quality":template["minimum_quality"]}]);
    Ok(())
}

fn evaluate_case(
    mut case: Value,
    plan: &Plan,
    library: &str,
    work: &Path,
    retain: bool,
) -> R<Value> {
    let started = Instant::now();
    let mut evaluations = 0u64;
    let reference_dir = work.join("reference");
    evaluations += 1;
    let reference = crate::backend::run(
        &json!({"operation":"inspect","project":case["project"],"gmsh_library":library,"output_dir":reference_dir,"report_only":!retain}),
    );
    let (reference, geometry_error) = match reference {
        Ok(report) => {
            resolve_zone(&mut case, &report)?;
            (Some(report), None)
        }
        Err(error) => {
            if failure_kind(&error, case["expected_invalid"] == true) == "environment_error" {
                return Err(format!("Environnement de calcul indisponible, lot suspendu sans étiqueter les cas suivants : {error}"));
            }
            (None, Some(error))
        }
    };
    let original = case["project"].clone();
    let descriptor = crate::representation::descriptor(&original, reference.as_ref());
    let mut actions = Vec::new();
    for (strategy_id, project) in generator::strategies(&original, plan.strategies) {
        let features = crate::representation::features(&original, &project, reference.as_ref());
        if features.iter().any(|v| !v.is_finite()) {
            return Err("La représentation du corpus contient une valeur non finie".into());
        }
        let mut action = json!({"strategy_id":strategy_id,"mesh_settings":project["mesh"],"features":features.to_vec(),"target":0.,"success":false,"accepted":false});
        let action_started = Instant::now();
        let evaluated = if let Some(error) = &geometry_error {
            Err(error.clone())
        } else {
            evaluations += 1;
            crate::backend::run(
                &json!({"operation":"mesh","project":project,"gmsh_library":library,"output_dir":work.join(&strategy_id),"report_only":!retain}),
            )
        };
        match evaluated {
            Ok(report) => {
                let g = &report["generation"];
                if g["method"] != "rust_direct_structured"
                    || g["element_shape"] != project["mesh"]["element_shape"]
                    || g["intermediate_mesh_generated"] != false
                    || g["recombination_used"] != false
                {
                    return Err("Le noyau n’a pas confirmé la génération directe quad/hex du corpus".into());
                }
                action["generator"] = g["method"].clone();
                let accepted = report["quality"]["accepted"] == true;
                action["success"] = json!(true);
                action["accepted"] = json!(accepted);
                action["target"] = json!(crate::quality::score(&report)?);
                action["quality"] = compact_quality(&report);
                action["elements"] = report["elements"].clone();
                action["failure_kind"] = if accepted {
                    Value::Null
                } else {
                    json!("quality_rejected")
                };
            }
            Err(error) => {
                let kind = failure_kind(&error, case["expected_invalid"] == true);
                if kind == "environment_error" {
                    return Err(format!(
                        "Environnement de calcul indisponible, cas non enregistré : {error}"
                    ));
                }
                action["failure_kind"] = json!(kind);
                action["error"] = json!(error);
            }
        }
        action["seconds"] = json!(action_started.elapsed().as_secs_f64());
        actions.push(action);
    }
    case["schema"] = json!("atelier.corpus.case.v1");
    case["actions"] = json!(actions);
    case["element_policy"] = json!(generator::QUAD_HEX);
    case["feature_schema"] = json!(crate::representation::SCHEMA);
    case["feature_count"] = json!(crate::representation::INPUTS);
    case["geometry_descriptor"] = descriptor;
    case["reference_inspection_success"] = json!(reference.is_some());
    case["backend_evaluations"] = json!(evaluations);
    case["seconds"] = json!(started.elapsed().as_secs_f64());
    case["evaluated_at"] = json!(now());
    case["meshes_retained"] = json!(retain);
    if let Some(error) = geometry_error {
        case["reference_error"] = json!(error);
    }
    Ok(case)
}

fn safe_remove_work(work: &Path) -> R<()> {
    if work.is_symlink() {
        return Err("Le dossier temporaire de corpus est un lien symbolique".into());
    }
    if work.exists() {
        fs::remove_dir_all(work).map_err(|e| e.to_string())?;
    }
    Ok(())
}
fn owned_work(root: &Path, manifest: &Value) -> R<PathBuf> {
    for name in ["shards", "work", "meshes"] {
        if root.join(name).is_symlink() {
            return Err(format!(
                "Le sous-dossier {name} du corpus ne doit pas être un lien symbolique"
            ));
        }
    }
    let directory = root.join("work");
    let marker = directory.join("owner.json");
    if marker.exists() {
        if read_json(&marker)?["plan_id"] != manifest["plan_id"] {
            return Err(
                "Le dossier temporaire appartient à un autre plan ; aucun fichier n'est supprimé"
                    .into(),
            );
        }
    } else {
        if directory.exists()
            && fs::read_dir(&directory)
                .map_err(|e| e.to_string())?
                .next()
                .is_some()
        {
            return Err("Le dossier work contient des fichiers non identifiés ; choisir un dossier de corpus vide".into());
        }
        atomic_json(
            &marker,
            &json!({"schema":"atelier.corpus.work.v1","plan_id":manifest["plan_id"]}),
        )?;
    }
    Ok(directory.join("current"))
}
fn run(root: &Path, request: &Value) -> R<Value> {
    let _lock = Lock::acquire(root)?;
    let (manifest, plan) = load_manifest(root)?;
    require_quad_hex(&plan)?;
    let mut cp = checkpoint(root, &plan)?;
    let recovered = recover(root, &plan, &mut cp)?;
    let recovery_stats = recovered.value();
    let mut journal = JournalWriter::new(recovered.cursor);
    let options = request.get("run").unwrap_or(&Value::Null);
    if !options.is_null() && !options.is_object() {
        return Err("run doit être un objet".into());
    }
    let limit = integer(options, "limit", 20, 1, 10000)?;
    let max_seconds = integer(options, "max_seconds", 240, 1, 86400)?;
    let checkpoint_every = integer(options, "checkpoint_every", 16, 1, 1000)?;
    let retain = boolean(options, "retain_meshes", false)?;
    if let Some(start) = options.get("start_index") {
        if start.as_u64() != cp["next_index"].as_u64() {
            return Err("start_index doit être exactement le prochain indice du corpus ; les trous et doublons sont interdits".into());
        }
    }
    let library = request["gmsh_library"]
        .as_str()
        .ok_or("Bibliothèque Gmsh native absente")?;
    let started = Instant::now();
    let initial = cp["next_index"].as_u64().unwrap();
    let mut processed = 0u64;
    let mut checkpoint_writes = 0u64;
    let work = owned_work(root, &manifest)?;
    safe_remove_work(&work)?;
    while processed < limit && cp["next_index"].as_u64().unwrap() < plan.total() {
        if processed > 0 && started.elapsed().as_secs_f64() >= max_seconds as f64 {
            break;
        }
        let index = cp["next_index"].as_u64().unwrap();
        let generated = case_at(index, &plan)?;
        let mut record = evaluate_case(generated, &plan, library, &work, retain)?;
        if retain {
            let kept = root
                .join("meshes")
                .join(format!("shard-{:06}", index / plan.shard_size))
                .join(format!("case-{index:09}"));
            if kept.exists() {
                return Err("Un dossier de maillage retenu existe déjà pour ce cas ; aucun fichier n'est écrasé".into());
            }
            fs::create_dir_all(kept.parent().unwrap()).map_err(|e| e.to_string())?;
            fs::rename(&work, &kept).map_err(|e| e.to_string())?;
            record["mesh_directory"] = json!(kept.strip_prefix(root).unwrap());
        }
        journal.append(root, &plan, &record)?;
        apply_record(&mut cp, &record)?;
        journal.stamp(&mut cp);
        processed += 1;
        if processed % checkpoint_every == 0 {
            atomic_json(&root.join("checkpoint.json"), &cp)?;
            checkpoint_writes += 1;
        }
        if !retain {
            safe_remove_work(&work)?;
        }
    }
    // Every record was already synced. Always publish the exact counters on a
    // normal stop; cancellation can at most leave one interval to replay.
    if processed > 0 && processed % checkpoint_every != 0 {
        atomic_json(&root.join("checkpoint.json"), &cp)?;
        checkpoint_writes += 1;
    }
    let mut result = report(root, &manifest, &plan, &cp, "corpus_run");
    result["processed_cases"] = json!(processed);
    result["started_index"] = json!(initial);
    result["recovered_cases"] = recovery_stats["recovered_cases"].clone();
    result["recovery"] = recovery_stats;
    result["checkpoint_every"] = json!(checkpoint_every);
    result["journal_synced_per_case"] = json!(true);
    result["checkpoint_writes_this_batch"] = json!(checkpoint_writes);
    result["seconds"] = json!(started.elapsed().as_secs_f64());
    result["budget_stopped"] =
        json!(processed < limit && cp["next_index"].as_u64().unwrap() < plan.total());
    result["notes"]=json!(["Le budget de temps est vérifié entre les cas ; l'interface impose aussi un délai maximal au processus natif","Une inspection CAO de référence indépendante des stratégies fournit la représentation spatiale","Les maillages sont supprimés après journalisation par défaut ; graines, projets, stratégies, qualité et résultats restent conservés","Le corpus segmenté est distinct de la petite mémoire interactive ; il ne charge pas un million de cas en RAM","Chaque cas est synchronisé sur disque ; les compteurs sont publiés par intervalles et à la fin du lot, puis rattrapés depuis le journal après interruption"]);
    atomic_json(&root.join("last_run.json"), &result)?;
    Ok(result)
}

pub fn process(request: &Value) -> R<Value> {
    let root = PathBuf::from(
        request["corpus_dir"]
            .as_str()
            .ok_or("Dossier de corpus absent")?,
    );
    if root.is_symlink() {
        return Err("Le dossier de corpus ne doit pas être un lien symbolique".into());
    }
    match request["operation"].as_str().unwrap_or("") {
        "corpus_plan" => create_plan(&root, request),
        "corpus_train" => {
            check_training_manifest(&root)?;
            crate::dataset_model::train(request)
        }
        "corpus_status" => {
            let (m, p) = load_manifest(&root)?;
            status_report(&root, &m, &p, "corpus_status")
        }
        "corpus_preview" => {
            let (m, p) = load_manifest(&root)?;
            let start = integer(request, "start_index", 0, 0, p.total() - 1)?;
            let limit = integer(request, "limit", 12, 1, 100)?;
            let mut rows = Vec::new();
            for i in start..(start + limit).min(p.total()) {
                rows.push(case_at(i, &p)?);
            }
            let mut v = status_report(&root, &m, &p, "corpus_preview")?;
            v["cases"] = json!(rows);
            v["evaluated"] = json!(false);
            Ok(v)
        }
        "corpus_run" => run(&root, request),
        _ => Err("Opération de corpus inconnue".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn temp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "mailleur-corpus-{name}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }
    fn row(i: u64) -> Value {
        json!({"schema":"atelier.corpus.case.v1","case_id":format!("case{i}"),"index":i,"family":"plate_hole_2d","split":"train","origin":"structured","backend_evaluations":3,"actions":[{"accepted":true},{"accepted":false}]})
    }
    #[test]
    fn legacy_profiles_keep_their_original_preview_and_refuse_new_observations() {
        let root = temp("historical-profile");
        let request = json!({"operation":"corpus_plan","corpus_dir":root,"plan":{"structured":8,"random":0}});
        process(&request).unwrap();
        let mut manifest = read_json(&root.join("manifest.json")).unwrap();
        manifest["plan"]["profile"] = json!("structured_direct");
        manifest["plan_id"] = json!(hash_json(&json!({"generator":GENERATOR_VERSION,"plan":manifest["plan"]})));
        atomic_json(&root.join("manifest.json"), &manifest).unwrap();
        let preview = process(&json!({"operation":"corpus_preview","corpus_dir":root,"start_index":4,"limit":1})).unwrap();
        assert_eq!(preview["cases"][0]["project"]["mesh"]["element_shape"], "prism");
        assert!(check_training_manifest(&root).is_err());
        assert!(process(&json!({"operation":"corpus_run","corpus_dir":root})).is_err());
        assert!(!root.join("shards").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn planning_million_allocates_no_case_files() {
        let root = temp("plan");
        let r = process(&json!({"operation":"corpus_plan","corpus_dir":root})).unwrap();
        assert_eq!(r["planned_total"], 1_000_000);
        assert_eq!(r["completed_cases"], 0);
        assert!(!root.join("shards").exists());
        assert!(fs::metadata(root.join("manifest.json")).unwrap().len() < 15000);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn incompatible_replanning_preserves_manifest() {
        let root = temp("preserve");
        let req = json!({"operation":"corpus_plan","corpus_dir":root,"plan":{"structured":10,"random":0}});
        process(&req).unwrap();
        let before = fs::read(root.join("manifest.json")).unwrap();
        assert!(process(&json!({"operation":"corpus_plan","corpus_dir":root,"plan":{"structured":11,"random":0}})).is_err());
        assert_eq!(before, fs::read(root.join("manifest.json")).unwrap());
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn durable_case_recovers_when_checkpoint_is_behind_and_partial_tail_is_removed() {
        let root = temp("recover");
        fs::create_dir_all(&root).unwrap();
        let plan = Plan::parse(&json!({"structured":3,"random":0,"shard_size":2})).unwrap();
        let mut cp = fresh_checkpoint();
        append_case(&root, &plan, &row(0)).unwrap();
        let file = shard(&root, 0, &plan);
        OpenOptions::new()
            .append(true)
            .open(&file)
            .unwrap()
            .write_all(b"{\"partial\":\"\xe2")
            .unwrap();
        assert_eq!(recover(&root, &plan, &mut cp).unwrap().cases, 1);
        assert_eq!(cp["next_index"], 1);
        assert_eq!(cp["accepted_actions"], 1);
        assert!(fs::read(&file).unwrap().ends_with(b"\n"));
        assert_eq!(recover(&root, &plan, &mut cp).unwrap().cases, 0);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn malformed_complete_record_is_never_silently_skipped() {
        let root = temp("corrupt");
        let p = Plan::parse(&json!({"structured":2,"random":0})).unwrap();
        fs::create_dir_all(root.join("shards")).unwrap();
        fs::write(shard(&root, 0, &p), b"{broken}\n").unwrap();
        assert!(recover(&root, &p, &mut fresh_checkpoint()).is_err());
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn corpus_lock_is_released_on_drop() {
        let root = temp("lock");
        let lock = Lock::acquire(&root).unwrap();
        assert!(Lock::acquire(&root).is_err());
        drop(lock);
        assert!(Lock::acquire(&root).is_ok());
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn foreign_work_is_preserved_before_any_cleanup() {
        let root = temp("foreign-work");
        let work = root.join("work").join("current");
        fs::create_dir_all(&work).unwrap();
        fs::write(work.join("important.txt"), b"preserve me").unwrap();
        assert!(owned_work(&root, &json!({"plan_id":"ours"})).is_err());
        assert_eq!(
            fs::read(work.join("important.txt")).unwrap(),
            b"preserve me"
        );
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn vanished_durable_segment_is_not_replaced_by_a_gap() {
        let root = temp("missing");
        fs::create_dir_all(&root).unwrap();
        let p = Plan::parse(&json!({"structured":3,"random":0})).unwrap();
        let mut cp = fresh_checkpoint();
        apply_record(&mut cp, &row(0)).unwrap();
        assert!(recover(&root, &p, &mut cp).is_err());
        assert_eq!(cp["next_index"], 1);
        let _ = fs::remove_dir_all(root);
    }
    fn committed_rows(root: &Path, plan: &Plan, count: u64) -> Value {
        fs::create_dir_all(root).unwrap();
        let mut cp = fresh_checkpoint();
        let mut writer = JournalWriter::new(None);
        for index in 0..count {
            let record = row(index);
            writer.append(root, plan, &record).unwrap();
            apply_record(&mut cp, &record).unwrap();
            writer.stamp(&mut cp);
        }
        cp
    }
    #[test]
    fn committed_cursor_checks_bytes_without_decoding_old_cases() {
        let root = temp("cursor-fast");
        let plan = Plan::parse(&json!({"structured":10,"random":0,"shard_size":4})).unwrap();
        let mut cp = committed_rows(&root, &plan, 3);
        let counts = cp.clone();
        let recovered = recover(&root, &plan, &mut cp).unwrap();
        assert_eq!(recovered.cases, 0);
        assert_eq!(recovered.decoded_records, 0);
        assert_eq!(
            recovered.verified_bytes,
            fs::metadata(shard(&root, 2, &plan)).unwrap().len()
        );
        assert_eq!(cp, counts);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn saved_cursor_recovers_durable_rows_and_discards_only_partial_tail() {
        let root = temp("cursor-crash");
        let plan = Plan::parse(&json!({"structured":10,"random":0,"shard_size":4})).unwrap();
        let mut cp = committed_rows(&root, &plan, 1);
        append_case(&root, &plan, &row(1)).unwrap();
        let path = shard(&root, 0, &plan);
        let complete = fs::metadata(&path).unwrap().len();
        OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(b"{\"partial\":\"\xe2")
            .unwrap();
        let recovered = recover(&root, &plan, &mut cp).unwrap();
        assert_eq!(recovered.cases, 1);
        assert_eq!(recovered.decoded_records, 1);
        assert_eq!(cp["next_index"], 2);
        assert_eq!(cp["accepted_actions"], 2);
        assert_eq!(fs::metadata(&path).unwrap().len(), complete);
        assert_eq!(cp["journal_cursor"]["offset"], complete);
        assert_eq!(recover(&root, &plan, &mut cp).unwrap().decoded_records, 0);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn legacy_checkpoint_is_upgraded_once_without_counting_old_cases_again() {
        let root = temp("cursor-upgrade");
        let plan = Plan::parse(&json!({"structured":10,"random":0})).unwrap();
        let mut cp = committed_rows(&root, &plan, 3);
        cp.as_object_mut().unwrap().remove("journal_cursor");
        let recovered = recover(&root, &plan, &mut cp).unwrap();
        assert!(recovered.upgraded);
        assert_eq!(recovered.decoded_records, 3);
        assert_eq!(recovered.cases, 0);
        assert_eq!(cp["next_index"], 3);
        assert_eq!(cp["accepted_actions"], 3);
        assert!(root.join("checkpoint.json").is_file());
        assert_eq!(recover(&root, &plan, &mut cp).unwrap().decoded_records, 0);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn changed_committed_json_is_rejected_even_when_its_size_and_syntax_are_valid() {
        let root = temp("cursor-modified");
        let plan = Plan::parse(&json!({"structured":10,"random":0})).unwrap();
        let mut cp = committed_rows(&root, &plan, 2);
        let path = shard(&root, 0, &plan);
        let before = fs::read_to_string(&path).unwrap();
        let modified = before.replacen("plate_hole_2d", "plate_hole_3d", 1);
        assert_eq!(before.len(), modified.len());
        for line in modified.lines() {
            serde_json::from_str::<Value>(line).unwrap();
        }
        fs::write(&path, &modified).unwrap();
        let original_cp = cp.clone();
        assert!(recover(&root, &plan, &mut cp)
            .err()
            .unwrap()
            .contains("préfixe durable"));
        assert_eq!(cp, original_cp);
        assert_eq!(fs::read_to_string(path).unwrap(), modified);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn cursor_detects_truncated_data_or_changed_index_without_repairing_them() {
        let root = temp("cursor-truncated");
        let plan = Plan::parse(&json!({"structured":10,"random":0})).unwrap();
        let cp = committed_rows(&root, &plan, 2);
        let mut wrong = cp.clone();
        wrong["next_index"] = json!(3);
        wrong["completed_cases"] = json!(3);
        assert!(recover(&root, &plan, &mut wrong).is_err());
        let path = shard(&root, 0, &plan);
        let length = fs::metadata(&path).unwrap().len() - 1;
        OpenOptions::new()
            .write(true)
            .open(&path)
            .unwrap()
            .set_len(length)
            .unwrap();
        assert!(recover(&root, &plan, &mut cp.clone()).is_err());
        assert_eq!(fs::metadata(path).unwrap().len(), length);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn cursor_crosses_a_shard_boundary_and_writer_keeps_the_next_record_contiguous() {
        let root = temp("cursor-rollover");
        let plan = Plan::parse(&json!({"structured":6,"random":0,"shard_size":2})).unwrap();
        let mut cp = committed_rows(&root, &plan, 2);
        append_case(&root, &plan, &row(2)).unwrap();
        let recovered = recover(&root, &plan, &mut cp).unwrap();
        assert_eq!(recovered.cases, 1);
        assert_eq!(recovered.decoded_records, 1);
        assert_eq!(cp["journal_cursor"]["shard"], 1);
        assert_eq!(cp["journal_cursor"]["records"], 1);
        let mut writer = JournalWriter::new(recovered.cursor);
        writer.append(&root, &plan, &row(3)).unwrap();
        apply_record(&mut cp, &row(3)).unwrap();
        writer.stamp(&mut cp);
        assert_eq!(recover(&root, &plan, &mut cp).unwrap().decoded_records, 0);
        assert_eq!(cp["next_index"], 4);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn status_reconciles_a_killed_checkpoint_interval_without_starting_another_run() {
        let root = temp("cursor-status");
        process(&json!({"operation":"corpus_plan","corpus_dir":root,
            "plan":{"structured":20,"random":0,"shard_size":2}}))
        .unwrap();
        let (manifest, plan) = load_manifest(&root).unwrap();
        let cp = committed_rows(&root, &plan, 1);
        atomic_json(&root.join("checkpoint.json"), &cp).unwrap();
        let writer_lock = Lock::acquire(&root).unwrap();
        for index in 1..17 {
            append_case(&root, &plan, &row(index)).unwrap();
        }
        let live = status_report(&root, &manifest, &plan, "corpus_status").unwrap();
        assert_eq!(live["completed_cases"], 1);
        assert_eq!(live["in_progress"], true);
        assert_eq!(live["checkpoint_may_lag"], true);
        drop(writer_lock);
        let stopped = status_report(&root, &manifest, &plan, "corpus_status").unwrap();
        assert_eq!(stopped["completed_cases"], 17);
        assert_eq!(stopped["recovered_cases"], 16);
        assert_eq!(stopped["accepted_actions"], 17);
        assert_eq!(stopped["in_progress"], false);
        assert_eq!(stopped["checkpoint_may_lag"], false);
        assert_eq!(checkpoint(&root, &plan).unwrap()["next_index"], 17);
        assert_eq!(
            status_report(&root, &manifest, &plan, "corpus_status").unwrap()["recovered_cases"],
            0
        );
        let _ = fs::remove_dir_all(root);
    }
    #[cfg(unix)]
    #[test]
    fn status_never_repairs_a_partial_file_through_a_shard_directory_symlink() {
        use std::os::unix::fs::symlink;
        let root = temp("cursor-symlink");
        let external = temp("cursor-external");
        process(&json!({"operation":"corpus_plan","corpus_dir":root,
            "plan":{"structured":2,"random":0}}))
        .unwrap();
        fs::create_dir_all(&external).unwrap();
        let source = external.join("cases-000000.jsonl");
        fs::write(&source, b"{\"partial\":\"\xe2").unwrap();
        symlink(&external, root.join("shards")).unwrap();
        assert!(process(&json!({"operation":"corpus_status","corpus_dir":root})).is_err());
        assert_eq!(fs::read(source).unwrap(), b"{\"partial\":\"\xe2");
        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_dir_all(external);
    }
    /// Same JSON decoding work as rc4's committed-prefix recovery, retained
    /// only for a reproducible benchmark and equivalence checks.
    fn legacy_decode_prefix(path: &Path) -> R<u64> {
        let mut reader = BufReader::new(File::open(path).map_err(|e| e.to_string())?);
        let mut line = Vec::new();
        let mut records = 0;
        loop {
            line.clear();
            let bytes = reader
                .by_ref()
                .take((MAX_LINE + 1) as u64)
                .read_until(b'\n', &mut line)
                .map_err(|e| e.to_string())?;
            if bytes == 0 {
                break;
            }
            if bytes > MAX_LINE || line.last() != Some(&b'\n') {
                return Err("Invalid benchmark row".into());
            }
            let value: Value = serde_json::from_slice(&line).map_err(|e| e.to_string())?;
            if value["index"].as_u64() != Some(records)
                || value["schema"] != "atelier.corpus.case.v1"
            {
                return Err("Invalid benchmark prefix".into());
            }
            records += 1;
        }
        Ok(records)
    }
    #[test]
    fn cursor_recovery_benchmark_preserves_counts_and_avoids_repeated_json_decoding() {
        let root = temp("cursor-benchmark");
        fs::create_dir_all(root.join("shards")).unwrap();
        let count = 1000;
        let plan = Plan::parse(&json!({"structured":1001,"random":0,"shard_size":1000})).unwrap();
        let path = shard(&root, 0, &plan);
        let mut file = File::create(&path).unwrap();
        let mut cp = fresh_checkpoint();
        for index in 0..count {
            let mut value = row(index);
            value["actions"] = json!([{"accepted":true,"features":vec![0.125;128]},
                {"accepted":false,"features":vec![0.625;128]},
                {"accepted":true,"features":vec![0.375;128]},
                {"accepted":false,"features":vec![0.875;128]}]);
            serde_json::to_writer(&mut file, &value).unwrap();
            file.write_all(b"\n").unwrap();
            apply_record(&mut cp, &value).unwrap();
        }
        file.sync_all().unwrap();
        drop(file);
        let upgrade = recover(&root, &plan, &mut cp).unwrap();
        assert_eq!(upgrade.decoded_records, count);
        let expected = cp.clone();
        let mut legacy = Vec::new();
        let mut optimized = Vec::new();
        for _ in 0..5 {
            let start = Instant::now();
            assert_eq!(legacy_decode_prefix(&path).unwrap(), count);
            legacy.push(start.elapsed().as_secs_f64());
            let start = Instant::now();
            let recovered = recover(&root, &plan, &mut cp).unwrap();
            optimized.push(start.elapsed().as_secs_f64());
            assert_eq!(recovered.decoded_records, 0);
            assert_eq!(recovered.cases, 0);
            assert_eq!(cp, expected);
        }
        legacy.sort_by(f64::total_cmp);
        optimized.sort_by(f64::total_cmp);
        let result = json!({"workload":"synthetic_committed_corpus_shard","records":count,
            "bytes":fs::metadata(&path).unwrap().len(),"repetitions":5,
            "legacy_median_seconds":legacy[2],"cursor_median_seconds":optimized[2],
            "speedup":legacy[2]/optimized[2],"legacy_decoded_records":count,"cursor_decoded_records":0,
            "counters_equal":true,"prefix_sha256_checked":true,
            "profile":if cfg!(debug_assertions){"debug"}else{"release"}});
        println!("CORPUS_RECOVERY_BENCHMARK={result}");
        if let Ok(path) = std::env::var("ATELIER_CORPUS_BENCH_REPORT") {
            fs::write(path, serde_json::to_vec_pretty(&result).unwrap()).unwrap();
        }
        let _ = fs::remove_dir_all(root);
    }
}
