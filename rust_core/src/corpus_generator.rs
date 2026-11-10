//! Stateless reproducible CAD/configuration generation. No mesh is fabricated.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub const GENERATOR_VERSION: &str = "atelier.corpus.generator.v1";
pub const FAMILIES: [&str; 24] = [
    "plate_hole_2d",
    "plate_many_holes_2d",
    "plate_fillet_2d",
    "plate_chamfer_2d",
    "concave_l_2d",
    "polygon_hole_2d",
    "thin_plate_volume",
    "perforated_plate_volume",
    "bracket_volume",
    "sphere_surface",
    "box_surface",
    "perforated_plate_surface",
    "box_volume",
    "sphere_volume",
    "frame_2d",
    "slender_bracket_volume",
    "thin_box_surface",
    "invalid_polygon_defect_2d",
    "near_touching_holes_2d",
    "tiny_edge_polygon_2d",
    "stepped_polygon_2d",
    "high_curvature_plate_2d",
    "multiple_holes_volume",
    "thin_plate_surface",
];

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Plan {
    pub structured: u64,
    pub random: u64,
    pub seed: u64,
    pub strategies: u64,
    pub shard_size: u64,
}

impl Plan {
    pub fn total(&self) -> u64 {
        self.structured + self.random
    }
    pub fn parse(value: &Value) -> Result<Self, String> {
        fn n(v: &Value, k: &str, default: u64, max: u64) -> Result<u64, String> {
            let x = v
                .get(k)
                .map(|x| {
                    x.as_u64()
                        .ok_or_else(|| format!("plan.{k} doit être un entier positif ou nul"))
                })
                .transpose()?
                .unwrap_or(default);
            if x > max {
                Err(format!("plan.{k} dépasse {max}"))
            } else {
                Ok(x)
            }
        }
        if !value.is_null() && !value.is_object() {
            return Err("plan doit être un objet".into());
        }
        let p = Self {
            structured: n(value, "structured", 250_000, 10_000_000)?,
            random: n(value, "random", 750_000, 10_000_000)?,
            seed: n(value, "seed", 42, u64::MAX)?,
            strategies: n(value, "strategies", 4, 4)?,
            shard_size: n(value, "shard_size", 1000, 10000)?,
        };
        if p.total() == 0 || p.total() > 10_000_000 {
            return Err("Le plan doit contenir entre 1 et 10000000 configurations".into());
        }
        if p.strategies == 0 || p.shard_size == 0 {
            return Err("strategies et shard_size doivent être strictement positifs".into());
        }
        Ok(p)
    }
}

pub fn hash_json(value: &Value) -> String {
    format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).expect("finite corpus JSON"))
    )
}

fn mix(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e3779b97f4a7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d049bb133111eb);
    x ^ (x >> 31)
}
fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    a
}
fn unit(seed: u64) -> f64 {
    (mix(seed) >> 11) as f64 / 9007199254740992.
}

/// Each continuous parameter visits each marginal stratum exactly once within
/// one random family, using independently seeded coprime affine permutations.
fn lhs(seed: u64, family: usize, ordinal: u64, count: u64, dim: usize) -> f64 {
    let count = count.max(1);
    let salt = mix(seed
        ^ (family as u64).wrapping_mul(0x517cc1b727220a95)
        ^ (dim as u64).wrapping_mul(0x6eed0e9da4d94a4f));
    let mut a = (salt % count).max(1);
    while gcd(a, count) != 1 {
        a = (a + 1) % count;
        if a == 0 {
            a = 1;
        }
    }
    let b = mix(salt ^ 0x3c79ac492ba7b653) % count;
    let rank = (a * ordinal + b) % count;
    (rank as f64 + unit(salt ^ ordinal.wrapping_mul(0x1c69b3f74ac4ae35))) / count as f64
}

fn parameter(seed: u64, family: usize, ordinal: u64, count: u64, dim: usize, random: bool) -> f64 {
    if random {
        return lhs(seed, family, ordinal, count, dim);
    }
    const RADIX: [u64; 8] = [17, 19, 23, 29, 31, 37, 41, 43];
    let d = dim % RADIX.len();
    let stride = RADIX[..d].iter().product::<u64>();
    let digit = (ordinal / stride + mix(seed ^ family as u64 ^ dim as u64) % RADIX[d]) % RADIX[d];
    (digit as f64 + 0.5) / RADIX[d] as f64
}

pub fn split_for_family(family: usize) -> &'static str {
    if [3, 10, 15, 19].contains(&family) {
        "validation"
    } else if [5, 11, 13, 21].contains(&family) {
        "test"
    } else {
        "train"
    }
}

/// One immutable base configuration. Different meshing strategies are action
/// observations of this same case, not new geometries or independent test data.
pub fn case_at(index: u64, plan: &Plan) -> Result<Value, String> {
    if index >= plan.total() {
        return Err("Indice hors du plan de corpus".into());
    }
    let random = index >= plan.structured;
    let local = if random {
        index - plan.structured
    } else {
        index
    };
    let population = if random { plan.random } else { plan.structured };
    let family = (local % FAMILIES.len() as u64) as usize;
    let ordinal = local / FAMILIES.len() as u64;
    let count = population
        .saturating_sub(family as u64)
        .div_ceil(FAMILIES.len() as u64)
        .max(1);
    let u = |d| parameter(plan.seed, family, ordinal, count, d, random);
    let w = 4. + 8. * u(0);
    let h = 3. + 6. * u(1);
    let depth = 1.5 + 3. * u(2);
    let hole = 0.10 * w.min(h) * (0.6 + 0.5 * u(3));
    let single = json!([{"x":w*(0.4+0.2*u(4)),"y":h*0.5,"radius":hole}]);
    let multiple =
        json!([{"x":w*0.28,"y":h*0.5,"radius":hole*0.7},{"x":w*0.72,"y":h*0.5,"radius":hole*0.7}]);
    let mut geometry = json!({"kind":"plate","width":w,"height":h});
    let mut mode = "planar";
    let mut defect = "none";
    match family {
        0 => geometry["holes"] = single,
        1 => geometry["holes"] = multiple,
        2 => geometry["corner_radius"] = json!(0.10 * w.min(h) * (0.5 + u(3))),
        3 => geometry["chamfer"] = json!(0.10 * w.min(h) * (0.5 + u(3))),
        4 => {
            geometry = json!({"kind":"polygon","outer":[[0.,0.],[w,0.],[w,h*0.3],[w*0.4,h*0.3],[w*0.4,h],[0.,h]]})
        }
        5 => {
            geometry = json!({"kind":"polygon","outer":[[0.,0.],[w,0.],[w,h],[0.,h]],"holes":[[[w*0.35,h*0.35],[w*0.35,h*0.65],[w*0.65,h*0.65],[w*0.65,h*0.35]]]})
        }
        6 => {
            mode = "volume";
            geometry["thickness"] = json!(0.12 + 0.25 * u(3));
            defect = "thin_wall";
        }
        7 => {
            mode = "volume";
            geometry["thickness"] = json!(depth);
            geometry["holes"] = single;
        }
        8 => {
            mode = "volume";
            geometry = json!({"kind":"bracket","width":w,"height":h,"depth":depth,"wall":0.25*w.min(h),"throughhole_radius":0.06*w.min(h)});
        }
        9 => {
            mode = "surface";
            geometry = json!({"kind":"sphere","radius":w*0.4});
        }
        10 => {
            mode = "surface";
            geometry = json!({"kind":"box","width":w,"height":h,"depth":depth});
        }
        11 => {
            mode = "surface";
            geometry["holes"] = single;
            geometry["corner_radius"] = json!(w.min(h) * 0.07);
        }
        12 => {
            mode = "volume";
            geometry = json!({"kind":"box","width":w,"height":h,"depth":depth});
        }
        13 => {
            mode = "volume";
            geometry = json!({"kind":"sphere","radius":w*0.35});
        }
        14 => {
            let t = w.min(h) * (0.12 + 0.1 * u(3));
            geometry = json!({"kind":"polygon","outer":[[0.,0.],[w,0.],[w,h],[0.,h]],"holes":[[[t,t],[t,h-t],[w-t,h-t],[w-t,t]]]});
        }
        15 => {
            mode = "volume";
            geometry = json!({"kind":"bracket","width":w,"height":h,"depth":depth,"wall":w.min(h)*(0.08+0.04*u(3))});
            defect = "thin_wall";
        }
        16 => {
            mode = "surface";
            geometry = json!({"kind":"box","width":w,"height":h,"depth":0.08+0.2*u(3)});
            defect = "thin_surface_spacing";
        }
        17 => {
            geometry = json!({"kind":"polygon","outer":[[0.,0.],[w,h],[w,0.],[0.,h]]});
            defect = "self_intersection";
        }
        18 => {
            let r = w.min(h) * 0.15;
            let gap = w.min(h) * (0.003 + 0.015 * u(3));
            geometry["holes"] = json!([{"x":w*0.5-r-gap*0.5,"y":h*0.5,"radius":r},{"x":w*0.5+r+gap*0.5,"y":h*0.5,"radius":r}]);
            defect = "narrow_gap";
        }
        19 => {
            let tiny = w * (0.001 + 0.003 * u(3));
            geometry =
                json!({"kind":"polygon","outer":[[0.,0.],[w-tiny,0.],[w,tiny],[w,h],[0.,h]]});
            defect = "tiny_edge";
        }
        20 => {
            geometry = json!({"kind":"polygon","outer":[[0.,0.],[w,0.],[w,h*0.3],[w*0.7,h*0.3],[w*0.7,h*0.65],[w*0.4,h*0.65],[w*0.4,h],[0.,h]]})
        }
        21 => {
            geometry["corner_radius"] = json!(w.min(h) * (0.015 + 0.02 * u(3)));
            geometry["holes"] = single;
            defect = "small_radius";
        }
        22 => {
            mode = "volume";
            geometry["thickness"] = json!(depth);
            geometry["holes"] = json!([{"x":w*0.25,"y":h*0.3,"radius":hole*0.45},{"x":w*0.75,"y":h*0.3,"radius":hole*0.45},{"x":w*0.25,"y":h*0.7,"radius":hole*0.45},{"x":w*0.75,"y":h*0.7,"radius":hole*0.45}]);
        }
        _ => {
            mode = "surface";
            geometry["holes"] = multiple;
            geometry["chamfer"] = json!(w.min(h) * 0.03);
        }
    }
    // Resolution has an independent, injective structured coordinate within
    // every family, so repeated CAD primitives still have distinct settings.
    let resolution = if random {
        u(7)
    } else {
        (ordinal as f64 + 0.5) / count as f64
    };
    let size = w.min(h) * (0.10 + 0.11 * resolution);
    let mut project = json!({"schema_version":1,"name":format!("{}_{index:07}",FAMILIES[family]),"mode":mode,
        "units":{"input":"mm","internal":"m"},"geometry":geometry,
        "mesh":{"size":size,"min_size":size*0.22,"order":1,"algorithm":"automatic","recombine":false,"threads":1,"max_elements":50000,"smoothing":1,"optimize":true,"curvature_elements":16},
        "repair":{"enabled":false,"tolerance":1e-7,"sew":true,"small_edges":true,"small_faces":false,"make_solids":false},
        "quality_policy":{"poor_threshold":0.2,"minimum_quality":0.03,"strict":false}});
    // Small local spherical sizing is part of the configuration, independently
    // of the candidate action, and therefore also present during reference CAD.
    if family % 4 == 0 {
        project["refinement_regions"] = json!([{"center":[w*(0.25+0.5*u(4)),h*(0.25+0.5*u(5)),if mode=="volume"{depth*0.5}else{0.}],"radius":w.min(h)*(0.13+0.1*u(6)),"size":size*0.6}]);
    }
    let geometry_id =
        hash_json(&json!({"geometry":project["geometry"],"mode":mode,"units":project["units"]}));
    let case_id = hash_json(
        &json!({"generator":GENERATOR_VERSION,"seed":plan.seed,"index":index,"project":project}),
    );
    let zone_template = if family % 3 == 0 && family != 17 {
        json!({"kind":match(family/3+ordinal as usize)%3{0=>"contact",1=>"interface",_=>"stress"},"source":"synthetic_marking_without_mechanical_solution","dimension":if mode=="volume"{2}else{1},"selector":if ordinal%2==0{"first"}else{"last"},"buffer":w.min(h)*(0.01+0.02*u(5)),"minimum_quality":0.3})
    } else {
        Value::Null
    };
    Ok(
        json!({"schema":"atelier.corpus.generated_case.v1","case_id":case_id,"index":index,"origin":if random{"random"}else{"structured"},
        "family":FAMILIES[family],"family_index":family,"geometry_id":geometry_id,"geometry_group":geometry_id,"split":split_for_family(family),
        "expected_invalid":family==17,"defect":defect,"critical_zone_template":zone_template,"project":project}),
    )
}

pub fn strategies(project: &Value, count: u64) -> Vec<(String, Value)> {
    let volume = project["mode"] == "volume";
    let local = project["refinement_regions"]
        .as_array()
        .is_some_and(|a| !a.is_empty())
        || project["local_sizes"]
            .as_array()
            .is_some_and(|a| !a.is_empty());
    let base = project["mesh"]["size"].as_f64().unwrap_or(1.);
    let minimum = project["mesh"]["min_size"].as_f64().unwrap_or(base * 0.2);
    let mut rows = Vec::new();
    for index in 0..count {
        let mut p = project.clone();
        let id = match index {
            0 => {
                p["mesh"]["algorithm"] = json!("automatic");
                "automatic_base"
            }
            1 => {
                p["mesh"]["algorithm"] = json!("delaunay");
                p["mesh"]["smoothing"] = json!(5);
                "delaunay_smoothed"
            }
            2 if !volume || !local => {
                p["mesh"]["algorithm"] = json!("frontal");
                "frontal_base"
            }
            2 => {
                p["mesh"]["algorithm"] = json!("delaunay");
                p["mesh"]["size"] = json!((base * 0.85).max(minimum));
                "delaunay_finer_085"
            }
            _ => {
                p["mesh"]["algorithm"] = json!("delaunay");
                p["mesh"]["size"] = json!((base * 0.65).max(minimum));
                p["mesh"]["smoothing"] = json!(8);
                "delaunay_finer_065"
            }
        };
        rows.push((id.to_string(), p));
    }
    rows
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn million_plan_is_small_and_deterministic() {
        let p = Plan::parse(&Value::Null).unwrap();
        assert_eq!(p.total(), 1_000_000);
        assert!(serde_json::to_vec(&p).unwrap().len() < 200);
        assert_eq!(case_at(999999, &p).unwrap(), case_at(999999, &p).unwrap());
    }
    #[test]
    fn quotas_and_origins_are_exact() {
        let p = Plan::parse(&json!({"structured":7,"random":11})).unwrap();
        assert_eq!(case_at(6, &p).unwrap()["origin"], "structured");
        assert_eq!(case_at(7, &p).unwrap()["origin"], "random");
        assert!(case_at(18, &p).is_err());
    }
    #[test]
    fn split_does_not_change_with_mesh_or_sampling_origin() {
        let p = Plan::parse(&json!({"structured":48,"random":48})).unwrap();
        for i in 0..24 {
            let a = case_at(i, &p).unwrap();
            let b = case_at(i + 24, &p).unwrap();
            let c = case_at(i + 48, &p).unwrap();
            assert_eq!(a["split"], b["split"]);
            assert_eq!(a["split"], c["split"]);
        }
    }
    #[test]
    fn random_parameters_have_one_sample_per_stratum() {
        for dim in 0..8 {
            let n = 137;
            let set: std::collections::BTreeSet<_> = (0..n)
                .map(|i| (lhs(42, 3, i, n, dim) * n as f64).floor() as u64)
                .collect();
            assert_eq!(set.len(), n as usize);
        }
    }
    #[test]
    fn structured_configurations_are_distinct_even_on_spheres() {
        let p = Plan::parse(&json!({"structured":2400,"random":0})).unwrap();
        let mut ids = std::collections::BTreeSet::new();
        for i in (9..2400).step_by(24) {
            let c = case_at(i, &p).unwrap();
            let mut project = c["project"].clone();
            project.as_object_mut().unwrap().remove("name");
            assert!(ids.insert(hash_json(&project)));
        }
    }
    #[test]
    fn local_volume_strategies_never_use_unsupported_frontal() {
        let p = Plan::parse(&Value::Null).unwrap();
        let c = case_at(12, &p).unwrap();
        for (_, p) in strategies(&c["project"], 4) {
            assert_ne!(p["mesh"]["algorithm"], "frontal");
        }
    }
    #[test]
    fn synthetic_zones_cover_contacts_interfaces_and_marked_stress() {
        let p = Plan::parse(&json!({"structured":24,"random":0})).unwrap();
        let kinds = (0..24)
            .filter_map(|i| {
                case_at(i, &p).unwrap()["critical_zone_template"]["kind"]
                    .as_str()
                    .map(str::to_string)
            })
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(
            kinds,
            std::collections::BTreeSet::from([
                "contact".to_string(),
                "interface".to_string(),
                "stress".to_string()
            ])
        );
    }
}
