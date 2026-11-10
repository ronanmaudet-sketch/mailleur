//! Exhaustive element-quality accounting and explicitly declared protected zones.
//!
//! This module assesses geometry, not stresses or finite-element error. Buffer
//! regions use expanded CAD bounding boxes conservatively; they are deliberately
//! reported as such instead of pretending to be exact CAD offsets.
use crate::gmsh::{ElementBlock, Gmsh};
use serde_json::{json, Value};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

type R<T> = Result<T, String>;
const ID_LIMIT: usize = 10_000;

#[derive(Clone)]
struct Element<'a> {
    tag: usize,
    entity: i32,
    nodes: &'a [usize],
    center: [f64; 3],
    bounds: [f64; 6],
    quality: f64,
}

fn number(v: &Value, key: &str, default: f64, low: f64, high: f64) -> R<f64> {
    let n = match v.get(key) {
        Some(x) => x
            .as_f64()
            .ok_or_else(|| format!("{key} : nombre attendu"))?,
        None => default,
    };
    if !n.is_finite() || n < low || n > high {
        return Err(format!("{key} doit être compris entre {low} et {high}"));
    }
    Ok(n)
}

fn intersects(a: &[f64; 6], b: &[f64; 6], buffer: f64) -> bool {
    (0..3).all(|i| a[i] <= b[i + 3] + buffer && a[i + 3] >= b[i] - buffer)
}

/// Compressed adjacency is built only for several unbuffered boundary zones.
/// A shared node is stored once per incident element, without a Vec allocation
/// for every node. Single-zone and buffered checks retain their cheap scan.
struct NodeIncidence {
    offsets: Vec<usize>,
    // Production meshes are limited to two million cells. Compact indices
    // halve adjacency storage, including higher-order connectivities.
    elements: Vec<u32>,
}

impl NodeIncidence {
    fn new(elements: &[Element<'_>], node_index: &HashMap<usize, usize>, count: usize) -> R<Self> {
        if u32::try_from(elements.len()).is_err() {
            return Err("Trop d'éléments pour l'index compact d'adjacence".into());
        }
        let mut offsets = vec![0usize; count + 1];
        for element in elements {
            for tag in element.nodes {
                let index = *node_index
                    .get(tag)
                    .ok_or("Nœud absent de l'index d'adjacence")?;
                if index >= count {
                    return Err("Indice de nœud d'adjacence hors plage".into());
                }
                offsets[index + 1] += 1;
            }
        }
        for i in 1..offsets.len() {
            offsets[i] += offsets[i - 1];
        }
        let mut slots = vec![0; offsets[count]];
        let mut cursor = offsets[..count].to_vec();
        for (index, element) in elements.iter().enumerate() {
            for tag in element.nodes {
                let node = node_index[tag];
                slots[cursor[node]] = index as u32;
                cursor[node] += 1;
            }
        }
        Ok(Self {
            offsets,
            elements: slots,
        })
    }

    fn matching(
        &self,
        support: &HashSet<usize>,
        node_index: &HashMap<usize, usize>,
        marks: &mut [u32],
        stamp: u32,
    ) -> Vec<usize> {
        let mut selected = Vec::new();
        for tag in support {
            if let Some(&node) = node_index.get(tag) {
                for &element in &self.elements[self.offsets[node]..self.offsets[node + 1]] {
                    let element = element as usize;
                    if marks[element] != stamp {
                        marks[element] = stamp;
                        selected.push(element);
                    }
                }
            }
        }
        // Restore exactly the original mesh traversal order for report IDs,
        // floating-point sums, and deterministic overlapping-zone results.
        if selected.len() > marks.len() / 8 {
            selected.clear();
            selected.extend(
                marks
                    .iter()
                    .enumerate()
                    .filter_map(|(i, mark)| (*mark == stamp).then_some(i)),
            );
        } else {
            selected.sort_unstable();
        }
        selected
    }
}

fn sample_order(a: usize, b: usize, elements: &[Element<'_>], protected_bad: &[bool]) -> Ordering {
    protected_bad[b]
        .cmp(&protected_bad[a])
        .then(elements[a].quality.total_cmp(&elements[b].quality))
        .then(elements[a].tag.cmp(&elements[b].tag))
}

fn best_samples(
    elements: &[Element<'_>],
    protected_bad: &[bool],
    threshold: f64,
    minimum: f64,
) -> Vec<usize> {
    let mut samples = Vec::with_capacity(32);
    for (index, element) in elements.iter().enumerate() {
        if element.quality >= threshold && element.quality >= minimum && !protected_bad[index] {
            continue;
        }
        let position = samples
            .binary_search_by(|old| sample_order(*old, index, elements, protected_bad))
            .unwrap_or_else(|position| position);
        if position < 32 {
            samples.insert(position, index);
            if samples.len() > 32 {
                samples.pop();
            }
        }
    }
    samples
}

/// Evaluate every top-dimensional element, independently of preview limits.
pub fn assess(
    api: &Gmsh,
    project: &Value,
    dimension: i32,
    factor: f64,
    node_index: &HashMap<usize, usize>,
    points: &[[f64; 3]],
    tags: &[usize],
    qualities: &[f64],
    jacobians: &[f64],
    blocks: &[(i32, ElementBlock)],
) -> R<Value> {
    if tags.is_empty()
        || tags.len() != qualities.len()
        || tags.len() != jacobians.len()
        || node_index.len() != points.len()
        || qualities
            .iter()
            .chain(jacobians)
            .any(|x| !x.is_finite() || *x <= 0.)
    {
        return Err("Éléments inversés, dégénérés ou mesures de qualité incohérentes".into());
    }
    let raw_policy = project.get("quality_policy").filter(|p| !p.is_null());
    if raw_policy.is_some_and(|p| !p.is_object()) {
        return Err("quality_policy doit être un objet".into());
    }
    let policy = raw_policy.unwrap_or(&Value::Null);
    let threshold = number(policy, "poor_threshold", 0.2, f64::EPSILON, 1.)?;
    let minimum = number(
        policy,
        "minimum_quality",
        if raw_policy.is_some() { 0.05 } else { 0. },
        0.,
        1.,
    )?;
    let strict = match policy.get("strict") {
        Some(v) => v
            .as_bool()
            .ok_or("quality_policy.strict doit être booléen")?,
        None => true,
    };
    let max_count = policy
        .get("max_poor_elements")
        .map(|v| {
            v.as_u64()
                .ok_or("max_poor_elements doit être un entier positif ou nul")
        })
        .transpose()?;
    let max_fraction = if policy.get("max_poor_fraction").is_some() {
        Some(number(policy, "max_poor_fraction", 1., 0., 1.)?)
    } else {
        None
    };
    let unique: HashSet<usize> = tags.iter().copied().collect();
    if unique.len() != tags.len() {
        return Err("Identifiants d'éléments dupliqués dans les mesures".into());
    }
    let mut elements = Vec::with_capacity(tags.len());
    drop(unique);
    let mut cursor = 0;
    for (entity, block) in blocks {
        if block.num_nodes == 0 || block.nodes.len() != block.tags.len() * block.num_nodes {
            return Err("Connectivité de maillage incohérente pendant le contrôle qualité".into());
        }
        for (tag, nodes) in block
            .tags
            .iter()
            .zip(block.nodes.chunks_exact(block.num_nodes))
        {
            if tags.get(cursor) != Some(tag) {
                return Err("Ordre des blocs incohérent avec les mesures de qualité".into());
            }
            let quality = qualities[cursor];
            cursor += 1;
            let mut bounds = [
                f64::INFINITY,
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            ];
            let mut center = [0.; 3];
            for node in nodes {
                let point_index = *node_index
                    .get(node)
                    .ok_or("Nœud absent pendant le contrôle qualité")?;
                let p = points
                    .get(point_index)
                    .ok_or("Indice de nœud hors plage pendant le contrôle qualité")?;
                for i in 0..3 {
                    bounds[i] = bounds[i].min(p[i]);
                    bounds[i + 3] = bounds[i + 3].max(p[i]);
                    center[i] += p[i] / nodes.len() as f64;
                }
            }
            elements.push(Element {
                tag: *tag,
                entity: *entity,
                nodes,
                center,
                bounds,
                quality,
            });
        }
    }
    if elements.len() != tags.len() {
        return Err("Le contrôle qualité n'a pas couvert tous les éléments du domaine".into());
    }
    summarize(
        api,
        project,
        dimension,
        factor,
        policy,
        threshold,
        minimum,
        strict,
        max_count,
        max_fraction,
        &elements,
        jacobians,
        node_index,
        points,
    )
}

fn summarize(
    api: &Gmsh,
    project: &Value,
    dimension: i32,
    factor: f64,
    policy: &Value,
    threshold: f64,
    minimum: f64,
    strict: bool,
    max_count: Option<u64>,
    max_fraction: Option<f64>,
    elements: &[Element<'_>],
    jacobians: &[f64],
    node_index: &HashMap<usize, usize>,
    points: &[[f64; 3]],
) -> R<Value> {
    let zones = project.get("critical_zones").filter(|v| !v.is_null());
    let zone_rows = match zones {
        Some(v) => v
            .as_array()
            .ok_or("critical_zones doit être une liste")?
            .as_slice(),
        None => &[],
    };
    if zone_rows.len() > 128 {
        return Err("Limite de 128 zones critiques dépassée".into());
    }
    let mut names = HashSet::new();
    let mut protected_bad = vec![false; elements.len()];
    let mut protected_bad_count = 0usize;
    let mut boundary_bad = HashSet::new();
    let mut boundary_samples: Vec<Value> = Vec::new();
    let mut boundary_worst: Option<(usize, f64)> = None;
    let top_entities: HashSet<i32> = elements.iter().map(|e| e.entity).collect();
    let mut protected = vec![false; elements.len()];
    let mut protected_count = 0usize;
    let indexed_zones = zone_rows
        .iter()
        .filter(|zone| {
            zone["dimension"]
                .as_i64()
                .is_some_and(|dim| dim < dimension as i64)
                && zone.get("buffer").and_then(Value::as_f64).unwrap_or(0.) == 0.
        })
        .count();
    let incidence = if indexed_zones >= 3 && elements.len() >= 256 {
        Some(NodeIncidence::new(elements, node_index, points.len())?)
    } else {
        None
    };
    let mut marks = if incidence.is_some() {
        vec![0u32; elements.len()]
    } else {
        Vec::new()
    };
    let mut catalogs: BTreeMap<i32, HashSet<i32>> = BTreeMap::new();
    let mut support_cache: BTreeMap<(i32, i32), Vec<usize>> = BTreeMap::new();
    let mut bounds_cache: BTreeMap<(i32, i32), [f64; 6]> = BTreeMap::new();
    let mut facet_cache: BTreeMap<i32, (i32, Vec<(ElementBlock, Vec<f64>)>)> = BTreeMap::new();
    let mut zone_reports = Vec::new();
    let mut violations = Vec::new();
    let mut critical_severity = 0.;
    for (zone_index, zone) in zone_rows.iter().enumerate() {
        if !zone.is_object() {
            return Err("Chaque zone critique doit être un objet".into());
        }
        let name = zone["name"]
            .as_str()
            .filter(|s| !s.trim().is_empty() && s.len() <= 200)
            .ok_or("Nom de zone critique absent ou invalide")?;
        if !names.insert(name.to_string()) {
            return Err("Les noms de zones critiques doivent être distincts".into());
        }
        let kind = zone["kind"].as_str().unwrap_or("custom");
        if !["contact", "interface", "stress", "custom"].contains(&kind) {
            return Err(format!("Type de zone critique inconnu : {kind}"));
        }
        let dim = zone["dimension"]
            .as_i64()
            .filter(|d| (0..=dimension as i64).contains(d))
            .ok_or("Dimension de zone critique invalide pour ce domaine")? as i32;
        let list = zone["tags"]
            .as_array()
            .filter(|v| !v.is_empty() && v.len() <= 2000)
            .ok_or("Une zone critique exige entre 1 et 2000 identifiants d'entités")?;
        let mut wanted = BTreeSet::new();
        for v in list {
            let tag = v
                .as_i64()
                .filter(|t| *t > 0 && *t <= i32::MAX as i64)
                .ok_or("Identifiant d'entité de zone critique invalide")?
                as i32;
            wanted.insert(tag);
        }
        if !catalogs.contains_key(&dim) {
            catalogs.insert(dim, api.entities(dim)?.iter().map(|(_, t)| *t).collect());
        }
        let catalog = &catalogs[&dim];
        if wanted.iter().any(|t| !catalog.contains(t)) {
            return Err(format!("Zone {name} : une entité géométrique n'existe pas"));
        }
        let buffer = number(zone, "buffer", 0., 0., 1e15)? * factor;
        if !buffer.is_finite() {
            return Err("Marge de zone critique hors plage".into());
        }
        let limit = number(zone, "minimum_quality", 0.35, 0., 1.)?.max(minimum);
        let mut support = HashSet::new();
        let mut bounds = Vec::new();
        for &tag in &wanted {
            if !support_cache.contains_key(&(dim, tag)) {
                support_cache.insert((dim, tag), api.nodes_for(dim, tag, true)?.0);
            }
            support.extend(support_cache[&(dim, tag)].iter().copied());
            if buffer > 0. {
                if !bounds_cache.contains_key(&(dim, tag)) {
                    bounds_cache.insert((dim, tag), api.bbox(dim, tag)?);
                }
                bounds.push(bounds_cache[&(dim, tag)]);
            }
        }
        let mut matched = Vec::new();
        let mut bad = Vec::new();
        let mut zone_min = f64::INFINITY;
        let mut zone_sum = 0.;
        let mut severity = 0.;
        let selected = if buffer == 0. && dim < dimension {
            incidence.as_ref().map(|index| {
                index.matching(&support, node_index, &mut marks, zone_index as u32 + 1)
            })
        } else {
            None
        };
        let selected_indices: Box<dyn Iterator<Item = usize> + '_> = match &selected {
            Some(selected) => Box::new(selected.iter().copied()),
            None => Box::new(0..elements.len()),
        };
        for element_index in selected_indices {
            let element = &elements[element_index];
            let incident = selected.is_some()
                || (dim == dimension && wanted.contains(&element.entity))
                || element.nodes.iter().any(|n| support.contains(n));
            let buffered = buffer > 0.
                && bounds
                    .iter()
                    .any(|b| intersects(&element.bounds, b, buffer));
            if incident || buffered {
                matched.push(element.tag);
                if !protected[element_index] {
                    protected[element_index] = true;
                    protected_count += 1;
                }
                zone_min = zone_min.min(element.quality);
                zone_sum += element.quality;
                if element.quality < limit {
                    bad.push(element.tag);
                    if !protected_bad[element_index] {
                        protected_bad[element_index] = true;
                        protected_bad_count += 1;
                    }
                    severity += limit - element.quality;
                }
            }
        }
        if matched.is_empty() {
            return Err(format!(
                "Zone {name} : aucun élément du domaine n'est adjacent à cette zone"
            ));
        }
        // Contact facets are checked in their own dimension as well as through
        // their adjacent volume elements. A good tetrahedral average must not
        // hide a distorted surface triangulation used by contact integration.
        let mut facet_count = 0usize;
        let mut facet_min: Option<f64> = None;
        let mut facet_bad = Vec::new();
        if dim == 2 && dimension == 3 {
            for &tag in &wanted {
                if !facet_cache.contains_key(&tag) {
                    let (upper, _) = api.adjacencies(2, tag)?;
                    let top_entity = upper
                        .into_iter()
                        .filter(|t| top_entities.contains(t))
                        .min()
                        .ok_or_else(|| {
                            format!("Zone {name} : la face n'a aucun volume maillé adjacent")
                        })?;
                    let mut measured = Vec::new();
                    for block in api.elements(2, tag)? {
                        let values = api.element_qualities(&block.tags, "minSICN")?;
                        if block.num_nodes == 0
                            || block.nodes.len() != block.tags.len() * block.num_nodes
                            || values.len() != block.tags.len()
                            || values.iter().any(|v| !v.is_finite() || *v <= 0.)
                        {
                            return Err(format!(
                                "Zone {name} : facettes de contact inversées ou dégénérées"
                            ));
                        }
                        measured.push((block, values));
                    }
                    facet_cache.insert(tag, (top_entity, measured));
                }
                let (top_entity, measured) = &facet_cache[&tag];
                for (block, values) in measured {
                    facet_count += values.len();
                    for ((element_tag, nodes), value) in block
                        .tags
                        .iter()
                        .zip(block.nodes.chunks_exact(block.num_nodes))
                        .zip(values.iter().copied())
                    {
                        facet_min = Some(facet_min.map_or(value, |old| old.min(value)));
                        if value < limit {
                            facet_bad.push(*element_tag);
                            boundary_bad.insert(*element_tag);
                            severity += limit - value;
                            let replace = boundary_worst;
                            if boundary_samples.len() < 32
                                || replace.is_some_and(|(_, worst)| value < worst)
                            {
                                let mut center = [0.; 3];
                                for node in nodes {
                                    let p = points
                                        .get(
                                            *node_index
                                                .get(node)
                                                .ok_or("Nœud de facette de contact absent")?,
                                        )
                                        .ok_or("Indice de facette de contact hors plage")?;
                                    for i in 0..3 {
                                        center[i] += p[i] / nodes.len() as f64;
                                    }
                                }
                                let sample = json!({"element_tag":element_tag,"entity_tag":top_entity,
                                    "source_dimension":2,"source_entity_tag":tag,"center":center,"quality":value,"critical":true});
                                if boundary_samples.len() < 32 {
                                    let index = boundary_samples.len();
                                    boundary_samples.push(sample);
                                    if boundary_worst.is_none_or(|(_, worst)| value >= worst) {
                                        boundary_worst = Some((index, value));
                                    }
                                } else {
                                    boundary_samples[replace.unwrap().0] = sample;
                                    boundary_worst = boundary_samples
                                        .iter()
                                        .enumerate()
                                        .max_by(|(_, a), (_, b)| {
                                            a["quality"]
                                                .as_f64()
                                                .unwrap()
                                                .total_cmp(&b["quality"].as_f64().unwrap())
                                        })
                                        .map(|(i, v)| (i, v["quality"].as_f64().unwrap()));
                                }
                            }
                        }
                    }
                }
            }
            if facet_count == 0 {
                return Err(format!(
                    "Zone {name} : aucune facette de contact n'a été maillée"
                ));
            }
        }
        if !bad.is_empty() {
            violations.push(format!(
                "Zone {name} : {} éléments sous la qualité minimale {limit}",
                bad.len()
            ));
        }
        if !facet_bad.is_empty() {
            violations.push(format!(
                "Zone {name} : {} facettes surfaciques sous la qualité minimale {limit}",
                facet_bad.len()
            ));
        }
        critical_severity += severity;
        zone_reports.push(json!({"name":name,"kind":kind,"dimension":dim,"tags":list,
            "buffer":buffer/factor,"buffer_internal":buffer,"minimum_quality":limit,
            "elements":matched.len(),"minimum":zone_min,"mean":zone_sum/matched.len() as f64,
            "below_minimum":bad.len(),"severity":severity,
            "boundary_elements":facet_count,"boundary_minimum":facet_min,"boundary_below_minimum":facet_bad.len(),
            "boundary_bad_element_tags":facet_bad.iter().take(ID_LIMIT).collect::<Vec<_>>(),
            "boundary_bad_element_tags_truncated":facet_bad.len()>ID_LIMIT,
            "element_tags":matched.iter().take(ID_LIMIT).collect::<Vec<_>>(),
            "bad_element_tags":bad.iter().take(ID_LIMIT).collect::<Vec<_>>(),
            "element_tags_truncated":matched.len()>ID_LIMIT,"bad_element_tags_truncated":bad.len()>ID_LIMIT,
            "accepted":bad.is_empty()&&facet_bad.is_empty(),"selection_method":if buffer>0. {"incident_nodes_or_expanded_entity_aabb"}else{"entity_or_incident_nodes"},
            "note":if buffer>0. {"La marge utilise une boîte englobante élargie conservatrice, pas une distance CAO exacte"}else{"Zone explicitement déclarée ; aucune contrainte mécanique ni contact n'est déduit de la forme"}}));
    }
    let qmin = elements
        .iter()
        .map(|e| e.quality)
        .fold(f64::INFINITY, f64::min);
    let mean = elements.iter().map(|e| e.quality).sum::<f64>() / elements.len() as f64;
    let mut poor: Vec<&Element> = elements.iter().filter(|e| e.quality < threshold).collect();
    poor.sort_by(|a, b| a.quality.total_cmp(&b.quality).then(a.tag.cmp(&b.tag)));
    let poor_fraction = poor.len() as f64 / elements.len() as f64;
    let severity = poor.iter().map(|e| threshold - e.quality).sum::<f64>();
    if qmin < minimum {
        violations.push(format!(
            "Qualité minimale {qmin:.6} inférieure au seuil global {minimum}"
        ));
    }
    if max_count.is_some_and(|n| poor.len() as u64 > n) {
        violations.push(format!(
            "{} éléments médiocres dépassent le nombre autorisé",
            poor.len()
        ));
    }
    if max_fraction.is_some_and(|n| poor_fraction > n) {
        violations.push(format!(
            "Fraction d'éléments médiocres {poor_fraction:.6} supérieure à la limite"
        ));
    }
    let samples = best_samples(elements, &protected_bad, threshold, minimum);
    struct EntitySummary {
        count: usize,
        minimum: f64,
        sum: f64,
        poor: usize,
        critical: usize,
    }
    let mut entities: BTreeMap<i32, EntitySummary> = BTreeMap::new();
    for (index, e) in elements.iter().enumerate() {
        let entry = entities.entry(e.entity).or_insert(EntitySummary {
            count: 0,
            minimum: f64::INFINITY,
            sum: 0.,
            poor: 0,
            critical: 0,
        });
        entry.count += 1;
        entry.minimum = entry.minimum.min(e.quality);
        entry.sum += e.quality;
        entry.poor += usize::from(e.quality < threshold);
        entry.critical += usize::from(protected_bad[index]);
    }
    let per_entity: Vec<Value> = entities
        .iter()
        .map(|(tag, rows)| {
            json!({"dimension":dimension,"tag":tag,
        "elements":rows.count,"minimum":rows.minimum,"mean":rows.sum/rows.count as f64,
        "poor_count":rows.poor,"critical_bad_count":rows.critical})
        })
        .collect();
    let mut sample_rows = boundary_samples;
    sample_rows.extend(samples.iter().map(|&index| { let e=&elements[index]; json!({"element_tag":e.tag,"entity_tag":e.entity,
        "source_dimension":dimension,"source_entity_tag":e.entity,"center":e.center,"quality":e.quality,
        "critical":protected_bad[index]})}));
    sample_rows.sort_by(|a, b| {
        b["critical"].as_bool().cmp(&a["critical"].as_bool()).then(
            a["quality"]
                .as_f64()
                .unwrap()
                .total_cmp(&b["quality"].as_f64().unwrap()),
        )
    });
    sample_rows.truncate(32);
    Ok(json!({"metric":"minSICN","minimum":qmin,"mean":mean,
        "min_jacobian_determinant":jacobians.iter().copied().fold(f64::INFINITY,f64::min),"invalid_elements":0,
        "below_0_1":elements.iter().filter(|e|e.quality<0.1).count(),"evaluated_elements":elements.len(),
        "poor_threshold":threshold,"poor_count":poor.len(),"poor_fraction":poor_fraction,"poor_severity":severity,
        "poor_element_tags":poor.iter().take(ID_LIMIT).map(|e|e.tag).collect::<Vec<_>>(),
        "poor_element_tags_truncated":poor.len()>ID_LIMIT,"critical_bad_count":protected_bad_count+boundary_bad.len(),
        "critical_bad_top_count":protected_bad_count,"critical_bad_boundary_count":boundary_bad.len(),
        "critical_severity":critical_severity,"protected_elements":protected_count,"critical_zones":zone_reports,
        "poor_samples":sample_rows,
        "entities":per_entity,"accepted":violations.is_empty(),"violations":violations,
        "policy":{"poor_threshold":threshold,"minimum_quality":minimum,"max_poor_elements":max_count,
            "max_poor_fraction":max_fraction,"strict":strict,"configured":!policy.is_null()},
        "note":"Tous les éléments du domaine sont contrôlés ; métrique native minSICN échantillonnée, sans certification de précision mécanique"}))
}

/// Reject inadmissible production meshes. Search trials may set strict=false.
pub fn enforce(quality: &Value) -> R<()> {
    if quality["accepted"] == false && quality["policy"]["strict"] != false {
        let details = quality["violations"]
            .as_array()
            .map(|v| {
                v.iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(" ; ")
            })
            .unwrap_or_default();
        return Err(format!(
            "Maillage refusé par les critères de qualité : {details}"
        ));
    }
    Ok(())
}

/// Scalar learning label; final decisions should use compare_reports, not this
/// smooth score alone. Rejected meshes never receive a positive reward.
pub fn score(report: &Value) -> R<f64> {
    let q = &report["quality"];
    if !q.is_object() || q.get("minimum").is_none() || q.get("mean").is_none() {
        return Err(
            "Un rapport de qualité mesurée est requis pour attribuer une récompense".into(),
        );
    }
    let minimum = number(q, "minimum", 0., 0., 1.000001)?;
    let mean = number(q, "mean", 0., 0., 1.000001)?;
    if q["accepted"] == false || q["invalid_elements"].as_u64().unwrap_or(0) > 0 {
        return Ok(0.);
    }
    let count = number(q, "poor_count", 0., 0., 2_000_000.)?;
    let severity = number(q, "poor_severity", 0., 0., 2_000_000.)?;
    Ok((0.3 * minimum + 0.3 * mean + 0.2 / (1. + count) + 0.2 / (1. + severity)).clamp(0., 1.))
}

/// Greater means a is preferable to b. Hard validity/zone requirements precede
/// poor-element count, distortion, then minimum/mean quality and mesh size.
pub fn compare_reports(a: &Value, b: &Value) -> Ordering {
    let aq = &a["quality"];
    let bq = &b["quality"];
    let n = |q: &Value, key: &str| q[key].as_f64().filter(|v| v.is_finite()).unwrap_or(0.);
    let valid = |q: &Value| {
        q.is_object()
            && q["accepted"] != false
            && q["minimum"]
                .as_f64()
                .is_some_and(|v| v.is_finite() && v > 0.)
            && q["mean"].as_f64().is_some_and(|v| v.is_finite() && v > 0.)
            && q["invalid_elements"].as_u64().unwrap_or(0) == 0
    };
    valid(aq)
        .cmp(&valid(bq))
        .then_with(|| n(bq, "critical_bad_count").total_cmp(&n(aq, "critical_bad_count")))
        .then_with(|| n(bq, "poor_count").total_cmp(&n(aq, "poor_count")))
        .then_with(|| n(bq, "poor_severity").total_cmp(&n(aq, "poor_severity")))
        .then_with(|| n(aq, "minimum").total_cmp(&n(bq, "minimum")))
        .then_with(|| n(aq, "mean").total_cmp(&n(bq, "mean")))
        .then_with(|| {
            b["elements"]
                .as_u64()
                .unwrap_or(u64::MAX)
                .cmp(&a["elements"].as_u64().unwrap_or(u64::MAX))
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn test_element<'a>(tag: usize, nodes: &'a [usize], quality: f64) -> Element<'a> {
        Element {
            tag,
            entity: 1,
            nodes,
            center: [0.; 3],
            bounds: [0.; 6],
            quality,
        }
    }
    #[test]
    fn incident_index_matches_reference_and_preserves_overlapping_zone_order() {
        let nodes = [
            [100, 300, 500],
            [500, 700, 900],
            [300, 900, 1100],
            [1300, 1500, 1700],
        ];
        let elements: Vec<_> = nodes
            .iter()
            .enumerate()
            .map(|(i, n)| test_element(40 - i, n, 0.5))
            .collect();
        let node_index: HashMap<_, _> = nodes
            .iter()
            .flatten()
            .copied()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .enumerate()
            .map(|(i, t)| (t, i))
            .collect();
        let adjacency = NodeIncidence::new(&elements, &node_index, node_index.len()).unwrap();
        let mut marks = vec![0; elements.len()];
        for (i, support) in [
            vec![300, 500],
            vec![900, 500, 9999],
            vec![1300, 1700],
            vec![],
        ]
        .iter()
        .enumerate()
        {
            let support: HashSet<_> = support.iter().copied().collect();
            let reference: Vec<_> = elements
                .iter()
                .enumerate()
                .filter_map(|(i, e)| e.nodes.iter().any(|n| support.contains(n)).then_some(i))
                .collect();
            assert_eq!(
                adjacency.matching(&support, &node_index, &mut marks, i as u32 + 1),
                reference
            );
        }
    }
    #[test]
    fn bounded_samples_match_full_sort_with_quality_ties_and_critical_overlaps() {
        let nodes = [1, 2, 3];
        for length in [0, 1, 31, 32, 33, 256] {
            let elements: Vec<_> = (0..length)
                .map(|i| test_element(1000 - i, &nodes, 0.1 + (i % 7) as f64 / 10.))
                .collect();
            let critical: Vec<_> = (0..length).map(|i| i % 3 == 0 || i % 5 == 0).collect();
            let mut reference: Vec<_> = elements
                .iter()
                .enumerate()
                .filter_map(|(i, e)| {
                    (e.quality < 0.4 || e.quality < 0.55 || critical[i]).then_some(i)
                })
                .collect();
            reference.sort_by(|a, b| sample_order(*a, *b, &elements, &critical));
            reference.truncate(32);
            assert_eq!(best_samples(&elements, &critical, 0.4, 0.55), reference);
        }
    }
    #[test]
    fn indexed_zone_queries_match_scan_on_a_large_sparse_mesh() {
        let count = 120_000;
        let nodes: Vec<[usize; 4]> = (0..count)
            .map(|i| {
                [
                    i * 7 + 101,
                    (i + 1) * 7 + 101,
                    (i + 2) * 7 + 101,
                    (i + 3) * 7 + 101,
                ]
            })
            .collect();
        let elements: Vec<_> = nodes
            .iter()
            .enumerate()
            .map(|(i, n)| test_element(i + 1, n, 0.5))
            .collect();
        let node_index: HashMap<_, _> = (0..count + 3).map(|i| (i * 7 + 101, i)).collect();
        let supports: Vec<HashSet<usize>> = (0..16)
            .map(|zone| (0..600).map(|i| (zone * 6000 + i) * 7 + 101).collect())
            .collect();
        let started = std::time::Instant::now();
        let reference: Vec<Vec<usize>> = supports
            .iter()
            .map(|support| {
                elements
                    .iter()
                    .enumerate()
                    .filter_map(|(i, e)| e.nodes.iter().any(|n| support.contains(n)).then_some(i))
                    .collect()
            })
            .collect();
        let scan_seconds = started.elapsed().as_secs_f64();
        let started = std::time::Instant::now();
        let adjacency = NodeIncidence::new(&elements, &node_index, count + 3).unwrap();
        let mut marks = vec![0; count];
        let result: Vec<Vec<usize>> = supports
            .iter()
            .enumerate()
            .map(|(i, support)| adjacency.matching(support, &node_index, &mut marks, i as u32 + 1))
            .collect();
        let indexed_seconds = started.elapsed().as_secs_f64();
        assert_eq!(reference, result);
        println!("QUALITY_ZONE_BENCHMARK elements={count} zones={} scan_seconds={scan_seconds:.9} indexed_build_and_queries_seconds={indexed_seconds:.9}",supports.len());
    }
    #[test]
    fn bbox_buffer_is_conservative_and_respects_gap() {
        let a = [0., 0., 0., 1., 1., 1.];
        let b = [1.1, 0., 0., 2., 1., 1.];
        assert!(!intersects(&a, &b, 0.05));
        assert!(intersects(&a, &b, 0.2));
    }
    #[test]
    fn strict_rejection_and_search_trial_are_distinct() {
        let mut q = json!({"accepted":false,"violations":["contact"],"policy":{"strict":true}});
        assert!(enforce(&q).unwrap_err().contains("contact"));
        q["policy"]["strict"] = json!(false);
        assert!(enforce(&q).is_ok());
    }
    #[test]
    fn count_precedes_mean_and_zone_failures_precede_count() {
        let a = json!({"quality":{"accepted":true,"critical_bad_count":0,"poor_count":0,"poor_severity":0.,"minimum":0.25,"mean":0.5}});
        let mut b = json!({"quality":{"accepted":true,"critical_bad_count":0,"poor_count":1,"poor_severity":0.001,"minimum":0.199,"mean":0.99}});
        assert_eq!(compare_reports(&a, &b), Ordering::Greater);
        b["quality"]["accepted"] = json!(false);
        b["quality"]["poor_count"] = json!(0);
        assert_eq!(compare_reports(&a, &b), Ordering::Greater);
        assert_eq!(score(&b).unwrap(), 0.);
    }
    #[test]
    fn invalid_score_input_is_rejected() {
        assert!(score(&json!({"quality":{"minimum":-0.1,"mean":0.9}})).is_err());
        assert!(score(&json!({"error":"no mesh"})).is_err());
    }
}
