//! Bounded-memory mini-batch training from an immutable prefix of corpus shards.
//!
//! Only training families enter gradient updates. Validation families select
//! weights and gate candidate ranking. The test partition is read for the final
//! report only, after weight selection and the deployment decision are fixed.
use serde::{de::DeserializeOwned, Deserialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

type R<T> = Result<T, String>;
const SCHEMA: &str = "atelier.corpus.model.v1";
const CHECKPOINT: &str = "atelier.corpus.training_checkpoint.v1";
const MAX_LINE: usize = 8 * 1024 * 1024;
const MAX_INPUTS: usize = 512;
const MAX_ACTIONS: usize = 128;
const MAX_FAMILIES: usize = 4096;
const PRODUCTION_FEATURE_SCHEMA: &str = "atelier.geometry_action_features.v2";

fn integer(v: &Value, key: &str, default: u64, min: u64, max: u64) -> R<u64> {
    let n = v.get(key).map_or(Ok(default), |x| {
        x.as_u64().ok_or_else(|| format!("{key} doit être entier"))
    })?;
    if (min..=max).contains(&n) {
        Ok(n)
    } else {
        Err(format!("{key} doit être compris entre {min} et {max}"))
    }
}
fn real(v: &Value, key: &str, default: f64, min: f64, max: f64) -> R<f64> {
    let n = v.get(key).map_or(Ok(default), |x| {
        x.as_f64()
            .ok_or_else(|| format!("{key} doit être numérique"))
    })?;
    if n.is_finite() && (min..=max).contains(&n) {
        Ok(n)
    } else {
        Err(format!("{key} hors limites"))
    }
}
fn boolean(v: &Value, key: &str, default: bool) -> R<bool> {
    v.get(key).map_or(Ok(default), |x| {
        x.as_bool()
            .ok_or_else(|| format!("{key} doit être booléen"))
    })
}
fn atomic_json(path: &Path, value: &Value) -> R<()> {
    let parent = path.parent().ok_or("Dossier de sauvegarde absent")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let tmp = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id()
    ));
    let mut file = File::create(&tmp).map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(&mut file, value).map_err(|e| e.to_string())?;
    file.write_all(b"\n").map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())?;
    fs::rename(tmp, path).map_err(|e| e.to_string())
}
fn progress(output: &Path, phase: &str, started: &Instant, details: Value) -> R<()> {
    let mut value = json!({"operation":"corpus_train","phase":phase,"seconds":started.elapsed().as_secs_f64(),"budget_scope":"gradient_batches_soft_limit_parent_process_timeout_is_hard_limit"});
    if let Some(fields) = details.as_object() {
        value.as_object_mut().unwrap().extend(fields.clone());
    }
    atomic_json(&output.join("progress.json"), &value)
}
struct TrainingLock {
    _file: File,
}
impl TrainingLock {
    fn acquire(path: &Path) -> R<Self> {
        #[cfg(windows)]
        let file = {
            use std::os::windows::fs::OpenOptionsExt;
            OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .share_mode(0)
                .open(path)
                .map_err(|e| format!("Entraînement déjà occupé : {e}"))?
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
                return Err("Un autre entraînement utilise déjà ce corpus".into());
            }
        }
        Ok(Self { _file: file })
    }
}

#[derive(Clone)]
struct Shard {
    path: PathBuf,
    bytes: u64,
}
#[derive(Clone)]
struct Snapshot {
    shards: Vec<Shard>,
    max_records: usize,
}
impl Snapshot {
    fn new(root: &Path, max_records: usize) -> R<Self> {
        let directory = root.join("shards");
        let mut shards = Vec::new();
        for item in
            fs::read_dir(&directory).map_err(|e| format!("Corpus sans fragments lisibles : {e}"))?
        {
            let item = item.map_err(|e| e.to_string())?;
            let path = item.path();
            let name = item.file_name().to_string_lossy().to_string();
            if name.starts_with("cases-") && name.ends_with(".jsonl") {
                let info = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
                if !info.is_file() || info.file_type().is_symlink() {
                    return Err("Un fragment du corpus n'est pas un fichier régulier".into());
                }
                shards.push(Shard {
                    path,
                    bytes: info.len(),
                });
            }
        }
        shards.sort_by(|a, b| a.path.cmp(&b.path));
        if shards.is_empty() {
            return Err(
                "Aucun cas calculé dans le corpus ; lancer corpus_run avant l'entraînement".into(),
            );
        }
        if shards.len() > 100_000 {
            return Err("Trop de fragments ; choisir des fragments plus grands".into());
        }
        Ok(Self {
            shards,
            max_records,
        })
    }
    /// One bounded JSON record at a time. Returns cases visited and ignored
    /// incomplete trailing lines. Appends after the snapshot are not consumed.
    fn visit(&self, mut apply: impl FnMut(usize, &Value) -> R<bool>) -> R<(usize, usize)> {
        self.visit_typed::<Value>(|ordinal, record| apply(ordinal, &record))
    }
    fn visit_typed<T: DeserializeOwned>(
        &self,
        apply: impl FnMut(usize, T) -> R<bool>,
    ) -> R<(usize, usize)> {
        self.visit_typed_from(0, apply)
    }
    fn visit_typed_from<T: DeserializeOwned>(
        &self,
        start: usize,
        mut apply: impl FnMut(usize, T) -> R<bool>,
    ) -> R<(usize, usize)> {
        let mut ordinal = 0usize;
        let mut partial = 0;
        // A shard normally contains similarly sized records. Reusing this
        // buffer avoids one large allocation per record on every epoch.
        let mut line = Vec::with_capacity(16 * 1024);
        for (shard_index, shard) in self.shards.iter().enumerate() {
            let file = File::open(&shard.path).map_err(|e| e.to_string())?;
            if file.metadata().map_err(|e| e.to_string())?.len() < shard.bytes {
                return Err("Un fragment du corpus a été tronqué pendant l'entraînement".into());
            }
            let mut reader = BufReader::with_capacity(128 * 1024, file.take(shard.bytes));
            loop {
                if ordinal >= self.max_records {
                    return Ok((ordinal, partial));
                }
                line.clear();
                let n = reader
                    .by_ref()
                    .take((MAX_LINE + 1) as u64)
                    .read_until(b'\n', &mut line)
                    .map_err(|e| e.to_string())?;
                if n == 0 {
                    break;
                }
                if n > MAX_LINE {
                    return Err("Un cas dépasse la limite de lecture bornée de 8 Mio".into());
                }
                if !line.ends_with(b"\n") {
                    if shard_index + 1 != self.shards.len() {
                        return Err("Ligne incomplète dans un fragment antérieur du corpus".into());
                    }
                    partial += 1;
                    break;
                }
                if line.iter().all(u8::is_ascii_whitespace) {
                    return Err("Ligne vide inattendue dans un fragment du corpus".into());
                }
                // Preparation has already validated the immutable prefix.
                // Resume scans it as bytes, without rebuilding old examples.
                if ordinal < start {
                    ordinal += 1;
                    continue;
                }
                let value: T = serde_json::from_slice(&line)
                    .map_err(|e| format!("Cas JSONL invalide ({} cas) : {e}", ordinal))?;
                if !apply(ordinal, value)? {
                    return Ok((ordinal, partial));
                }
                ordinal += 1;
            }
        }
        Ok((ordinal, partial))
    }
}

#[derive(Clone, Deserialize)]
struct Action {
    #[serde(rename = "strategy_id")]
    strategy: String,
    features: Vec<f64>,
    target: f64,
    accepted: bool,
    success: bool,
}

/// Numeric passes need only actions and partition. Serde skips the CAD graph,
/// geometry, diagnostics and timing payload without building their Value trees.
#[derive(Deserialize)]
struct TrainingCase {
    split: String,
    actions: Vec<Action>,
}
impl TrainingCase {
    fn validate(&self, dimension: usize) -> R<()> {
        if !["train", "validation", "test"].contains(&self.split.as_str()) {
            return Err("Partition de cas inconnue".into());
        }
        if self.actions.is_empty() || self.actions.len() > MAX_ACTIONS {
            return Err("Chaque cas doit avoir entre 1 et 128 actions calculées".into());
        }
        let mut strategies = std::collections::BTreeSet::new();
        for row in &self.actions {
            if row.strategy.is_empty()
                || row.strategy.len() > 160
                || !strategies.insert(row.strategy.as_str())
            {
                return Err("Identifiant de stratégie absent ou dupliqué".into());
            }
            if row.features.len() != dimension
                || row.features.iter().any(|x| !x.is_finite() || x.abs() > 1e6)
            {
                return Err("Caractéristiques incompatibles dans le corpus".into());
            }
            if !row.target.is_finite()
                || !(0. ..=1.).contains(&row.target)
                || ((!row.accepted || !row.success) && row.target != 0.)
                || (row.accepted && !row.success)
            {
                return Err("Score cible ou décision de calcul incohérents".into());
            }
        }
        Ok(())
    }
}
fn actions(record: &Value, dimension: Option<usize>) -> R<Vec<Action>> {
    let rows = record["actions"]
        .as_array()
        .filter(|v| !v.is_empty() && v.len() <= MAX_ACTIONS)
        .ok_or("Chaque cas doit avoir entre 1 et 128 actions calculées")?;
    let mut result = Vec::with_capacity(rows.len());
    let mut expected_dimension = dimension;
    let mut strategies = std::collections::BTreeSet::new();
    for row in rows {
        let strategy = row["strategy_id"]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= 160)
            .ok_or("Identifiant de stratégie absent")?
            .to_string();
        if !strategies.insert(strategy.clone()) {
            return Err("Une stratégie est dupliquée dans le même cas".into());
        }
        let features = row["features"]
            .as_array()
            .filter(|f| !f.is_empty() && f.len() <= MAX_INPUTS)
            .ok_or("Vecteur de caractéristiques absent ou trop grand")?;
        if expected_dimension.is_some_and(|d| d != features.len()) {
            return Err("Dimensions de caractéristiques incompatibles dans le corpus".into());
        }
        expected_dimension = Some(features.len());
        let features = features
            .iter()
            .map(|x| {
                x.as_f64()
                    .filter(|n| n.is_finite() && n.abs() <= 1e6)
                    .ok_or_else(|| "Caractéristique non finie ou hors limites".to_string())
            })
            .collect::<R<Vec<_>>>()?;
        let target = row["target"]
            .as_f64()
            .filter(|v| v.is_finite() && (0. ..=1.).contains(v))
            .ok_or("Score cible absent ou hors [0,1]")?;
        let accepted = row["accepted"]
            .as_bool()
            .ok_or("Décision d'admissibilité absente")?;
        let success = row["success"].as_bool().ok_or("État de calcul absent")?;
        if ((!accepted || !success) && target != 0.) || (accepted && !success) {
            return Err("Un échec ou maillage refusé ne doit pas recevoir un score positif".into());
        }
        result.push(Action {
            strategy,
            features,
            target,
            accepted,
            success,
        });
    }
    Ok(result)
}
fn split(record: &Value) -> R<&str> {
    record["split"]
        .as_str()
        .filter(|s| ["train", "validation", "test"].contains(s))
        .ok_or_else(|| "Partition de cas inconnue".into())
}
#[derive(Default)]
struct Scan {
    dimension: usize,
    feature_schema: String,
    families: BTreeMap<String, String>,
    counts: BTreeMap<String, (usize, usize)>,
    strategies: BTreeMap<String, (f64, usize)>,
    fingerprint: String,
    partial_lines: usize,
}
fn scan_with_progress(snapshot: &Snapshot, mut on_progress: impl FnMut(usize) -> R<()>) -> R<Scan> {
    scan_with_actions(snapshot, |n| on_progress(n), |_, _| Ok(()))
}
fn scan_with_actions(
    snapshot: &Snapshot,
    mut on_progress: impl FnMut(usize) -> R<()>,
    mut on_actions: impl FnMut(&str, &[Action]) -> R<()>,
) -> R<Scan> {
    let mut result = Scan::default();
    let mut hash = Sha256::new();
    let mut encoded = Vec::with_capacity(16 * 1024);
    let mut last_index = None;
    let (_, partial) = snapshot.visit(|ordinal, record| {
        if !record.is_object() {
            return Err("Chaque ligne du corpus doit être un objet".into());
        }
        let partition = split(record)?;
        let family = record["family"]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= 200)
            .ok_or("Famille géométrique absente")?;
        if result
            .families
            .get(family)
            .is_some_and(|old| old != partition)
        {
            return Err(format!("Fuite entre partitions pour la famille {family}"));
        }
        result
            .families
            .insert(family.to_string(), partition.to_string());
        if result.families.len() > MAX_FAMILIES {
            return Err("Trop de familles pour le modèle de corpus".into());
        }
        let index = record["index"].as_u64().ok_or("Index de cas absent")?;
        if last_index.is_some_and(|previous| index <= previous) {
            return Err("Indices de cas dupliqués ou désordonnés dans le corpus".into());
        }
        last_index = Some(index);
        let rows = actions(
            record,
            if result.dimension > 0 {
                Some(result.dimension)
            } else {
                None
            },
        )?;
        result.dimension = rows[0].features.len();
        let schema = record
            .get("feature_schema")
            .and_then(Value::as_str)
            .unwrap_or("unversioned");
        if result.feature_schema.is_empty() {
            result.feature_schema = schema.to_string();
        }
        if result.feature_schema != schema {
            return Err("Schémas de caractéristiques incompatibles dans le corpus".into());
        }
        let counts = result.counts.entry(partition.to_string()).or_default();
        counts.0 += 1;
        counts.1 += rows.len();
        on_actions(partition, &rows)?;
        if partition == "train" {
            for row in rows {
                let entry = result.strategies.entry(row.strategy).or_default();
                entry.0 += row.target;
                entry.1 += 1;
            }
        }
        // Canonical serde_json objects have ordered keys. Hash content, not
        // filenames or transient worker paths, for exact resumability.
        encoded.clear();
        serde_json::to_writer(&mut encoded, record).map_err(|e| e.to_string())?;
        hash.update(&encoded);
        hash.update(b"\n");
        if ordinal % 1024 == 0 {
            on_progress(ordinal + 1)?;
        }
        Ok(true)
    })?;
    result.partial_lines = partial;
    result.fingerprint = format!("{:x}", hash.finalize());
    if result.counts.get("train").map_or(0, |x| x.1) == 0 {
        return Err("Le corpus calculé ne contient aucun exemple d'entraînement".into());
    }
    Ok(result)
}

#[derive(Clone, PartialEq)]
struct Network {
    input: Vec<Vec<f64>>,
    hidden: Vec<f64>,
    output: Vec<f64>,
    bias: f64,
}

struct InferenceWorkspace {
    hidden: Vec<f64>,
    clipped: Vec<f64>,
}
impl InferenceWorkspace {
    fn new(network: &Network) -> Self {
        Self {
            hidden: vec![0.; network.hidden.len()],
            clipped: vec![0.; network.input[0].len()],
        }
    }
}

struct BatchWorkspace {
    inference: InferenceWorkspace,
    input_gradient: Vec<f64>,
    hidden_gradient: Vec<f64>,
    output_gradient: Vec<f64>,
}
impl BatchWorkspace {
    fn new(network: &Network) -> Self {
        Self {
            inference: InferenceWorkspace::new(network),
            input_gradient: vec![0.; network.hidden.len() * network.input[0].len()],
            hidden_gradient: vec![0.; network.hidden.len()],
            output_gradient: vec![0.; network.hidden.len()],
        }
    }
}
fn sigmoid(x: f64) -> f64 {
    if x >= 0. {
        1. / (1. + (-x).exp())
    } else {
        let e = x.exp();
        e / (1. + e)
    }
}
impl Network {
    fn new(inputs: usize, width: usize, seed: u64, average: f64) -> Self {
        let mut rng = seed ^ 0x9e3779b97f4a7c15;
        let mut random = || {
            rng ^= rng >> 12;
            rng ^= rng << 25;
            rng ^= rng >> 27;
            ((rng.wrapping_mul(0x2545f4914f6cdd1d) >> 11) as f64 / 9007199254740992.) - 0.5
        };
        let p = average.clamp(0.02, 0.98);
        Self {
            input: (0..width)
                .map(|_| (0..inputs).map(|_| random() * 0.3).collect())
                .collect(),
            hidden: vec![0.; width],
            output: (0..width).map(|_| random() * 0.2).collect(),
            bias: (p / (1. - p)).ln(),
        }
    }
    fn forward(&self, features: &[f64], workspace: &mut InferenceWorkspace) -> f64 {
        for (value, clipped) in features.iter().zip(&mut workspace.clipped) {
            *clipped = value.clamp(-4., 4.);
        }
        for ((row, bias), hidden) in self
            .input
            .iter()
            .zip(&self.hidden)
            .zip(&mut workspace.hidden)
        {
            *hidden = (*bias
                + row
                    .iter()
                    .zip(&workspace.clipped)
                    .map(|(w, x)| w * x)
                    .sum::<f64>())
            .tanh();
        }
        sigmoid(
            self.bias
                + self
                    .output
                    .iter()
                    .zip(&workspace.hidden)
                    .map(|(w, x)| w * x)
                    .sum::<f64>(),
        )
    }
    fn batch(&mut self, examples: &[Action], rate: f64, workspace: &mut BatchWorkspace) {
        let width = self.hidden.len();
        let dim = self.input[0].len();
        workspace.input_gradient.fill(0.);
        workspace.hidden_gradient.fill(0.);
        workspace.output_gradient.fill(0.);
        let mut db = 0.;
        for row in examples {
            let predicted = self.forward(&row.features, &mut workspace.inference);
            let error = 2. * (predicted - row.target) * predicted * (1. - predicted);
            db += error;
            for k in 0..width {
                let hidden = workspace.inference.hidden[k];
                let g = error * self.output[k] * (1. - hidden * hidden);
                workspace.hidden_gradient[k] += g;
                workspace.output_gradient[k] += error * hidden;
                let gradient = &mut workspace.input_gradient[k * dim..(k + 1) * dim];
                for j in 0..dim {
                    gradient[j] += g * workspace.inference.clipped[j];
                }
            }
        }
        let scale = rate / examples.len() as f64;
        self.bias -= scale * db;
        for k in 0..width {
            self.hidden[k] -= scale * workspace.hidden_gradient[k];
            self.output[k] -= scale * workspace.output_gradient[k] + rate * 1e-6 * self.output[k];
            let gradient = &workspace.input_gradient[k * dim..(k + 1) * dim];
            for j in 0..dim {
                self.input[k][j] -= scale * gradient[j] + rate * 1e-6 * self.input[k][j];
            }
        }
    }
    fn value(&self) -> Value {
        json!({"input_weights":self.input,"hidden_bias":self.hidden,"output_weights":self.output,"output_bias":self.bias})
    }
    fn load(v: &Value, dimension: usize, width: usize) -> R<Self> {
        let scalar = |v: &Value| {
            v.as_f64()
                .filter(|v| v.is_finite() && v.abs() <= 1e4)
                .ok_or_else(|| "Poids du modèle invalide".to_string())
        };
        let vector = |v: &Value, len: usize| -> R<Vec<f64>> {
            v.as_array()
                .filter(|a| a.len() == len)
                .ok_or("Dimensions du modèle invalides")?
                .iter()
                .map(scalar)
                .collect()
        };
        let input = v["input_weights"]
            .as_array()
            .filter(|a| a.len() == width)
            .ok_or("Largeur du modèle invalide")?
            .iter()
            .map(|v| vector(v, dimension))
            .collect::<R<Vec<_>>>()?;
        Ok(Self {
            input,
            hidden: vector(&v["hidden_bias"], width)?,
            output: vector(&v["output_weights"], width)?,
            bias: scalar(&v["output_bias"])?,
        })
    }
}

#[derive(Default, Clone)]
struct Metrics {
    cases: usize,
    examples: usize,
    error: f64,
    reward: f64,
    baseline: f64,
    oracle: f64,
    accepted: usize,
    baseline_accepted: usize,
    missing_baseline: usize,
}
impl Metrics {
    fn value(&self) -> Value {
        let n = self.cases.max(1) as f64;
        json!({"cases":self.cases,"examples":self.examples,
        "model_mse":if self.examples>0{Some(self.error/self.examples as f64)}else{None},
        "model_reward":if self.cases>0{Some(self.reward/n)}else{None},"baseline_reward":if self.cases>0{Some(self.baseline/n)}else{None},
        "oracle_reward":if self.cases>0{Some(self.oracle/n)}else{None},"selected_accepted_rate":if self.cases>0{Some(self.accepted as f64/n)}else{None},
        "baseline_accepted_rate":if self.cases>0{Some(self.baseline_accepted as f64/n)}else{None},"missing_baseline_cases":self.missing_baseline})
    }
}
fn evaluate(snapshot: &Snapshot, network: &Network, partition: &str, baseline: &str) -> R<Metrics> {
    let mut result = Metrics::default();
    let mut workspace = InferenceWorkspace::new(network);
    snapshot.visit_typed::<TrainingCase>(|_, record| {
        record.validate(network.input[0].len())?;
        if record.split != partition {
            return Ok(true);
        }
        evaluate_actions(
            &mut result,
            &record.actions,
            network,
            baseline,
            &mut workspace,
        );
        Ok(true)
    })?;
    Ok(result)
}
fn evaluate_actions(
    result: &mut Metrics,
    rows: &[Action],
    network: &Network,
    baseline: &str,
    workspace: &mut InferenceWorkspace,
) {
    let mut best = 0;
    let mut predicted = f64::NEG_INFINITY;
    for (i, row) in rows.iter().enumerate() {
        let score = network.forward(&row.features, workspace);
        result.error += (score - row.target).powi(2);
        if score > predicted {
            best = i;
            predicted = score;
        }
    }
    let base = rows
        .iter()
        .position(|r| r.strategy == baseline)
        .unwrap_or_else(|| {
            result.missing_baseline += 1;
            0
        });
    result.reward += rows[best].target;
    result.accepted += usize::from(rows[best].accepted);
    result.baseline += rows[base].target;
    result.baseline_accepted += usize::from(rows[base].accepted);
    result.oracle += rows.iter().map(|r| r.target).fold(0., f64::max);
    result.cases += 1;
    result.examples += rows.len();
}
fn better(candidate: &Metrics, current: &Metrics) -> bool {
    if candidate.cases == 0 {
        return candidate.error / (candidate.examples.max(1) as f64)
            < current.error / (current.examples.max(1) as f64) - 1e-12;
    }
    candidate.reward > current.reward + 1e-12
        || ((candidate.reward - current.reward).abs() <= 1e-12
            && candidate.error < current.error - 1e-12)
}
fn load_json(path: &Path) -> R<Value> {
    if fs::metadata(path).map_err(|e| e.to_string())?.len() > 8 * 1024 * 1024 {
        return Err("Modèle ou checkpoint trop volumineux".into());
    }
    serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}
fn checkpoint(
    path: &Path,
    profile: &str,
    current: &Network,
    best: &Network,
    initial: &Network,
    epoch: usize,
    case: usize,
    action: usize,
    batches: usize,
    complete: bool,
) -> R<()> {
    atomic_json(
        path,
        &json!({"schema":CHECKPOINT,"profile":profile,"network":current.value(),"best_network":best.value(),"initial_network":initial.value(),"epoch":epoch,"case_offset":case,"action_offset":action,"batches_completed":batches,"complete":complete}),
    )
}

/// Predictions order candidate experiments only; the native mesher still
/// computes and validates every selected candidate.
pub fn predict(model: &Value, features: &[f64]) -> R<Option<f64>> {
    predict_with_schema(model, features, PRODUCTION_FEATURE_SCHEMA)
}
pub fn predict_with_schema(
    model: &Value,
    features: &[f64],
    expected_schema: &str,
) -> R<Option<f64>> {
    if model["schema"] != SCHEMA {
        return Err("Schéma de modèle de corpus incompatible".into());
    }
    if expected_schema.is_empty() || model["feature_schema"].as_str() != Some(expected_schema) {
        return Err("Schéma de caractéristiques incompatible avec le modèle de corpus".into());
    }
    let enabled = model["deploy_enabled"]
        .as_bool()
        .ok_or("Décision d'activation du modèle absente")?;
    let inputs = model["input_count"]
        .as_u64()
        .filter(|n| (1..=MAX_INPUTS as u64).contains(n))
        .ok_or("Dimension du modèle invalide")? as usize;
    let width = model["hidden_count"]
        .as_u64()
        .filter(|n| (1..=128).contains(n))
        .ok_or("Largeur du modèle invalide")? as usize;
    if features.len() != inputs || features.iter().any(|x| !x.is_finite() || x.abs() > 1e6) {
        return Err("Caractéristiques incompatibles avec le modèle".into());
    }
    let network = Network::load(&model["network"], inputs, width)?;
    if enabled {
        let evidence = &model["validation"];
        let cases = evidence["cases"]
            .as_u64()
            .filter(|n| *n >= 2)
            .ok_or("Nombre de cas de validation insuffisant pour activer le modèle")?;
        let score = |key: &str| {
            evidence[key]
                .as_f64()
                .filter(|v| v.is_finite() && (0. ..=1.).contains(v))
                .ok_or_else(|| format!("Preuve de validation absente : {key}"))
        };
        if cases == 0
            || model["weights_trained"] != true
            || score("model_reward")? - score("baseline_reward")? <= 1e-5
            || score("selected_accepted_rate")? + 1e-12 < score("baseline_accepted_rate")?
            || evidence["missing_baseline_cases"].as_u64() != Some(0)
        {
            return Err("Les preuves de validation ne permettent pas d'activer ce modèle".into());
        }
    }
    Ok(if enabled {
        Some(network.forward(features, &mut InferenceWorkspace::new(&network)))
    } else {
        None
    })
}

/// Train from bounded mini-batches. A million-case plan need not fit in memory.
pub fn train(request: &Value) -> R<Value> {
    train_with_interrupt(request, |_, _, _| false)
}

// The hook permits deterministic interruption checks in the checkpoint tests.
// Production instantiates it with a constant false predicate; no public option
// or alternative numerical path is introduced.
fn train_with_interrupt(
    request: &Value,
    mut should_pause: impl FnMut(usize, usize, usize) -> bool,
) -> R<Value> {
    let started = Instant::now();
    let root = PathBuf::from(
        request["corpus_dir"]
            .as_str()
            .ok_or("Dossier de corpus absent")?,
    );
    let output = PathBuf::from(
        request["output_dir"]
            .as_str()
            .ok_or("Dossier de résultat absent")?,
    );
    if !root.is_dir() {
        return Err("Le dossier du corpus n'existe pas".into());
    }
    fs::create_dir_all(&output).map_err(|e| e.to_string())?;
    fs::create_dir_all(root.join("models")).map_err(|e| e.to_string())?;
    let _lock = TrainingLock::acquire(&root.join("training.lock"))?;
    let config = request.get("training").unwrap_or(&Value::Null);
    if !config.is_null() && !config.is_object() {
        return Err("training doit être un objet".into());
    }
    let epochs = integer(config, "epochs", 3, 1, 200)? as usize;
    let batch_size = integer(config, "batch_size", 128, 1, 4096)? as usize;
    let checkpoint_batches = integer(config, "checkpoint_batches", 16, 1, 4096)? as usize;
    let requested_max_records = integer(config, "max_records", 0, 0, 1_000_000_000)? as usize;
    let max_records = if requested_max_records == 0 {
        1_000_000_000
    } else {
        requested_max_records
    };
    let max_batches = integer(config, "max_batches", u32::MAX as u64, 1, u32::MAX as u64)? as usize;
    let width = integer(config, "hidden_count", 32, 1, 128)? as usize;
    let seed = integer(config, "seed", 42, 0, u32::MAX as u64)?;
    let max_seconds = real(config, "max_seconds", 300., 0.001, 86400.)?;
    let rate = real(config, "learning_rate", 0.05, 1e-6, 1.)?;
    let resume = boolean(config, "resume", true)?;
    let warm = boolean(config, "warm_start", true)?;
    progress(
        &output,
        "preparation",
        &started,
        json!({"processed_cases":0,"max_seconds":max_seconds,"note":"Lecture préalable des partitions et de l'empreinte ; cette passe doit se terminer avant les gradients"}),
    )?;
    let snapshot = Snapshot::new(&root, max_records)?;
    let scanned = scan_with_progress(&snapshot, |cases| {
        progress(
            &output,
            "preparation",
            &started,
            json!({"processed_cases":cases}),
        )
    })?;
    let preparation_seconds = started.elapsed().as_secs_f64();
    let count = |s: &str| scanned.counts.get(s).copied().unwrap_or_default();
    let baseline = scanned
        .strategies
        .iter()
        .max_by(|a, b| {
            (a.1 .0 / a.1 .1 as f64)
                .total_cmp(&(b.1 .0 / b.1 .1 as f64))
                .then_with(|| b.0.cmp(a.0))
        })
        .map(|(name, _)| name.clone())
        .ok_or("Aucune stratégie d'entraînement")?;
    let average = scanned.strategies.values().map(|x| x.0).sum::<f64>() / count("train").1 as f64;
    let profile=format!("{:x}",Sha256::digest(serde_json::to_vec(&json!({"dataset":scanned.fingerprint,"feature_schema":scanned.feature_schema,"inputs":scanned.dimension,"width":width,"epochs":epochs,"batch_size":batch_size,"rate":rate,"seed":seed})).map_err(|e|e.to_string())?));
    let model_path = root.join("models/current.json");
    let checkpoint_path = root.join("models/training.checkpoint.json");
    let previous = if warm && model_path.is_file() {
        Some(load_json(&model_path)?)
    } else {
        None
    };
    let mut warm_started = false;
    let mut initial = if let Some(model) = &previous {
        if model["schema"] != SCHEMA
            || model["input_count"] != scanned.dimension
            || model["hidden_count"] != width
            || model["feature_schema"] != scanned.feature_schema
        {
            return Err("Modèle précédent incompatible ; choisir warm_start=false pour un entraînement neuf explicite".into());
        }
        let old = model["family_splits"]
            .as_object()
            .ok_or("Provenance des partitions absente du modèle")?;
        for (family, partition) in old {
            if scanned
                .families
                .get(family)
                .is_some_and(|p| partition.as_str() != Some(p))
            {
                return Err(
                    "Une famille précédemment entraînée change de partition ; reprise refusée"
                        .into(),
                );
            }
        }
        warm_started = true;
        Network::load(&model["network"], scanned.dimension, width)?
    } else {
        Network::new(scanned.dimension, width, seed, average)
    };
    let mut current = initial.clone();
    let mut best = initial.clone();
    let mut epoch = 0;
    let mut case_offset = 0;
    let mut action_offset = 0;
    let mut batches = 0;
    let mut resumed = false;
    if resume && checkpoint_path.is_file() {
        let saved = load_json(&checkpoint_path)?;
        if saved["schema"] != CHECKPOINT {
            return Err("Checkpoint d'entraînement incompatible".into());
        }
        if saved["complete"] != true {
            if saved["profile"] != profile {
                return Err("Le corpus ou les paramètres ont changé depuis l'interruption ; relancer avec resume=false".into());
            }
            current = Network::load(&saved["network"], scanned.dimension, width)?;
            best = Network::load(&saved["best_network"], scanned.dimension, width)?;
            initial = Network::load(&saved["initial_network"], scanned.dimension, width)?;
            epoch = integer(&saved, "epoch", 0, 0, epochs as u64)? as usize;
            case_offset = integer(&saved, "case_offset", 0, 0, max_records as u64)? as usize;
            action_offset = integer(&saved, "action_offset", 0, 0, MAX_ACTIONS as u64)? as usize;
            batches = integer(&saved, "batches_completed", 0, 0, u32::MAX as u64)? as usize;
            resumed = true;
        }
    }
    progress(
        &output,
        "initial_validation",
        &started,
        json!({"preparation_seconds":preparation_seconds}),
    )?;
    let validation_started = Instant::now();
    let baseline_metrics = evaluate(&snapshot, &initial, "validation", &baseline)?;
    let mut best_validation = if best == initial {
        baseline_metrics.clone()
    } else {
        evaluate(&snapshot, &best, "validation", &baseline)?
    };
    let mut best_training = if best_validation.cases == 0 {
        evaluate(&snapshot, &best, "train", &baseline)?
    } else {
        Metrics::default()
    };
    let initial_validation_seconds = validation_started.elapsed().as_secs_f64();
    let gradient_started = Instant::now();
    let call_start_batches = batches;
    let mut stop = false;
    let mut max_batch_observed = 0;
    let mut pending: Vec<Action> = Vec::with_capacity(batch_size);
    let mut batch_workspace = BatchWorkspace::new(&current);
    let mut cursor = (case_offset, action_offset);
    let mut pending_start = cursor;
    checkpoint(
        &checkpoint_path,
        &profile,
        &current,
        &best,
        &initial,
        epoch,
        case_offset,
        action_offset,
        batches,
        false,
    )?;
    while epoch < epochs {
        progress(
            &output,
            "training",
            &started,
            json!({"epoch":epoch,"epochs_requested":epochs,"batches_completed":batches,"case_offset":case_offset}),
        )?;
        if started.elapsed().as_secs_f64() >= max_seconds
            || batches - call_start_batches >= max_batches
        {
            stop = true;
            break;
        }
        let start_case = case_offset;
        let start_action = action_offset;
        let mut interrupted = false;
        snapshot.visit_typed_from::<TrainingCase>(start_case, |ordinal,record|{
            if ordinal<start_case{return Ok(true);}
            if started.elapsed().as_secs_f64()>=max_seconds||batches-call_start_batches>=max_batches
                || should_pause(epoch,cursor.0,cursor.1){interrupted=true;return Ok(false);}
            record.validate(scanned.dimension)?;
            if record.split!="train"{cursor=(ordinal+1,0);return Ok(true);}
            let rows=record.actions;let len=rows.len();
            for (index,row)in rows.into_iter().enumerate(){
                if ordinal==start_case&&index<start_action{continue;}
                if pending.is_empty(){pending_start=(ordinal,index);}
                pending.push(row);cursor=if index+1==len{(ordinal+1,0)}else{(ordinal,index+1)};
                if pending.len()==batch_size{
                    current.batch(&pending,rate/(1.+epoch as f64*0.1), &mut batch_workspace);max_batch_observed=max_batch_observed.max(pending.len());pending.clear();batches+=1;
                    if batches%checkpoint_batches==0 {
                        checkpoint(&checkpoint_path,&profile,&current,&best,&initial,epoch,cursor.0,cursor.1,batches,false)?;
                        progress(&output,"training",&started,json!({"epoch":epoch,"epochs_requested":epochs,"batches_completed":batches,"case_offset":cursor.0}))?;
                    }
                    if started.elapsed().as_secs_f64()>=max_seconds||batches-call_start_batches>=max_batches{interrupted=true;return Ok(false);}
                }
            }Ok(true)
        })?;
        if interrupted && !pending.is_empty() {
            // A soft pause is not the end of an epoch. Applying this incomplete
            // batch would change the SGD trajectory when resumed. Re-read these
            // unapplied examples instead, retaining the ordinary batch size.
            cursor = pending_start;
            pending.clear();
        } else if !pending.is_empty() {
            current.batch(
                &pending,
                rate / (1. + epoch as f64 * 0.1),
                &mut batch_workspace,
            );
            max_batch_observed = max_batch_observed.max(pending.len());
            pending.clear();
            batches += 1;
        }
        if interrupted {
            case_offset = cursor.0;
            action_offset = cursor.1;
            checkpoint(
                &checkpoint_path,
                &profile,
                &current,
                &best,
                &initial,
                epoch,
                case_offset,
                action_offset,
                batches,
                false,
            )?;
            stop = true;
            break;
        }
        progress(
            &output,
            "epoch_validation",
            &started,
            json!({"epoch":epoch,"batches_completed":batches}),
        )?;
        let validation = evaluate(&snapshot, &current, "validation", &baseline)?;
        if validation.cases > 0 {
            if better(&validation, &best_validation) {
                best = current.clone();
                best_validation = validation;
            }
        } else {
            let training = evaluate(&snapshot, &current, "train", &baseline)?;
            if training.error
                < training.examples as f64
                    * (best_training.error / best_training.examples.max(1) as f64)
                    - 1e-12
            {
                best = current.clone();
                best_training = training;
            }
        }
        epoch += 1;
        case_offset = 0;
        action_offset = 0;
        cursor = (0, 0);
        checkpoint(
            &checkpoint_path,
            &profile,
            &current,
            &best,
            &initial,
            epoch,
            0,
            0,
            batches,
            epoch == epochs,
        )?;
    }
    let training_seconds = gradient_started.elapsed().as_secs_f64();
    progress(
        &output,
        "integrity_check",
        &started,
        json!({"epochs_completed":epoch,"batches_completed":batches}),
    )?;
    let final_started = Instant::now();
    // Detect mutation of already committed records before publishing weights.
    let mut validation = Metrics::default();
    let mut train_metrics = Metrics::default();
    let mut final_workspace = InferenceWorkspace::new(&best);
    let verified = scan_with_actions(
        &snapshot,
        |_| Ok(()),
        |partition, rows| {
            let result = match partition {
                "train" => &mut train_metrics,
                "validation" => &mut validation,
                _ => return Ok(()),
            };
            evaluate_actions(result, rows, &best, &baseline, &mut final_workspace);
            Ok(())
        },
    )?;
    if verified.fingerprint != scanned.fingerprint {
        return Err(
            "Les cas déjà lus ont été modifiés pendant l'entraînement ; modèle précédent conservé"
                .into(),
        );
    }
    let improvement = if validation.cases > 0 {
        Some((validation.reward - validation.baseline) / validation.cases as f64)
    } else {
        None
    };
    let weights_trained = epoch > 0
        || previous
            .as_ref()
            .is_some_and(|m| m["weights_trained"] == true);
    let enabled = weights_trained
        && validation.cases >= 2
        && improvement.is_some_and(|v| v > 1e-5)
        && validation.accepted >= validation.baseline_accepted
        && validation.missing_baseline == 0;
    // The final test pass happens strictly after all selection/gating above.
    progress(
        &output,
        "final_test",
        &started,
        json!({"epochs_completed":epoch,"deploy_enabled":enabled,"test_used_for_weight_selection":false}),
    )?;
    let test = evaluate(&snapshot, &best, "test", &baseline)?;
    let mut version = previous
        .as_ref()
        .and_then(|m| m["model_version"].as_u64())
        .unwrap_or(0)
        + 1;
    while root
        .join(format!("models/model-{version:06}.json"))
        .exists()
    {
        version += 1;
    }
    let model = json!({"schema":SCHEMA,"feature_schema":scanned.feature_schema,"input_count":scanned.dimension,"hidden_count":width,
        "parameter_count":scanned.dimension*width+width+width+1,"network":best.value(),"model_version":version,
        "dataset_fingerprint":scanned.fingerprint,"family_splits":scanned.families,"baseline_strategy":baseline,
        "deploy_enabled":enabled,"weights_trained":weights_trained,"deployment_reason":if enabled{"validation_selection_reward_improved"}else{"no_validated_selection_gain"},
        "validation":validation.value(),"role":"candidate_order_only_never_mesh_acceptance","optimizer":"Rust mini-batch SGD with MSE; no validation/test gradients","seed":seed});
    atomic_json(
        &root.join(format!("models/model-{version:06}.json")),
        &model,
    )?;
    atomic_json(&model_path, &model)?;
    let mut report = json!({"schema":"atelier.corpus.training_report.v1","operation":"corpus_train","computation_backend":"rust","streaming":true,
        "dataset_fingerprint":scanned.fingerprint,"input_count":scanned.dimension,"hidden_count":width,"parameter_count":model["parameter_count"],
        "feature_schema":scanned.feature_schema,"family_splits":scanned.families,"training_cases":count("train").0,"training_examples":count("train").1,
        "validation_cases":count("validation").0,"validation_examples":count("validation").1,"test_cases":count("test").0,"test_examples":count("test").1,
        "epochs_requested":epochs,"epochs_completed":epoch,"batches_completed":batches,"batches_this_call":batches-call_start_batches,
        "batch_size":batch_size,"max_batch_observed":max_batch_observed,"warm_started":warm_started,"resumed":resumed,"stopped_by_budget":stop});
    let details = json!({
        "baseline_strategy":baseline,"initial_validation":baseline_metrics.value(),"training":train_metrics.value(),"validation":validation.value(),"test":test.value(),
        "validation_selection_improvement":improvement,"deploy_enabled":enabled,"model_path":model_path,"model_version":version,"checkpoint_path":checkpoint_path,
        "ignored_incomplete_trailing_lines":scanned.partial_lines,"seconds":started.elapsed().as_secs_f64(),"test_used_for_gradients":false,"test_used_for_weight_selection":false,
        "notes":["Un seul cas JSONL et un mini-lot de caractéristiques sont chargés à la fois ; les maillages ne sont pas chargés en mémoire pour entraîner le réseau",
        "Le budget de temps est vérifié entre les mini-lots ; les passes de validation et de contrôle d'intégrité doivent se terminer pour publier un modèle cohérent",
        "Les familles de validation sont utilisées pour sélectionner les poids ; le jeu de test est évalué ensuite sans modifier les poids ni l'activation",
        "La prédiction du réseau ordonne les essais ; aucun maillage n'est accepté sur la seule prédiction"]});
    report
        .as_object_mut()
        .unwrap()
        .extend(details.as_object().unwrap().clone());
    report["phase"] = json!(if stop { "paused" } else { "completed" });
    report["checkpoint_complete"] = json!(!stop && epoch == epochs);
    report["checkpoint_batches"] = json!(checkpoint_batches);
    report["max_records_requested"] = json!(requested_max_records);
    report["budget_scope"]=json!("soft gradient budget checked between mini-batches; mandatory preparation, validation and integrity passes may exceed it; caller process timeout is the hard limit");
    report["phase_seconds"] = json!({"preparation":preparation_seconds,"initial_validation":initial_validation_seconds,"training_and_epoch_validation":training_seconds,"integrity_and_final_evaluation":final_started.elapsed().as_secs_f64()});
    progress(
        &output,
        if stop { "paused" } else { "completed" },
        &started,
        json!({"epochs_completed":epoch,"batches_completed":batches,"deploy_enabled":enabled,"model_path":model_path}),
    )?;
    atomic_json(&output.join("report.json"), &report)?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    // The rc4 arithmetic is retained only as a differential reference. The
    // production path reuses scratch space and computes clipping once.
    fn reference_forward(network: &Network, features: &[f64]) -> (Vec<f64>, f64) {
        let hidden: Vec<f64> = network
            .input
            .iter()
            .zip(&network.hidden)
            .map(|(row, bias)| {
                (*bias
                    + row
                        .iter()
                        .zip(features)
                        .map(|(w, x)| w * x.clamp(-4., 4.))
                        .sum::<f64>())
                .tanh()
            })
            .collect();
        let result = sigmoid(
            network.bias
                + network
                    .output
                    .iter()
                    .zip(&hidden)
                    .map(|(w, x)| w * x)
                    .sum::<f64>(),
        );
        (hidden, result)
    }
    fn reference_batch(network: &mut Network, examples: &[Action], rate: f64) {
        let width = network.hidden.len();
        let dimension = network.input[0].len();
        let mut di = vec![vec![0.; dimension]; width];
        let mut dh = vec![0.; width];
        let mut dout = vec![0.; width];
        let mut db = 0.;
        for row in examples {
            let (hidden, predicted) = reference_forward(network, &row.features);
            let error = 2. * (predicted - row.target) * predicted * (1. - predicted);
            db += error;
            for k in 0..width {
                let g = error * network.output[k] * (1. - hidden[k] * hidden[k]);
                dh[k] += g;
                dout[k] += error * hidden[k];
                for j in 0..dimension {
                    di[k][j] += g * row.features[j].clamp(-4., 4.);
                }
            }
        }
        let scale = rate / examples.len() as f64;
        network.bias -= scale * db;
        for k in 0..width {
            network.hidden[k] -= scale * dh[k];
            network.output[k] -= scale * dout[k] + rate * 1e-6 * network.output[k];
            for j in 0..dimension {
                network.input[k][j] -= scale * di[k][j] + rate * 1e-6 * network.input[k][j];
            }
        }
    }
    #[test]
    fn reused_workspace_preserves_rc4_predictions_and_gradients_exactly() {
        for (dimension, width) in [(2, 3), (128, 32)] {
            let mut optimized = Network::new(dimension, width, 42, 0.5);
            let mut reference = optimized.clone();
            let rows: Vec<Action> = (0..257)
                .map(|i| Action {
                    strategy: format!("a{i}"),
                    features: (0..dimension)
                        .map(|j| ((i * 17 + j * 23) % 101) as f64 / 6. - 8.)
                        .collect(),
                    target: ((i * 13) % 101) as f64 / 100.,
                    accepted: true,
                    success: true,
                })
                .collect();
            let mut scratch = BatchWorkspace::new(&optimized);
            for epoch in 0..3 {
                for chunk in rows.chunks(64) {
                    reference_batch(&mut reference, chunk, 0.05 / (1. + epoch as f64 * 0.1));
                    optimized.batch(chunk, 0.05 / (1. + epoch as f64 * 0.1), &mut scratch);
                    assert_eq!(optimized.value(), reference.value());
                    for row in chunk {
                        assert_eq!(
                            optimized
                                .forward(&row.features, &mut scratch.inference)
                                .to_bits(),
                            reference_forward(&reference, &row.features).1.to_bits()
                        );
                    }
                }
            }
        }
    }
    fn exact_float_bits(a: &Value, b: &Value) {
        match (a, b) {
            (Value::Number(a), Value::Number(b)) => {
                assert_eq!(a.as_f64().unwrap().to_bits(),b.as_f64().unwrap().to_bits())
            }
            (Value::Array(a), Value::Array(b)) => {
                assert_eq!(a.len(), b.len());
                for (x, y) in a.iter().zip(b) {
                    exact_float_bits(x, y)
                }
            }
            (Value::Object(a), Value::Object(b)) => {
                for (k, v) in a {
                    exact_float_bits(v, &b[k])
                }
            }
            _ => assert_eq!(a, b),
        }
    }
    fn directory(label: &str) -> PathBuf {
        let p =
            std::env::temp_dir().join(format!("mailleur-dataset-{}-{label}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(p.join("shards")).unwrap();
        p
    }
    fn fixture(root: &Path, test_target: f64) {
        let mut file = File::create(root.join("shards/cases-000000.jsonl")).unwrap();
        for i in 0..24 {
            let partition = if i < 12 {
                "train"
            } else if i < 18 {
                "validation"
            } else {
                "test"
            };
            let family = partition;
            let x = if i % 2 == 0 { 0. } else { 1. };
            let mut rows = Vec::new();
            for action in 0..2 {
                let target = if partition == "test" {
                    test_target
                } else if action == i % 2 {
                    0.95
                } else {
                    0.15
                };
                rows.push(json!({"strategy_id":format!("a{action}"),"features":[x,action as f64],"target":target,"accepted":true,"success":true}));
            }
            writeln!(file,"{}",json!({"index":i,"case_id":format!("c{i}"),"family":family,"geometry_id":format!("g{i}"),"split":partition,"actions":rows})).unwrap();
        }
    }
    fn request(root: &Path, output: &str) -> Value {
        json!({"corpus_dir":root,"output_dir":root.join(output),"training":{"epochs":8,"batch_size":4,"hidden_count":8,"learning_rate":0.3,"max_seconds":30,"warm_start":false}})
    }
    #[test]
    fn streaming_batches_and_final_test_are_reported() {
        let root = directory("stream");
        fixture(&root, 0.5);
        let r = train(&request(&root, "run")).unwrap();
        assert_eq!(r["training_cases"], 12);
        assert_eq!(r["validation_cases"], 6);
        assert_eq!(r["test_cases"], 6);
        assert_eq!(r["max_batch_observed"], 4);
        assert_eq!(r["test_used_for_gradients"], false);
        assert_eq!(r["epochs_completed"], 8);
        assert!(root.join("models/current.json").is_file());
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn test_targets_cannot_change_network_or_activation() {
        let a = directory("test-a");
        let b = directory("test-b");
        fixture(&a, 0.1);
        fixture(&b, 0.9);
        let ra = train(&request(&a, "run")).unwrap();
        let rb = train(&request(&b, "run")).unwrap();
        let ma = load_json(&a.join("models/current.json")).unwrap();
        let mb = load_json(&b.join("models/current.json")).unwrap();
        assert_eq!(ma["network"], mb["network"]);
        assert_eq!(ra["deploy_enabled"], rb["deploy_enabled"]);
        assert_ne!(ra["test"]["model_mse"], rb["test"]["model_mse"]);
        let _ = fs::remove_dir_all(a);
        let _ = fs::remove_dir_all(b);
    }
    #[test]
    fn partial_checkpoint_resumes_exact_minibatch_cursor() {
        let root = directory("resume");
        fixture(&root, 0.5);
        let mut r = request(&root, "first");
        r["training"]["batch_size"] = json!(3);
        r["training"]["max_batches"] = json!(1);
        let first = train(&r).unwrap();
        assert_eq!(first["stopped_by_budget"], true);
        assert_eq!(first["epochs_completed"], 0);
        r["output_dir"] = json!(root.join("second"));
        r["training"].as_object_mut().unwrap().remove("max_batches");
        let second = train(&r).unwrap();
        assert_eq!(second["resumed"], true);
        assert_eq!(second["epochs_completed"], 8);
        let uninterrupted = directory("resume-reference");
        fixture(&uninterrupted, 0.5);
        let mut reference = request(&uninterrupted, "run");
        reference["training"]["batch_size"] = json!(3);
        train(&reference).unwrap();
        let resumed = load_json(&root.join("models/current.json")).unwrap();
        let reference = load_json(&uninterrupted.join("models/current.json")).unwrap();
        exact_float_bits(&resumed["network"], &reference["network"]);
        let _ = fs::remove_dir_all(uninterrupted);
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn soft_pause_rewinds_unapplied_examples_instead_of_training_a_small_batch() {
        for (pause_case, expected_batches, saved_case) in [(1, 0, 0), (4, 2, 3)] {
            let root = directory(&format!("soft-pause-{pause_case}"));
            fixture(&root, 0.5);
            let mut req = request(&root, "first");
            req["training"]["batch_size"] = json!(3);
            // Two actions per case, three per batch. Pause with two pending
            // examples, both before any update and after two committed batches.
            let first = train_with_interrupt(&req, |epoch, case, action| {
                epoch == 0 && case == pause_case && action == 0
            })
            .unwrap();
            assert_eq!(first["stopped_by_budget"], true);
            assert_eq!(first["batches_this_call"], expected_batches);
            assert_eq!(
                first["max_batch_observed"],
                if expected_batches == 0 { 0 } else { 3 }
            );
            let saved = load_json(&root.join("models/training.checkpoint.json")).unwrap();
            assert_eq!(saved["case_offset"], saved_case);
            assert_eq!(saved["action_offset"], 0);
            req["output_dir"] = json!(root.join("resumed"));
            assert_eq!(train(&req).unwrap()["resumed"], true);
            let reference = directory(&format!("soft-pause-reference-{pause_case}"));
            fixture(&reference, 0.5);
            let mut whole = request(&reference, "whole");
            whole["training"]["batch_size"] = json!(3);
            train(&whole).unwrap();
            let resumed = load_json(&root.join("models/current.json")).unwrap();
            let uninterrupted = load_json(&reference.join("models/current.json")).unwrap();
            // Correctly rounded JSON parsing must preserve every saved f64 bit.
            exact_float_bits(&resumed["network"], &uninterrupted["network"]);
            exact_float_bits(&resumed["validation"], &uninterrupted["validation"]);
            assert_eq!(resumed["deploy_enabled"], uninterrupted["deploy_enabled"]);
            let _ = fs::remove_dir_all(root);
            let _ = fs::remove_dir_all(reference);
        }
    }
    #[test]
    fn family_leakage_and_duplicate_indices_are_rejected() {
        let root = directory("leak");
        fixture(&root, 0.5);
        let p = root.join("shards/cases-000000.jsonl");
        let s = fs::read_to_string(&p).unwrap();
        fs::write(
            &p,
            s.replace("\"family\":\"validation\"", "\"family\":\"train\""),
        )
        .unwrap();
        assert!(train(&request(&root, "run")).unwrap_err().contains("Fuite"));
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn disabled_or_incompatible_predictions_cannot_be_applied() {
        let n = Network::new(2, 3, 42, 0.5);
        let mut m = json!({"schema":SCHEMA,"input_count":2,"hidden_count":3,"network":n.value(),"feature_schema":PRODUCTION_FEATURE_SCHEMA,"weights_trained":true,"validation":{"cases":3,"model_reward":0.8,"baseline_reward":0.6,"selected_accepted_rate":1.,"baseline_accepted_rate":1.,"missing_baseline_cases":0},"deploy_enabled":false});
        assert_eq!(predict(&m, &[0., 1.]).unwrap(), None);
        m["deploy_enabled"] = json!(true);
        assert!(predict(&m, &[0., 1.]).unwrap().is_some());
        assert!(predict(&m, &[0.]).is_err());
        m["validation"]["model_reward"] = json!(0.3);
        assert!(predict(&m, &[0., 1.]).is_err());
        m["validation"]["model_reward"] = json!(0.8);
        m["feature_schema"] = json!("other-schema");
        assert!(predict(&m, &[0., 1.]).is_err());
    }
    #[test]
    fn partial_last_line_is_ignored_but_invalid_complete_line_is_refused() {
        let root = directory("partial");
        fixture(&root, 0.5);
        let p = root.join("shards/cases-000000.jsonl");
        let mut file = OpenOptions::new().append(true).open(&p).unwrap();
        file.write_all(b"{\"partial\"").unwrap();
        drop(file);
        let r = train(&request(&root, "run")).unwrap();
        assert_eq!(r["ignored_incomplete_trailing_lines"], 1);
        let mut file = OpenOptions::new().append(true).open(&p).unwrap();
        file.write_all(b"\n").unwrap();
        drop(file);
        assert!(train(&request(&root, "run2")).is_err());
        let _ = fs::remove_dir_all(root);
    }
    #[test]
    fn completed_checkpoint_allows_appended_corpus_and_warm_start() {
        let root = directory("append");
        fixture(&root, 0.5);
        let mut req = request(&root, "first");
        req["training"]["max_records"] = json!(0);
        train(&req).unwrap();
        let p = root.join("shards/cases-000001.jsonl");
        let mut file = File::create(p).unwrap();
        for i in 24..28 {
            writeln!(file,"{}",json!({"index":i,"case_id":format!("c{i}"),"family":"train","geometry_id":format!("g{i}"),"split":"train","actions":[{"strategy_id":"a0","features":[0.,0.],"target":0.8,"success":true,"accepted":true},{"strategy_id":"a1","features":[0.,1.],"target":0.2,"success":true,"accepted":true}]})).unwrap();
        }
        drop(file);
        req["output_dir"] = json!(root.join("second"));
        req["training"]["warm_start"] = json!(true);
        let report = train(&req).unwrap();
        assert_eq!(report["warm_started"], true);
        assert_eq!(report["resumed"], false);
        assert_eq!(report["training_cases"], 16);
        assert_eq!(report["epochs_completed"], 8);
        let _ = fs::remove_dir_all(root);
    }
}
