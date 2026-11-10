//! Cumulative, bounded neural ranking of meshing candidates.
//!
//! This network never accepts a mesh. Its optional predictions only order the
//! candidates that the native mesher will generate and independently validate.
//! Validation groups remain permanently excluded from gradient updates, also
//! when a saved network is resumed. The complete training history is replayed.

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const INPUTS: usize = crate::representation::INPUTS;
const HIDDEN: usize = 12;
const SCHEMA: &str = "atelier.experience_model.v2";
const INPUT_CLIP: f64 = 4.0;
const REQUIRED_IMPROVEMENT: f64 = 1e-5;
const NON_REGRESSION_TOLERANCE: f64 = 1e-12;

pub struct Example {
    pub features: [f64; INPUTS],
    pub target: f64,
    /// A geometry/context identity, shared by all its candidate actions.
    pub group: String,
}

#[derive(Clone)]
struct Network {
    input: [[f64; INPUTS]; HIDDEN],
    hidden: [f64; HIDDEN],
    output: [f64; HIDDEN],
    bias: f64,
}

struct Rng(u64);
impl Rng {
    fn new(seed: u64) -> Self {
        Self(seed ^ 0x9e3779b97f4a7c15)
    }
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545f4914f6cdd1d)
    }
    fn unit(&mut self) -> f64 {
        (self.next() >> 11) as f64 / 9007199254740992.0
    }
    fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            let j = (self.next() % (i as u64 + 1)) as usize;
            items.swap(i, j);
        }
    }
}

fn sigmoid(x: f64) -> f64 {
    // The split avoids overflow at either end, including extreme valid inputs.
    if x >= 0.0 {
        1.0 / (1.0 + (-x).exp())
    } else {
        let exp = x.exp();
        exp / (1.0 + exp)
    }
}

fn valid_features(features: &[f64; INPUTS]) -> bool {
    features.iter().all(|x| x.is_finite() && x.abs() <= 1e6)
}

impl Network {
    fn new(seed: u64, average: f64) -> Self {
        let mut rng = Rng::new(seed);
        let probability = average.clamp(0.02, 0.98);
        let mut result = Self {
            input: [[0.0; INPUTS]; HIDDEN],
            hidden: [0.0; HIDDEN],
            output: [0.0; HIDDEN],
            bias: (probability / (1.0 - probability)).ln(),
        };
        for row in &mut result.input {
            for weight in row {
                *weight = 0.4 * (rng.unit() - 0.5);
            }
        }
        for weight in &mut result.output {
            *weight = 0.2 * (rng.unit() - 0.5);
        }
        result
    }

    fn forward(&self, features: &[f64; INPUTS]) -> ([f64; HIDDEN], f64) {
        let clipped = features.map(|feature| feature.clamp(-INPUT_CLIP, INPUT_CLIP));
        self.forward_clipped(&clipped)
    }

    fn forward_clipped(&self, clipped: &[f64; INPUTS]) -> ([f64; HIDDEN], f64) {
        let mut hidden = [0.0; HIDDEN];
        for (i, value) in hidden.iter_mut().enumerate() {
            let x = self.hidden[i]
                + self.input[i]
                    .iter()
                    .zip(clipped)
                    .map(|(weight, feature)| weight * feature)
                    .sum::<f64>();
            *value = x.tanh();
        }
        let output = self.bias
            + self
                .output
                .iter()
                .zip(hidden)
                .map(|(weight, h)| weight * h)
                .sum::<f64>();
        (hidden, sigmoid(output))
    }

    fn step(&mut self, example: &Example, rate: f64) {
        // Each feature has the same bound for every hidden neuron and its
        // gradient. Reuse one stack buffer without changing any sum order.
        let clipped = example.features.map(|feature| feature.clamp(-INPUT_CLIP, INPUT_CLIP));
        let (hidden, predicted) = self.forward_clipped(&clipped);
        let output_gradient = 2.0 * (predicted - example.target) * predicted * (1.0 - predicted);
        let mut hidden_gradients = [0.0; HIDDEN];
        for k in 0..HIDDEN {
            hidden_gradients[k] = output_gradient * self.output[k] * (1.0 - hidden[k] * hidden[k]);
        }
        self.bias -= rate * output_gradient;
        for k in 0..HIDDEN {
            self.output[k] -= rate * (output_gradient * hidden[k] + 1e-5 * self.output[k]);
            self.hidden[k] -= rate * hidden_gradients[k];
            for j in 0..INPUTS {
                let x = clipped[j];
                self.input[k][j] -= rate * (hidden_gradients[k] * x + 1e-5 * self.input[k][j]);
            }
        }
    }

    fn value(&self) -> Value {
        json!({
            "input_weights": self.input.iter().map(|row|row.to_vec()).collect::<Vec<_>>(),
            "hidden_bias": self.hidden,
            "output_weights": self.output,
            "output_bias": self.bias,
        })
    }

    fn load(value: &Value) -> Result<Self, String> {
        let bounded = |x: &Value| {
            x.as_f64()
                .filter(|v| v.is_finite() && v.abs() <= 1e3)
                .ok_or_else(|| "Poids du modèle cumulatif invalide".to_string())
        };
        let array = |key: &str, expected: usize| {
            value[key]
                .as_array()
                .filter(|items| items.len() == expected)
                .ok_or_else(|| format!("Dimensions du modèle invalides : {key}"))
        };
        let mut input = [[0.0; INPUTS]; HIDDEN];
        for (i, row) in array("input_weights", HIDDEN)?.iter().enumerate() {
            let values = row
                .as_array()
                .filter(|items| items.len() == INPUTS)
                .ok_or("Dimensions des poids d'entrée invalides")?;
            for j in 0..INPUTS {
                input[i][j] = bounded(&values[j])?;
            }
        }
        let mut hidden = [0.0; HIDDEN];
        let mut output = [0.0; HIDDEN];
        for (i, value) in array("hidden_bias", HIDDEN)?.iter().enumerate() {
            hidden[i] = bounded(value)?;
        }
        for (i, value) in array("output_weights", HIDDEN)?.iter().enumerate() {
            output[i] = bounded(value)?;
        }
        Ok(Self {
            input,
            hidden,
            output,
            bias: bounded(&value["output_bias"])?,
        })
    }
}

fn group_names(value: &Value, key: &str) -> Result<BTreeSet<String>, String> {
    let values = value[key]
        .as_array()
        .ok_or_else(|| format!("Groupes du modèle absents : {key}"))?;
    let mut result = BTreeSet::new();
    for value in values {
        let name = value
            .as_str()
            .filter(|name| !name.is_empty() && name.len() <= 1024)
            .ok_or("Identité de groupe du modèle invalide")?;
        if !result.insert(name.to_string()) {
            return Err("Groupe dupliqué dans le modèle".into());
        }
    }
    Ok(result)
}

fn load_model(value: &Value) -> Result<(Network, BTreeSet<String>, BTreeSet<String>), String> {
    if value["schema"] != SCHEMA || value["schema_version"] != 2 {
        return Err("Version du modèle cumulatif incompatible : les poids rc3 à 16 entrées ne peuvent pas être repris par la représentation spatiale rc4".into());
    }
    if value["input_count"] != INPUTS
        || value["hidden_count"] != HIDDEN
        || value["feature_schema"] != crate::representation::SCHEMA
    {
        return Err("Architecture du modèle cumulatif incompatible".into());
    }
    let training = group_names(value, "training_groups")?;
    let validation = group_names(value, "validation_groups")?;
    if training.is_empty() || !training.is_disjoint(&validation) {
        return Err("Groupes entraînement/validation incompatibles".into());
    }
    Ok((Network::load(&value["network"])?, training, validation))
}

fn hash_group(group: &str, seed: u64) -> u64 {
    // Stable across processes/platforms, unlike the std HashMap random hasher.
    let mut hash = 0xcbf29ce484222325_u64 ^ seed;
    for byte in group.as_bytes() {
        hash = (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3);
    }
    hash
}

fn partition(
    groups: &BTreeSet<String>,
    previous_training: BTreeSet<String>,
    previous_validation: BTreeSet<String>,
    seed: u64,
) -> Result<(BTreeSet<String>, BTreeSet<String>), String> {
    if previous_training
        .iter()
        .chain(&previous_validation)
        .any(|group| !groups.contains(group))
    {
        return Err("Historique cumulatif incomplet : un ancien groupe manque au rejeu".into());
    }
    let mut training = previous_training;
    let mut validation = previous_validation;
    let mut new_groups: Vec<String> = groups
        .iter()
        .filter(|group| !training.contains(*group) && !validation.contains(*group))
        .cloned()
        .collect();
    new_groups.sort_by_key(|group| (hash_group(group, seed), group.clone()));
    // Establish a hold-out only using unseen groups. Never move a group whose
    // data already affected the warm-started weights into the validation set.
    if groups.len() >= 3 && validation.is_empty() {
        if let Some(first) = new_groups.first().cloned() {
            if groups.len() - 1 >= 2 {
                validation.insert(first.clone());
                new_groups.retain(|group| group != &first);
            }
        }
    }
    for group in new_groups {
        if groups.len() >= 4 && training.len() >= 2 && hash_group(&group, seed) % 4 == 0 {
            validation.insert(group);
        } else {
            training.insert(group);
        }
    }
    if training.is_empty() {
        return Err("Aucun groupe disponible pour l'apprentissage".into());
    }
    Ok((training, validation))
}

/// Fixed indices keep one evaluation pass per epoch and avoid copying a group
/// name for every replayed example. All sums retain the original example order.
struct EvaluationLayout {
    training: Vec<bool>,
    group_names: Vec<String>,
    group_index: Vec<usize>,
    group_counts: Vec<usize>,
    training_count: usize,
    validation_count: usize,
}

struct Evaluation {
    training_mse: f64,
    validation_mse: Option<f64>,
    replay_mse: f64,
    group_mse: Vec<f64>,
}

impl EvaluationLayout {
    fn new(examples: &[Example], training: &[usize], groups: &BTreeSet<String>) -> Self {
        let mut is_training = vec![false; examples.len()];
        for &index in training {
            is_training[index] = true;
        }
        let group_names: Vec<String> = groups.iter().cloned().collect();
        let lookup: BTreeMap<&str, usize> = group_names
            .iter()
            .enumerate()
            .map(|(index, name)| (name.as_str(), index))
            .collect();
        let group_index: Vec<usize> = examples
            .iter()
            .map(|example| lookup[example.group.as_str()])
            .collect();
        let mut group_counts = vec![0; group_names.len()];
        for &index in &group_index {
            group_counts[index] += 1;
        }
        Self {
            training: is_training,
            group_names,
            group_index,
            group_counts,
            training_count: training.len(),
            validation_count: examples.len() - training.len(),
        }
    }

    fn evaluate(&self, network: &Network, examples: &[Example]) -> Evaluation {
        let mut train_sum = 0.;
        let mut validation_sum = 0.;
        let mut all_sum = 0.;
        let mut group_sum = vec![0.; self.group_names.len()];
        for (index, example) in examples.iter().enumerate() {
            let error = (network.forward(&example.features).1 - example.target).powi(2);
            all_sum += error;
            if self.training[index] {
                train_sum += error;
            } else {
                validation_sum += error;
            }
            group_sum[self.group_index[index]] += error;
        }
        for (sum, count) in group_sum.iter_mut().zip(&self.group_counts) {
            *sum /= *count as f64;
        }
        Evaluation {
            training_mse: train_sum / self.training_count as f64,
            validation_mse: (self.validation_count > 0)
                .then(|| validation_sum / self.validation_count as f64),
            replay_mse: all_sum / examples.len() as f64,
            group_mse: group_sum,
        }
    }

    fn group_map(&self, values: &[f64]) -> BTreeMap<String, f64> {
        self.group_names
            .iter()
            .cloned()
            .zip(values.iter().copied())
            .collect()
    }
}

/// Warm-start and replay cumulative examples, keeping a non-regressing network.
///
/// A corrupt/incompatible saved model is explicitly recorded and replaced by a
/// fresh model. Omitting a group from a valid model's history is an error. The
/// scalar target must be in [0,1]; caller-defined feasibility checks remain the
/// sole authority for accepting a generated mesh.
pub fn update(
    existing: Option<&Value>,
    examples: &[Example],
    epochs: usize,
    seed: u64,
) -> Result<Value, String> {
    if !(1..=200).contains(&epochs) {
        return Err("Les passages d'apprentissage doivent être compris entre 1 et 200".into());
    }
    if examples.is_empty() || examples.len() > 100_000 {
        return Err("Entre 1 et 100000 expériences sont nécessaires pour le rejeu".into());
    }
    if examples.iter().any(|example| {
        !valid_features(&example.features)
            || !example.target.is_finite()
            || !(0.0..=1.0).contains(&example.target)
            || example.group.is_empty()
            || example.group.len() > 1024
    }) {
        return Err("Caractéristiques, cible ou groupe d'expérience invalides".into());
    }
    let groups = examples
        .iter()
        .map(|example| example.group.clone())
        .collect::<BTreeSet<_>>();
    let loaded = existing.map(load_model);
    let previous_rejected = loaded
        .as_ref()
        .and_then(|result| result.as_ref().err())
        .cloned();
    let valid_previous = loaded.and_then(Result::ok);
    let warm_started = valid_previous.is_some();
    let (previous_training, previous_validation) = valid_previous
        .as_ref()
        .map(|(_, training, validation)| (training.clone(), validation.clone()))
        .unwrap_or_default();
    // A saved split is part of the model provenance and must not depend on a
    // later UI seed change. New groups use the saved partition seed too.
    let split_seed = if warm_started {
        existing
            .and_then(|value| value["partition_seed"].as_u64())
            .unwrap_or(seed)
    } else {
        seed
    };
    let (training_groups, validation_groups) =
        partition(&groups, previous_training, previous_validation, split_seed)?;
    let mut training = Vec::new();
    let mut validation = Vec::new();
    for (i, example) in examples.iter().enumerate() {
        if training_groups.contains(&example.group) {
            training.push(i);
        } else if validation_groups.contains(&example.group) {
            validation.push(i);
        } else {
            return Err("Une expérience n'a pas de groupe d'apprentissage".into());
        }
    }
    let training_average =
        training.iter().map(|i| examples[*i].target).sum::<f64>() / training.len() as f64;
    let initial = valid_previous
        .map(|(network, _, _)| network)
        .unwrap_or_else(|| Network::new(seed, training_average));
    let layout = EvaluationLayout::new(examples, &training, &groups);
    let initial_metrics = layout.evaluate(&initial, examples);
    let previous_training_mse = initial_metrics.training_mse;
    let previous_validation_mse = initial_metrics.validation_mse;
    let previous_group_mse = layout.group_map(&initial_metrics.group_mse);
    let mut network = initial.clone();
    let mut best = initial;
    let mut best_score = initial_metrics.replay_mse;
    let mut best_epoch = 0;
    let mut rng = Rng::new(seed ^ 0x517cc1b727220a95);
    let mut order = training.clone();
    for epoch in 0..epochs {
        rng.shuffle(&mut order);
        let rate = 0.12 / (1.0 + epoch as f64 / epochs as f64);
        for i in &order {
            network.step(&examples[*i], rate);
        }
        let evaluated = layout.evaluate(&network, examples);
        let train_error = evaluated.training_mse;
        let valid_error = evaluated.validation_mse;
        let not_worse = train_error <= previous_training_mse + NON_REGRESSION_TOLERANCE
            && match (valid_error, previous_validation_mse) {
                (Some(current), Some(previous)) => current <= previous + NON_REGRESSION_TOLERANCE,
                (None, None) => true,
                _ => false,
            };
        // An aggregate improvement must not hide the loss of an older geometry.
        // A candidate may replace resumed weights only if every replayed group
        // is non-regressing on its current complete set of recorded actions.
        let groups_not_worse = not_worse
            && (!warm_started
                || evaluated
                    .group_mse
                    .iter()
                    .zip(&initial_metrics.group_mse)
                    .all(|(error, previous)| *error <= *previous + NON_REGRESSION_TOLERANCE));
        let score = evaluated.replay_mse;
        if groups_not_worse && score.is_finite() && score < best_score {
            best = network.clone();
            best_score = score;
            best_epoch = epoch + 1;
        }
    }
    let best_metrics = layout.evaluate(&best, examples);
    let training_mse = best_metrics.training_mse;
    let validation_mse = best_metrics.validation_mse;
    let constant_validation_mse = if validation.is_empty() {
        None
    } else {
        Some(
            validation
                .iter()
                .map(|i| (training_average - examples[*i].target).powi(2))
                .sum::<f64>()
                / validation.len() as f64,
        )
    };
    let improvement = validation_mse
        .zip(constant_validation_mse)
        .map(|(network, baseline)| baseline - network);
    let deploy_enabled = groups.len() >= 3
        && training_groups.len() >= 2
        && !validation_groups.is_empty()
        && improvement.is_some_and(|value| value > REQUIRED_IMPROVEMENT);
    let revision = if warm_started {
        existing
            .and_then(|value| value["revision"].as_u64())
            .unwrap_or(0)
            .saturating_add(1)
    } else {
        1
    };
    let weight_revision = if best_epoch == 0 && warm_started {
        existing
            .and_then(|value| value["weight_revision"].as_u64())
            .unwrap_or(revision - 1)
    } else {
        revision
    };
    let mut counts = BTreeMap::new();
    for example in examples {
        *counts.entry(example.group.clone()).or_insert(0usize) += 1;
    }
    Ok(json!({
        "schema": SCHEMA,
        "schema_version": 2,
        "feature_schema": crate::representation::SCHEMA,
        "revision": revision,
        "weight_revision": weight_revision,
        "input_count": INPUTS,
        "hidden_count": HIDDEN,
        "output_count": 1,
        "parameter_count": INPUTS * HIDDEN + HIDDEN + HIDDEN + 1,
        "input_clip": INPUT_CLIP,
        "output_activation": "sigmoid",
        "optimizer": "rust SGD, MSE, cumulative replay, fixed held-out geometry/context groups",
        "role": "candidate_order_only_never_mesh_acceptance",
        "warm_started": warm_started,
        "previous_model_rejected": previous_rejected,
        "update_outcome": if best_epoch > 0 {"updated_non_regressing_weights"} else if warm_started {"kept_previous_weights"} else {"initialized_without_non_regressing_update"},
        "requested_epochs": epochs,
        "selected_epoch": best_epoch,
        "seed": seed,
        "partition_seed": split_seed,
        "total_examples": examples.len(),
        "training_examples": training.len(),
        "validation_examples": validation.len(),
        "geometry_context_groups": groups.len(),
        "training_groups": training_groups,
        "validation_groups": validation_groups,
        "examples_by_group": counts,
        "previous_training_mse": previous_training_mse,
        "previous_validation_mse": previous_validation_mse,
        "previous_mse_by_group": previous_group_mse,
        "mse_by_group": layout.group_map(&best_metrics.group_mse),
        "non_regression_policy": "global training, global validation and every replayed geometry group when warm-started",
        "training_mse": training_mse,
        "validation_mse": validation_mse,
        "constant_validation_mse": constant_validation_mse,
        "validation_improvement": improvement,
        "required_improvement": REQUIRED_IMPROVEMENT,
        "deploy_enabled": deploy_enabled,
        "deployment_reason": if groups.len() < 3 {"at_least_three_geometry_context_groups_required"} else if !deploy_enabled {"no_validated_improvement_over_constant_baseline"} else {"held_out_group_improvement"},
        "validation_note": "Groupes de validation exclus des gradients et utilisés pour sélectionner les poids ; ce contrôle ne constitue pas un essai industriel indépendant.",
        "network": best.value(),
    }))
}

/// Return a ranking prediction only when a valid held-out comparison enables it.
pub fn predict(model: &Value, features: &[f64; INPUTS]) -> Option<f64> {
    if !valid_features(features) || model["deploy_enabled"] != true {
        return None;
    }
    let (network, training, validation) = load_model(model).ok()?;
    let improvement = model["validation_improvement"].as_f64()?;
    if training.len() < 2
        || validation.is_empty()
        || !improvement.is_finite()
        || improvement <= REQUIRED_IMPROVEMENT
    {
        return None;
    }
    let predicted = network.forward(features).1;
    predicted.is_finite().then_some(predicted)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preclipped_inputs_preserve_forward_and_sgd_bits() {
        // Retain the former per-neuron clipping path as a numerical reference.
        fn reference_forward(network: &Network, features: &[f64; INPUTS]) -> ([f64; HIDDEN], f64) {
            let mut hidden = [0.; HIDDEN];
            for (i, value) in hidden.iter_mut().enumerate() {
                *value = (network.hidden[i]
                    + network.input[i].iter().zip(features)
                        .map(|(w, x)| w * x.clamp(-INPUT_CLIP, INPUT_CLIP)).sum::<f64>())
                    .tanh();
            }
            let output = network.bias + network.output.iter().zip(hidden)
                .map(|(w, x)| w * x).sum::<f64>();
            (hidden, sigmoid(output))
        }
        fn reference_step(network: &mut Network, example: &Example, rate: f64) {
            let (hidden, predicted) = reference_forward(network, &example.features);
            let output_gradient = 2. * (predicted - example.target) * predicted * (1. - predicted);
            let mut gradients = [0.; HIDDEN];
            for k in 0..HIDDEN {
                gradients[k] = output_gradient * network.output[k] * (1. - hidden[k] * hidden[k]);
            }
            network.bias -= rate * output_gradient;
            for k in 0..HIDDEN {
                network.output[k] -= rate * (output_gradient * hidden[k] + 1e-5 * network.output[k]);
                network.hidden[k] -= rate * gradients[k];
                for j in 0..INPUTS {
                    let x = example.features[j].clamp(-INPUT_CLIP, INPUT_CLIP);
                    network.input[k][j] -= rate * (gradients[k] * x + 1e-5 * network.input[k][j]);
                }
            }
        }
        fn bits(network: &Network) -> Vec<u64> {
            network.input.iter().flatten().chain(network.hidden.iter())
                .chain(network.output.iter()).chain(std::iter::once(&network.bias))
                .map(|value| value.to_bits()).collect()
        }
        let boundaries = [-1e6, -4.00001, -4., -0., 0., 0.375, 4., 4.00001, 1e6];
        for seed in [0, 42, u32::MAX as u64] {
            let mut current = Network::new(seed, 0.37);
            let mut reference = current.clone();
            for offset in 0..boundaries.len() {
                let example = Example {
                    features: std::array::from_fn(|i| boundaries[(i + offset) % boundaries.len()]),
                    target: offset as f64 / (boundaries.len() - 1) as f64,
                    group: "clipping_regression".into(),
                };
                let (actual_hidden, actual) = current.forward(&example.features);
                let (expected_hidden, expected) = reference_forward(&reference, &example.features);
                assert_eq!(actual.to_bits(), expected.to_bits());
                assert_eq!(actual_hidden.map(f64::to_bits), expected_hidden.map(f64::to_bits));
                current.step(&example, 0.08);
                reference_step(&mut reference, &example, 0.08);
                assert_eq!(bits(&current), bits(&reference));
            }
        }
    }

    fn examples(groups: &[&str]) -> Vec<Example> {
        let mut rows = Vec::new();
        for group in groups {
            for action in 0..8 {
                let x = action as f64 / 7.0;
                let mut features = [0.0; INPUTS];
                features[0] = x;
                features[1] = 1.0 - x;
                rows.push(Example {
                    features,
                    target: 0.1 + 0.8 * x,
                    group: group.to_string(),
                });
            }
        }
        rows
    }

    #[test]
    fn fused_metrics_preserve_independent_partition_and_group_evaluations() {
        let rows = examples(&["one", "two", "three", "four"]);
        let groups = rows.iter().map(|row| row.group.clone()).collect();
        for seed in [0, 42, 817] {
            let mut network = Network::new(seed, 0.5);
            for validation_group in [Some("two"), None] {
                let training: Vec<_> = rows
                    .iter()
                    .enumerate()
                    .filter(|(_, row)| Some(row.group.as_str()) != validation_group)
                    .map(|(index, _)| index)
                    .collect();
                let layout = EvaluationLayout::new(&rows, &training, &groups);
                for _ in 0..3 {
                    let result = layout.evaluate(&network, &rows);
                    let errors: Vec<_> = rows
                        .iter()
                        .map(|row| (network.forward(&row.features).1 - row.target).powi(2))
                        .collect();
                    assert_eq!(
                        result.replay_mse,
                        errors.iter().sum::<f64>() / rows.len() as f64
                    );
                    assert_eq!(
                        result.training_mse,
                        training.iter().map(|i| errors[*i]).sum::<f64>() / training.len() as f64
                    );
                    let validation: Vec<_> =
                        (0..rows.len()).filter(|i| !training.contains(i)).collect();
                    let expected = if validation.is_empty() {
                        None
                    } else {
                        Some(
                            validation.iter().map(|i| errors[*i]).sum::<f64>()
                                / validation.len() as f64,
                        )
                    };
                    assert_eq!(result.validation_mse, expected);
                    for (name, value) in layout.group_map(&result.group_mse) {
                        let values: Vec<_> = rows
                            .iter()
                            .enumerate()
                            .filter(|(_, row)| row.group == name)
                            .map(|(i, _)| errors[i])
                            .collect();
                        assert_eq!(value, values.iter().sum::<f64>() / values.len() as f64);
                    }
                    for row in &rows {
                        network.step(row, 0.08);
                    }
                }
            }
        }
    }

    #[test]
    fn too_few_geometries_do_not_enable_predictions() {
        let rows = examples(&["one", "two"]);
        let model = update(None, &rows, 30, 42).unwrap();
        assert_eq!(model["deploy_enabled"], false);
        assert_eq!(model["training_groups"].as_array().unwrap().len(), 2);
        assert!(predict(&model, &rows[0].features).is_none());
    }

    #[test]
    fn learning_is_deterministic_and_improves_a_known_mapping() {
        let rows = examples(&["one", "two", "three", "four"]);
        let model = update(None, &rows, 160, 42).unwrap();
        assert_eq!(model, update(None, &rows, 160, 42).unwrap());
        assert_eq!(model["deploy_enabled"], true);
        assert!(model["training_mse"].as_f64().unwrap() < 0.02);
        assert!(
            predict(&model, &rows[0].features).unwrap()
                < predict(&model, &rows[7].features).unwrap()
        );
    }

    #[test]
    fn warm_start_preserves_split_and_cannot_regress_on_replay() {
        let rows = examples(&["one", "two", "three"]);
        let first = update(None, &rows, 50, 42).unwrap();
        let more = examples(&["one", "two", "three", "four", "five"]);
        let second = update(Some(&first), &more, 50, 18).unwrap();
        assert_eq!(second["warm_started"], true);
        assert_eq!(second["revision"], 2);
        let old_training = group_names(&first, "training_groups").unwrap();
        let new_validation = group_names(&second, "validation_groups").unwrap();
        assert!(old_training.is_disjoint(&new_validation));
        assert!(
            second["training_mse"].as_f64().unwrap()
                <= second["previous_training_mse"].as_f64().unwrap() + 1e-12
        );
        assert!(
            second["validation_mse"].as_f64().unwrap()
                <= second["previous_validation_mse"].as_f64().unwrap() + 1e-12
        );
        for (group, error) in second["mse_by_group"].as_object().unwrap() {
            assert!(
                error.as_f64().unwrap()
                    <= second["previous_mse_by_group"][group].as_f64().unwrap() + 1e-12
            );
        }
        assert_eq!(second["partition_seed"], first["partition_seed"]);
    }

    #[test]
    fn adding_first_validation_group_does_not_relabel_seen_training_data() {
        let first = update(None, &examples(&["old_one", "old_two"]), 20, 42).unwrap();
        let next = update(
            Some(&first),
            &examples(&["old_one", "old_two", "unseen"]),
            20,
            42,
        )
        .unwrap();
        assert_eq!(next["validation_groups"], json!(["unseen"]));
    }

    #[test]
    fn incomplete_history_is_not_silently_forgotten() {
        let first = update(None, &examples(&["one", "two", "three"]), 10, 42).unwrap();
        assert!(update(Some(&first), &examples(&["one", "two"]), 10, 42)
            .unwrap_err()
            .contains("Historique cumulatif incomplet"));
    }

    #[test]
    fn malformed_or_nonfinite_data_is_rejected() {
        let mut rows = examples(&["one"]);
        rows[0].features[3] = f64::NAN;
        assert!(update(None, &rows, 20, 42).is_err());
        rows[0].features[3] = 0.0;
        rows[0].target = 1.1;
        assert!(update(None, &rows, 20, 42).is_err());
        assert!(update(None, &[], 20, 42).is_err());
        assert!(update(None, &examples(&["one"]), 201, 42).is_err());
    }

    #[test]
    fn malformed_saved_network_is_reinitialized_explicitly() {
        let rows = examples(&["one", "two", "three"]);
        let mut first = update(None, &rows, 20, 42).unwrap();
        first["network"]["input_weights"] = json!([[0.0]]);
        assert!(predict(&first, &rows[0].features).is_none());
        let next = update(Some(&first), &rows, 20, 42).unwrap();
        assert_eq!(next["warm_started"], false);
        assert!(next["previous_model_rejected"].is_string());
    }

    #[test]
    fn old_sixteen_input_weights_are_explicitly_rejected() {
        let old = json!({"schema":"atelier.experience_model.v1","schema_version":1,"input_count":16,"hidden_count":12});
        let rows = examples(&["one", "two", "three"]);
        let next = update(Some(&old), &rows, 20, 42).unwrap();
        assert_eq!(next["warm_started"], false);
        assert!(next["previous_model_rejected"]
            .as_str()
            .unwrap()
            .contains("16 entrées"));
        assert_eq!(next["input_count"], 128);
        assert_eq!(next["parameter_count"], 1561);
        assert_eq!(next["feature_schema"], crate::representation::SCHEMA);
    }

    #[test]
    fn unchanged_constant_targets_do_not_enable_network() {
        let mut rows = examples(&["one", "two", "three"]);
        for row in &mut rows {
            row.target = 0.5;
        }
        let model = update(None, &rows, 40, 42).unwrap();
        assert_eq!(model["deploy_enabled"], false);
    }
}
