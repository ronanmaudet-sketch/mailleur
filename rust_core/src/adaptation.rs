//! Solver-neutral adaptation planning. No solver, process or physical state is
//! inferred here: the caller supplies measured indicators for one exact mesh.
use serde_json::{json, Value};

type R<T> = Result<T, String>;

fn finite(v: &Value, name: &str, default: Option<f64>, positive: bool) -> R<f64> {
    let n = match v.get(name) {
        Some(x) => x
            .as_f64()
            .ok_or_else(|| format!("{name} doit être numérique"))?,
        None => default.ok_or_else(|| format!("{name} absent"))?,
    };
    if !n.is_finite() || (positive && n <= 0.) || (!positive && n < 0.) {
        return Err(format!("{name} est hors limites"));
    }
    Ok(n)
}

fn unit(name: &str) -> R<f64> {
    match name {
        "m" => Ok(1.),
        "cm" => Ok(0.01),
        "mm" => Ok(0.001),
        "in" => Ok(0.0254),
        _ => Err("Unité attendue : m, cm, mm ou in".into()),
    }
}

fn identity(value: &Value, name: &str) -> R<String> {
    let s = value[name]
        .as_str()
        .filter(|s| s.len() == 64 && s.bytes().all(|c| c.is_ascii_hexdigit()))
        .ok_or_else(|| format!("{name} doit être une empreinte SHA-256 de maillage"))?;
    Ok(s.to_ascii_lowercase())
}

pub fn plan(request: &Value) -> R<Value> {
    let original = request
        .get("project")
        .filter(|v| v.is_object())
        .ok_or("Projet absent")?;
    let feedback = &request["indicators"];
    if feedback["schema"] != "atelier.solver.indicators.v1" {
        return Err("Schéma d'indicateurs incompatible".into());
    }
    let source_id = identity(request, "source_mesh_id")?;
    if identity(feedback, "mesh_id")? != source_id {
        return Err("Les indicateurs ne correspondent pas au maillage courant".into());
    }
    for key in ["solver", "physics"] {
        if feedback[key]
            .as_str()
            .is_none_or(|s| s.trim().is_empty() || s.len() > 160)
        {
            return Err(format!(
                "Métadonnée d'indicateurs {key} absente ou invalide"
            ));
        }
    }
    let declared_converged = feedback["converged"]
        .as_bool()
        .ok_or("converged doit être un booléen explicite")?;
    let input_units = original["units"]["input"].as_str().unwrap_or("mm");
    let indicator_units = feedback["units"]
        .as_str()
        .ok_or("Unité des indicateurs absente")?;
    let ratio = unit(indicator_units)? / unit(input_units)?;
    let options = &request["adaptation"];
    if !options.is_null() && !options.is_object() {
        return Err("adaptation doit être un objet".into());
    }
    let tolerance = finite(options, "tolerance", Some(0.05), false)?;
    let reduction = finite(options, "refinement_factor", Some(0.7), true)?;
    if !(0.1..=0.95).contains(&reduction) {
        return Err("refinement_factor doit être compris entre 0.1 et 0.95".into());
    }
    let maximum = options
        .get("max_regions")
        .map(|x| x.as_u64().ok_or("max_regions doit être entier"))
        .transpose()?
        .unwrap_or(256);
    if !(1..=256).contains(&maximum) {
        return Err("max_regions doit être compris entre 1 et 256".into());
    }
    let rows = feedback["regions"]
        .as_array()
        .ok_or("Liste regions absente")?;
    if rows.len() > maximum as usize {
        return Err("Trop de régions d'indicateurs".into());
    }
    let mesh = &original["mesh"];
    let current = finite(mesh, "size", None, true)?;
    let minimum = finite(mesh, "min_size", Some(current.min(0.1)), true)?;
    if minimum > current {
        return Err("min_size dépasse size".into());
    }
    let shape = crate::backend::requested_shape(original)?;
    let structured = crate::backend::requests_direct_cells(original)?;
    let mut active = Vec::new();
    let mut above_tolerance = 0usize;
    let mut minimum_reached = 0usize;
    for row in rows {
        if !row.is_object() {
            return Err("Chaque région doit être un objet".into());
        }
        let center = row["center"]
            .as_array()
            .filter(|a| a.len() == 3)
            .ok_or("center doit contenir trois coordonnées")?;
        let mut point = Vec::with_capacity(3);
        for c in center {
            let x = c
                .as_f64()
                .filter(|x| x.is_finite())
                .ok_or("Coordonnée d'indicateur invalide")?
                * ratio;
            if !x.is_finite() {
                return Err("Conversion de coordonnées hors plage".into());
            }
            point.push(x);
        }
        let radius = finite(row, "radius", None, true)? * ratio;
        let error = finite(row, "error", None, false)?;
        let support_size = if structured {
            current
        } else {
            original["refinement_regions"]
                .as_array()
                .into_iter()
                .flatten()
                .filter(|r| r["center"] == json!(point) && r["radius"].as_f64() == Some(radius))
                .filter_map(|r| r["size"].as_f64())
                .fold(current, f64::min)
        };
        let raw_target = match row.get("target_size") {
            Some(_) => finite(row, "target_size", None, true)? * ratio,
            None => support_size * reduction,
        };
        if !radius.is_finite() || !raw_target.is_finite() {
            return Err("Conversion d'indicateur hors plage".into());
        }
        if error > tolerance {
            above_tolerance += 1;
            let target = raw_target.max(minimum);
            if target < support_size * (1. - 1e-12) {
                active.push(json!({"center":point,"radius":radius,"size":target}));
            } else {
                minimum_reached += 1;
            }
        }
    }
    if declared_converged && above_tolerance > 0 {
        return Err(
            "Le solveur déclare la convergence avec des régions au-dessus de la tolérance".into(),
        );
    }
    let mut selected = original.clone();
    let requires_remesh = !declared_converged && !active.is_empty();
    let mut strategy = "no_change";
    if requires_remesh {
        if structured {
            strategy = "global_structured_refinement";
            let target = active
                .iter()
                .filter_map(|r| r["size"].as_f64())
                .fold(current, f64::min);
            selected["mesh"]["size"] = json!(target);
            if let Some(values) = mesh["structured"].get("divisions") {
                let divisions = values
                    .as_array()
                    .filter(|a| a.len() == 3)
                    .ok_or("structured.divisions doit contenir trois entiers")?;
                let closed_surface =
                    original["mode"] == "surface" && original["geometry"]["kind"] == "box";
                let dimension = if original["mode"] == "volume" || closed_surface {
                    3
                } else {
                    2
                };
                let mut next = Vec::new();
                let mut product = 1u64;
                for (axis, value) in divisions.iter().enumerate() {
                    let n = value
                        .as_u64()
                        .filter(|n| (1..=2000).contains(n))
                        .ok_or("Division structurée hors limites")?;
                    let refined = if axis < dimension {
                        ((n as f64 * current / target).ceil() as u64).max(n + 1)
                    } else {
                        n
                    };
                    if refined > 2000 {
                        return Err("Le raffinement dépasserait 2000 divisions par axe".into());
                    }
                    if axis < dimension {
                        product = product
                            .checked_mul(refined)
                            .ok_or("Budget de divisions dépassé")?;
                    }
                    next.push(refined);
                }
                let base = if closed_surface {
                    next[0]
                        .checked_mul(next[1])
                        .and_then(|xy| {
                            next[0].checked_mul(next[2]).and_then(|xz| {
                                next[1]
                                    .checked_mul(next[2])
                                    .and_then(|yz| xy.checked_add(xz)?.checked_add(yz))
                            })
                        })
                        .and_then(|sum| sum.checked_mul(2))
                        .ok_or("Budget de faces dépassé")?
                } else {
                    let patches = if original["geometry"]["holes"]
                        .as_array()
                        .is_some_and(|h| h.len() == 1)
                    {
                        4
                    } else {
                        1
                    };
                    product
                        .checked_mul(patches)
                        .ok_or("Budget de blocs dépassé")?
                };
                let count = base
                    .checked_mul(match shape {
                        "pyramid" | "tetrahedron" => 6,
                        "prism" | "triangle" => 2,
                        _ => 1,
                    })
                    .ok_or("Budget d'éléments dépassé")?;
                let limit = mesh["max_elements"].as_u64().unwrap_or(200000);
                if count > limit {
                    return Err("Le raffinement structuré dépasserait le budget d'éléments".into());
                }
                selected["mesh"]["structured"]["divisions"] = json!(next);
            }
        } else {
            strategy = "local_isotropic_refinement";
            let mut merged = original["refinement_regions"]
                .as_array()
                .cloned()
                .unwrap_or_default();
            // Replace matching supports rather than appending an identical field
            // on every callback. The smallest requested size wins.
            for region in active.iter().cloned() {
                if let Some(existing) = merged
                    .iter_mut()
                    .find(|r| r["center"] == region["center"] && r["radius"] == region["radius"])
                {
                    if existing["size"].as_f64().unwrap_or(f64::INFINITY)
                        > region["size"].as_f64().unwrap()
                    {
                        *existing = region;
                    }
                } else {
                    merged.push(region);
                }
            }
            if merged.len() > 256 {
                return Err("Le cumul de raffinements dépasse 256 régions".into());
            }
            if selected["refinement_regions"] == json!(merged) {
                strategy = "no_change";
            }
            selected["refinement_regions"] = json!(merged);
        }
    }
    let changed = requires_remesh && selected != *original;
    Ok(
        json!({"schema":"atelier.adaptation.plan.v1","rust_backend":"rust","source_mesh_id":source_id,
        "selected_project":selected,"requires_remesh":changed,"converged":declared_converged,
        "strategy":strategy,"solver":feedback["solver"],"physics":feedback["physics"],
        "diagnostics":{"indicator_regions":rows.len(),"above_tolerance":above_tolerance,"actionable_regions":active.len(),
            "minimum_size_or_no_reduction":minimum_reached,"tolerance":tolerance,"project_units":input_units,
            "state_transfer":"The solver must transfer fields and internal state before continuing its calculation",
            "structured_refinement":"Structured meshes are regenerated globally; local nonconforming patches are never inserted"}}),
    )
}

pub fn capabilities() -> Value {
    json!({"schema":"atelier.api.capabilities.v1","api_version":"1.0","core_version":env!("CARGO_PKG_VERSION"),
        "operations":["mesh","inspect","remesh","learn","adapt_plan","capabilities","corpus_plan","corpus_status","corpus_preview","corpus_run","corpus_train"],
        "element_orders":[1,2],"default_element_order":2,
        "element_shapes":{
            "automatic":{"generation":"native_direct","dimension":[2,3],"surface_family":"quadrilateral","volume_family":"hexahedron"},
            "triangle":{"generation":"native_unstructured_or_direct_structured","direct_if":"mesh.structured explicitly supplied","dimension":[2]},
            "tetrahedron":{"generation":"native_unstructured_or_direct_structured","direct_if":"mesh.structured explicitly supplied","dimension":[3]},
            "quadrilateral":{"generation":"native_direct","dimension":[2],"geometry_scope":"compatible structured primitive blocks"},
            "hexahedron":{"generation":"native_direct","dimension":[3],"geometry_scope":"compatible structured primitive blocks"},
            "prism":{"generation":"native_direct","dimension":[3],"geometry_scope":"compatible structured primitive blocks"},
            "pyramid":{"generation":"native_direct","dimension":[3],"geometry_scope":"compatible structured primitive blocks"}},
        "recombination":false,"silent_family_fallback":false,
        "direct_structured":{"orders":[1,2],"order_two_nodes_emitted_directly":true,
            "intermediate_linear_mesh":false,"max_divisions_per_axis":2000,
            "layouts":{"serendipity":["QUAD8","HEX20","PRISM15","PYR13"],
                "complete":["QUAD9","HEX27","PRISM18","PYR14"]},
            "geometry_scope":["rectangle","box","convex four-corner polygon","plate with zero or one interior circular hole","extrusion of compatible footprint"],
            "arbitrary_cad_block_decomposition":false,"nonuniform_grid":"positive progression ratios",
            "local_size_fields":false,"periodic_correspondences":false,"strict_local_remesh":false},
        "solver_coupling":{"solver_neutral":true,"indicator_schema":"atelier.solver.indicators.v1",
            "mesh_identity":"sha256(mesh.msh)","state_transfer":"solver_responsibility",
            "structured_adaptation":"global regeneration with conforming structured cells",
            "unstructured_adaptation":"local isotropic size fields followed by regeneration"}})
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request() -> Value {
        json!({"source_mesh_id":"a".repeat(64),"project":{"mode":"volume","units":{"input":"mm"},
            "mesh":{"size":1.,"min_size":0.1,"order":2,"element_shape":"hexahedron","structured":{"divisions":[2,3,4]},"max_elements":10000}},
            "indicators":{"schema":"atelier.solver.indicators.v1","solver":"test_adapter","physics":"thermal",
                "mesh_id":"a".repeat(64),"units":"m","converged":false,
                "regions":[{"center":[0.001,0.002,0.003],"radius":0.001,"error":0.2,"target_size":0.0005}]}})
    }
    #[test]
    fn structured_refinement_preserves_family_and_order() {
        let p = plan(&request()).unwrap();
        assert_eq!(p["selected_project"]["mesh"]["element_shape"], "hexahedron");
        assert_eq!(p["selected_project"]["mesh"]["order"], 2);
        assert_eq!(
            p["selected_project"]["mesh"]["structured"]["divisions"],
            json!([4, 6, 8])
        );
        assert_eq!(p["selected_project"]["mesh"]["size"], 0.5);
        assert_eq!(p["requires_remesh"], true);
    }
    #[test]
    fn mesh_identity_and_contradictory_convergence_are_rejected() {
        let mut r = request();
        r["indicators"]["mesh_id"] = json!("b".repeat(64));
        assert!(plan(&r).is_err());
        let mut r = request();
        r["indicators"]["converged"] = json!(true);
        assert!(plan(&r).is_err());
    }
    #[test]
    fn converged_feedback_keeps_project_unchanged() {
        let mut r = request();
        r["indicators"]["converged"] = json!(true);
        r["indicators"]["regions"] = json!([]);
        let p = plan(&r).unwrap();
        assert_eq!(p["requires_remesh"], false);
        assert_eq!(p["selected_project"], r["project"]);
    }
    #[test]
    fn local_fields_are_converted_to_input_units_and_deduplicated() {
        let mut r = request();
        r["project"]["mesh"]["element_shape"] = json!("tetrahedron");
        r["project"]["mesh"].as_object_mut().unwrap().remove("structured");
        let p = plan(&r).unwrap();
        let region = &p["selected_project"]["refinement_regions"][0];
        assert_eq!(region["center"], json!([1., 2., 3.]));
        assert_eq!(region["radius"], 1.);
        assert_eq!(region["size"], 0.5);
        r["project"] = p["selected_project"].clone();
        assert_eq!(plan(&r).unwrap()["requires_remesh"], false);
    }
    #[test]
    fn limits_and_minimum_size_prevent_unbounded_refinement() {
        let mut r = request();
        r["project"]["mesh"]["max_elements"] = json!(5);
        assert!(plan(&r).is_err());
        let mut r = request();
        r["project"]["mesh"]["min_size"] = json!(1.);
        assert_eq!(plan(&r).unwrap()["requires_remesh"], false);
        let mut r = request();
        r["indicators"]["regions"][0]["error"] = json!(-1.);
        assert!(plan(&r).is_err());
    }
    #[test]
    fn automatic_local_refinement_uses_the_previous_support_size() {
        let mut r = request();
        r["project"]["mesh"]["element_shape"] = json!("tetrahedron");
        r["project"]["mesh"].as_object_mut().unwrap().remove("structured");
        r["indicators"]["regions"][0]
            .as_object_mut()
            .unwrap()
            .remove("target_size");
        let first = plan(&r).unwrap();
        r["project"] = first["selected_project"].clone();
        let second = plan(&r).unwrap();
        assert_eq!(second["requires_remesh"], true);
        let h = second["selected_project"]["refinement_regions"][0]["size"]
            .as_f64()
            .unwrap();
        assert!((h - 0.49).abs() < 1e-12);
    }
    #[test]
    fn direct_simplex_plans_keep_structured_refinement_and_count_all_cells() {
        for (shape, mode, limit) in [("triangle", "planar", 47), ("tetrahedron", "volume", 1151)] {
            let mut r = request();
            r["project"]["mesh"]["element_shape"] = json!(shape);
            r["project"]["mode"] = json!(mode);
            if mode == "planar" {
                r["project"]["mesh"]["structured"]["divisions"] = json!([2, 3, 1]);
            }
            let p = plan(&r).unwrap();
            assert_eq!(p["strategy"], "global_structured_refinement");
            assert!(p["selected_project"]["refinement_regions"].is_null());
            r["project"]["mesh"]["max_elements"] = json!(limit);
            assert!(plan(&r).is_err());
        }
    }
    #[test]
    fn shell_box_and_four_patch_hole_budgets_are_not_underestimated() {
        let mut r = request();
        r["project"]["mode"] = json!("surface");
        r["project"]["geometry"] = json!({"kind":"box"});
        r["project"]["mesh"]["element_shape"] = json!("quadrilateral");
        let p = plan(&r).unwrap();
        assert_eq!(
            p["selected_project"]["mesh"]["structured"]["divisions"],
            json!([4, 6, 8])
        );
        r["project"]["mesh"]["max_elements"] = json!(207);
        assert!(plan(&r).is_err());
        r = request();
        r["project"]["geometry"] = json!({"kind":"plate","holes":[{"x":1.,"y":1.,"radius":0.1}]});
        r["project"]["mesh"]["max_elements"] = json!(767);
        assert!(plan(&r).is_err());
    }
}
