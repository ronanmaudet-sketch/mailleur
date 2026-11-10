//! Safe, process-local wrapper around the Gmsh 4.15 C ABI.
//!
//! Geometry/meshing run through a native library, never through Python. All
//! allocations returned by Gmsh are copied once and released with gmshFree.
//! A context is deliberately neither Send nor Sync: Gmsh owns global state.

use libloading::{Library, Symbol};
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::marker::PhantomData;
use std::path::Path;
use std::ptr;
use std::rc::Rc;

pub type DimTag = (i32, i32);
pub type PeriodicNodes = (i32, Vec<usize>, Vec<usize>, Vec<f64>);

#[derive(Clone, Debug)]
pub struct ElementBlock {
    pub element_type: i32,
    pub tags: Vec<usize>,
    pub nodes: Vec<usize>,
    pub name: String,
    pub dim: i32,
    pub order: i32,
    pub num_nodes: usize,
    pub num_primary: usize,
}

pub struct Gmsh {
    lib: Library,
    initialized: bool,
    _not_thread_safe: PhantomData<Rc<()>>,
}

macro_rules! invoke {
    ($self:ident, $name:literal, $signature:ty $(, $argument:expr)* $(,)?) => {{
        let function: Symbol<$signature> = unsafe { $self.lib.get(concat!($name, "\0").as_bytes()) }
            .map_err(|error| format!("Symbole Gmsh {} indisponible : {error}", $name))?;
        unsafe { function($($argument),*) }
    }};
}

fn text(value: &str) -> Result<CString, String> {
    CString::new(value).map_err(|_| "Une chaîne contient un caractère nul".to_string())
}

fn path_text(path: &Path) -> Result<CString, String> {
    text(
        path.to_str()
            .ok_or("Le chemin doit être représentable en UTF-8")?,
    )
}

fn flattened(items: &[DimTag]) -> Vec<i32> {
    items.iter().flat_map(|(dim, tag)| [*dim, *tag]).collect()
}

fn paired(values: Vec<i32>) -> Result<Vec<DimTag>, String> {
    if values.len() % 2 != 0 {
        return Err("Gmsh a retourné une liste d'entités incohérente".into());
    }
    Ok(values
        .chunks_exact(2)
        .map(|pair| (pair[0], pair[1]))
        .collect())
}

impl Gmsh {
    pub fn new(library_path: &str) -> Result<Self, String> {
        // The caller supplies the installed, official native library path.
        let lib = unsafe { Library::new(library_path) }
            .map_err(|error| format!("Chargement du moteur natif impossible : {error}"))?;
        let mut context = Self {
            lib,
            initialized: false,
            _not_thread_safe: PhantomData,
        };
        let mut ierr = 0;
        let active = invoke!(
            context,
            "gmshIsInitialized",
            unsafe extern "C" fn(*mut c_int) -> c_int,
            &mut ierr
        );
        context.check(ierr)?;
        if active != 0 {
            return Err("Un contexte Gmsh est déjà actif dans ce processus".into());
        }
        invoke!(
            context,
            "gmshInitialize",
            unsafe extern "C" fn(c_int, *mut *mut c_char, c_int, c_int, *mut c_int),
            0,
            ptr::null_mut(),
            0,
            0,
            &mut ierr
        );
        context.check(ierr)?;
        context.initialized = true;
        context.set_number("General.Terminal", 0.0)?;
        context.set_number("General.AbortOnError", 2.0)?;
        // OCC's analytical boxes can retain the unscaled location of degenerate
        // sphere edges after dilation. Configure before any shape/synchronize,
        // including imports and replay, so every catalogue uses physical SI
        // bounds. These tighter tessellation bounds are approximate, not exact
        // analytical envelopes (Gmsh documents this option as slower).
        context.set_number("Geometry.OCCBoundsUseStl", 1.0)?;
        let version = context.get_string("General.Version")?;
        if !version.starts_with("4.15.") {
            return Err(format!(
                "API Gmsh 4.15 attendue ; bibliothèque chargée : {version}"
            ));
        }
        Ok(context)
    }

    fn check(&self, ierr: i32) -> Result<(), String> {
        if ierr == 0 {
            return Ok(());
        }
        let mut message: *mut c_char = ptr::null_mut();
        let mut error = 0;
        let result = unsafe {
            self.lib
                .get::<unsafe extern "C" fn(*mut *mut c_char, *mut c_int)>(
                    b"gmshLoggerGetLastError\0",
                )
        };
        if let Ok(function) = result {
            unsafe {
                function(&mut message, &mut error);
            }
        }
        let detail = if !message.is_null() {
            let output = unsafe { CStr::from_ptr(message) }
                .to_string_lossy()
                .into_owned();
            self.free(message as *mut c_void);
            output
        } else {
            String::new()
        };
        if detail.is_empty() {
            Err(format!("Erreur Gmsh native {ierr}"))
        } else {
            Err(format!("Gmsh : {detail}"))
        }
    }

    fn free(&self, pointer: *mut c_void) {
        if pointer.is_null() {
            return;
        }
        // gmshFree is part of every supported C ABI and does not return errors.
        if let Ok(function) = unsafe {
            self.lib
                .get::<unsafe extern "C" fn(*mut c_void)>(b"gmshFree\0")
        } {
            unsafe {
                function(pointer);
            }
        }
    }

    fn take<T: Copy>(&self, pointer: *mut T, length: usize) -> Result<Vec<T>, String> {
        if pointer.is_null() {
            return if length == 0 {
                Ok(Vec::new())
            } else {
                Err("Tableau natif Gmsh manquant".into())
            };
        }
        // Lengths originate from the native ABI, not from user-controlled JSON.
        let result = unsafe { std::slice::from_raw_parts(pointer, length) }.to_vec();
        self.free(pointer as *mut c_void);
        Ok(result)
    }

    fn take_text(&self, pointer: *mut c_char) -> String {
        if pointer.is_null() {
            return String::new();
        }
        let result = unsafe { CStr::from_ptr(pointer) }
            .to_string_lossy()
            .into_owned();
        self.free(pointer as *mut c_void);
        result
    }

    fn take_nested<T: Copy>(
        &self,
        pointer: *mut *mut T,
        lengths: *mut usize,
        count: usize,
    ) -> Result<Vec<Vec<T>>, String> {
        let sizes_result = self.take(lengths, count);
        if pointer.is_null() {
            sizes_result?;
            return if count == 0 {
                Ok(Vec::new())
            } else {
                Err("Tableau de tableaux Gmsh manquant".into())
            };
        }
        let pointers = unsafe { std::slice::from_raw_parts(pointer, count) }.to_vec();
        self.free(pointer as *mut c_void);
        let sizes = match sizes_result {
            Ok(sizes) => sizes,
            Err(error) => {
                for item in pointers {
                    self.free(item as *mut c_void);
                }
                return Err(error);
            }
        };
        let mut result = Vec::with_capacity(count);
        let mut first_error = None;
        for (item, size) in pointers.into_iter().zip(sizes) {
            match self.take(item, size) {
                Ok(values) => result.push(values),
                Err(error) => {
                    if first_error.is_none() {
                        first_error = Some(error);
                    }
                }
            }
        }
        if let Some(error) = first_error {
            Err(error)
        } else {
            Ok(result)
        }
    }

    pub fn set_number(&self, name: &str, value: f64) -> Result<(), String> {
        if !value.is_finite() {
            return Err("Une option Gmsh doit être finie".into());
        }
        let name = text(name)?;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshOptionSetNumber",
            unsafe extern "C" fn(*const c_char, f64, *mut c_int),
            name.as_ptr(),
            value,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn set_string(&self, name: &str, value: &str) -> Result<(), String> {
        let name = text(name)?;
        let value = text(value)?;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshOptionSetString",
            unsafe extern "C" fn(*const c_char, *const c_char, *mut c_int),
            name.as_ptr(),
            value.as_ptr(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn get_string(&self, name: &str) -> Result<String, String> {
        let name = text(name)?;
        let mut output = ptr::null_mut();
        let mut ierr = 0;
        invoke!(
            self,
            "gmshOptionGetString",
            unsafe extern "C" fn(*const c_char, *mut *mut c_char, *mut c_int),
            name.as_ptr(),
            &mut output,
            &mut ierr
        );
        let result = self.take_text(output);
        self.check(ierr)?;
        Ok(result)
    }
    pub fn get_number(&self, name: &str) -> Result<f64, String> {
        let name = text(name)?;
        let mut value = 0.0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshOptionGetNumber",
            unsafe extern "C" fn(*const c_char, *mut f64, *mut c_int),
            name.as_ptr(),
            &mut value,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(value)
    }
    pub fn add_model(&self, name: &str) -> Result<(), String> {
        let name = text(name)?;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelAdd",
            unsafe extern "C" fn(*const c_char, *mut c_int),
            name.as_ptr(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn remove_model(&self) -> Result<(), String> {
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelRemove",
            unsafe extern "C" fn(*mut c_int),
            &mut ierr
        );
        self.check(ierr)
    }
    fn file_operation(&self, symbol: &[u8], path: &Path) -> Result<(), String> {
        let path = path_text(path)?;
        let mut ierr = 0;
        let function: Symbol<unsafe extern "C" fn(*const c_char, *mut c_int)> =
            unsafe { self.lib.get(symbol) }.map_err(|error| error.to_string())?;
        unsafe {
            function(path.as_ptr(), &mut ierr);
        }
        self.check(ierr)
    }
    pub fn open(&self, path: &Path) -> Result<(), String> {
        self.file_operation(b"gmshOpen\0", path)
    }
    pub fn merge(&self, path: &Path) -> Result<(), String> {
        self.file_operation(b"gmshMerge\0", path)
    }
    pub fn write(&self, path: &Path) -> Result<(), String> {
        self.file_operation(b"gmshWrite\0", path)
    }

    pub fn entities(&self, dim: i32) -> Result<Vec<DimTag>, String> {
        let mut output = ptr::null_mut();
        let mut length = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelGetEntities",
            unsafe extern "C" fn(*mut *mut c_int, *mut usize, c_int, *mut c_int),
            &mut output,
            &mut length,
            dim,
            &mut ierr
        );
        let values = self.take(output, length);
        self.check(ierr)?;
        paired(values?)
    }
    pub fn boundary(
        &self,
        entities: &[DimTag],
        combined: bool,
        oriented: bool,
        recursive: bool,
    ) -> Result<Vec<DimTag>, String> {
        let input = flattened(entities);
        let mut output = ptr::null_mut();
        let mut length = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelGetBoundary",
            unsafe extern "C" fn(
                *const c_int,
                usize,
                *mut *mut c_int,
                *mut usize,
                c_int,
                c_int,
                c_int,
                *mut c_int,
            ),
            input.as_ptr(),
            input.len(),
            &mut output,
            &mut length,
            combined as i32,
            oriented as i32,
            recursive as i32,
            &mut ierr
        );
        let values = self.take(output, length);
        self.check(ierr)?;
        paired(values?)
    }
    pub fn bbox(&self, dim: i32, tag: i32) -> Result<[f64; 6], String> {
        let mut values = [0.0; 6];
        let mut ierr = 0;
        let [xmin, ymin, zmin, xmax, ymax, zmax] = &mut values;
        invoke!(
            self,
            "gmshModelGetBoundingBox",
            unsafe extern "C" fn(
                c_int,
                c_int,
                *mut f64,
                *mut f64,
                *mut f64,
                *mut f64,
                *mut f64,
                *mut f64,
                *mut c_int,
            ),
            dim,
            tag,
            xmin,
            ymin,
            zmin,
            xmax,
            ymax,
            zmax,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(values)
    }
    pub fn entity_name(&self, dim: i32, tag: i32) -> Result<String, String> {
        let mut output = ptr::null_mut();
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelGetEntityName",
            unsafe extern "C" fn(c_int, c_int, *mut *mut c_char, *mut c_int),
            dim,
            tag,
            &mut output,
            &mut ierr
        );
        let name = self.take_text(output);
        self.check(ierr)?;
        Ok(name)
    }
    pub fn set_visibility(
        &self,
        entities: &[DimTag],
        visible: bool,
        recursive: bool,
    ) -> Result<(), String> {
        let values = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelSetVisibility",
            unsafe extern "C" fn(*const c_int, usize, c_int, c_int, *mut c_int),
            values.as_ptr(),
            values.len(),
            visible as i32,
            recursive as i32,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn physical_group(&self, dim: i32, tags: &[i32], name: &str) -> Result<i32, String> {
        let name = text(name)?;
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelAddPhysicalGroup",
            unsafe extern "C" fn(
                c_int,
                *const c_int,
                usize,
                c_int,
                *const c_char,
                *mut c_int,
            ) -> c_int,
            dim,
            tags.as_ptr(),
            tags.len(),
            -1,
            name.as_ptr(),
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    /// Read provenance only: source groups are not reapplied after a geometric
    /// skin reconstruction, whose new CAD entities have different identities.
    pub fn physical_group_inventory(&self) -> Result<Vec<(i32,i32,String,Vec<i32>)>,String> {
        let mut groups=ptr::null_mut();let mut length=0;let mut ierr=0;
        invoke!(self,"gmshModelGetPhysicalGroups",
            unsafe extern "C" fn(*mut *mut c_int,*mut usize,c_int,*mut c_int),
            &mut groups,&mut length,-1,&mut ierr);
        let values=self.take(groups,length);self.check(ierr)?;
        let groups=paired(values?)?;
        if groups.len()>10_000 {return Err("Trop de groupes physiques pour l'inventaire d'import".into());}
        let mut result=Vec::with_capacity(groups.len());
        for (dimension,tag) in groups {
            let mut name=ptr::null_mut();
            invoke!(self,"gmshModelGetPhysicalName",
                unsafe extern "C" fn(c_int,c_int,*mut *mut c_char,*mut c_int),
                dimension,tag,&mut name,&mut ierr);
            let name=self.take_text(name);self.check(ierr)?;
            let mut entities=ptr::null_mut();let mut count=0;
            invoke!(self,"gmshModelGetEntitiesForPhysicalGroup",
                unsafe extern "C" fn(c_int,c_int,*mut *mut c_int,*mut usize,*mut c_int),
                dimension,tag,&mut entities,&mut count,&mut ierr);
            let entities=self.take(entities,count);self.check(ierr)?;
            result.push((dimension,tag,name,entities?));
        }
        Ok(result)
    }
    pub fn remove_physical_groups(&self, entities: &[DimTag]) -> Result<(), String> {
        let values = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelRemovePhysicalGroups",
            unsafe extern "C" fn(*const c_int, usize, *mut c_int),
            values.as_ptr(),
            values.len(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn occ_import(&self, path: &Path) -> Result<Vec<DimTag>, String> {
        let filename = path_text(path)?;
        let format = text("")?;
        let mut output = ptr::null_mut();
        let mut length = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelOccImportShapes",
            unsafe extern "C" fn(
                *const c_char,
                *mut *mut c_int,
                *mut usize,
                c_int,
                *const c_char,
                *mut c_int,
            ),
            filename.as_ptr(),
            &mut output,
            &mut length,
            1,
            format.as_ptr(),
            &mut ierr
        );
        let values = self.take(output, length);
        self.check(ierr)?;
        paired(values?)
    }
    pub fn occ_sync(&self) -> Result<(), String> {
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelOccSynchronize",
            unsafe extern "C" fn(*mut c_int),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn geo_sync(&self) -> Result<(), String> {
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelGeoSynchronize",
            unsafe extern "C" fn(*mut c_int),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn occ_add_rectangle(
        &self,
        x: f64,
        y: f64,
        z: f64,
        dx: f64,
        dy: f64,
        radius: f64,
    ) -> Result<i32, String> {
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelOccAddRectangle",
            unsafe extern "C" fn(f64, f64, f64, f64, f64, c_int, f64, *mut c_int) -> c_int,
            x,
            y,
            z,
            dx,
            dy,
            -1,
            radius,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    pub fn occ_add_disk(&self, x: f64, y: f64, z: f64, rx: f64, ry: f64) -> Result<i32, String> {
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelOccAddDisk",
            unsafe extern "C" fn(
                f64,
                f64,
                f64,
                f64,
                f64,
                c_int,
                *const f64,
                usize,
                *const f64,
                usize,
                *mut c_int,
            ) -> c_int,
            x,
            y,
            z,
            rx,
            ry,
            -1,
            ptr::null(),
            0,
            ptr::null(),
            0,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    pub fn occ_add_box(
        &self,
        x: f64,
        y: f64,
        z: f64,
        dx: f64,
        dy: f64,
        dz: f64,
    ) -> Result<i32, String> {
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelOccAddBox",
            unsafe extern "C" fn(f64, f64, f64, f64, f64, f64, c_int, *mut c_int) -> c_int,
            x,
            y,
            z,
            dx,
            dy,
            dz,
            -1,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    pub fn occ_add_sphere(&self, x: f64, y: f64, z: f64, radius: f64) -> Result<i32, String> {
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelOccAddSphere",
            unsafe extern "C" fn(f64, f64, f64, f64, c_int, f64, f64, f64, *mut c_int) -> c_int,
            x,
            y,
            z,
            radius,
            -1,
            -std::f64::consts::FRAC_PI_2,
            std::f64::consts::FRAC_PI_2,
            2.0 * std::f64::consts::PI,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    pub fn occ_add_cylinder(
        &self,
        x: f64,
        y: f64,
        z: f64,
        dx: f64,
        dy: f64,
        dz: f64,
        radius: f64,
    ) -> Result<i32, String> {
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelOccAddCylinder",
            unsafe extern "C" fn(
                f64,
                f64,
                f64,
                f64,
                f64,
                f64,
                f64,
                c_int,
                f64,
                *mut c_int,
            ) -> c_int,
            x,
            y,
            z,
            dx,
            dy,
            dz,
            radius,
            -1,
            2.0 * std::f64::consts::PI,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    pub fn occ_add_point(&self, x: f64, y: f64, z: f64) -> Result<i32, String> {
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelOccAddPoint",
            unsafe extern "C" fn(f64, f64, f64, f64, c_int, *mut c_int) -> c_int,
            x,
            y,
            z,
            0.0,
            -1,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    pub fn occ_add_line(&self, a: i32, b: i32) -> Result<i32, String> {
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelOccAddLine",
            unsafe extern "C" fn(c_int, c_int, c_int, *mut c_int) -> c_int,
            a,
            b,
            -1,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    fn add_loop_like(&self, symbol: &[u8], tags: &[i32]) -> Result<i32, String> {
        let mut ierr = 0;
        let function: Symbol<
            unsafe extern "C" fn(*const c_int, usize, c_int, *mut c_int) -> c_int,
        > = unsafe { self.lib.get(symbol) }.map_err(|error| error.to_string())?;
        let tag = unsafe { function(tags.as_ptr(), tags.len(), -1, &mut ierr) };
        self.check(ierr)?;
        Ok(tag)
    }
    pub fn occ_add_loop(&self, tags: &[i32]) -> Result<i32, String> {
        self.add_loop_like(b"gmshModelOccAddCurveLoop\0", tags)
    }
    pub fn occ_add_plane_surface(&self, tags: &[i32]) -> Result<i32, String> {
        self.add_loop_like(b"gmshModelOccAddPlaneSurface\0", tags)
    }
    pub fn geo_add_surface_loop(&self, tags: &[i32]) -> Result<i32, String> {
        self.add_loop_like(b"gmshModelGeoAddSurfaceLoop\0", tags)
    }
    pub fn geo_add_volume(&self, tags: &[i32]) -> Result<i32, String> {
        self.add_loop_like(b"gmshModelGeoAddVolume\0", tags)
    }

    fn boolean(
        &self,
        symbol: &[u8],
        objects: &[DimTag],
        tools: &[DimTag],
    ) -> Result<Vec<DimTag>, String> {
        let objects = flattened(objects);
        let tools = flattened(tools);
        let mut output = ptr::null_mut();
        let mut length = 0;
        let mut map = ptr::null_mut();
        let mut map_sizes = ptr::null_mut();
        let mut map_count = 0;
        let mut ierr = 0;
        type Function = unsafe extern "C" fn(
            *const c_int,
            usize,
            *const c_int,
            usize,
            *mut *mut c_int,
            *mut usize,
            *mut *mut *mut c_int,
            *mut *mut usize,
            *mut usize,
            c_int,
            c_int,
            c_int,
            *mut c_int,
        );
        let function: Symbol<Function> =
            unsafe { self.lib.get(symbol) }.map_err(|error| error.to_string())?;
        unsafe {
            function(
                objects.as_ptr(),
                objects.len(),
                tools.as_ptr(),
                tools.len(),
                &mut output,
                &mut length,
                &mut map,
                &mut map_sizes,
                &mut map_count,
                -1,
                1,
                1,
                &mut ierr,
            );
        }
        let values = self.take(output, length);
        let correspondence = self.take_nested(map, map_sizes, map_count);
        self.check(ierr)?;
        correspondence?;
        paired(values?)
    }
    pub fn occ_cut(&self, objects: &[DimTag], tools: &[DimTag]) -> Result<Vec<DimTag>, String> {
        self.boolean(b"gmshModelOccCut\0", objects, tools)
    }
    pub fn occ_fuse(&self, objects: &[DimTag], tools: &[DimTag]) -> Result<Vec<DimTag>, String> {
        self.boolean(b"gmshModelOccFuse\0", objects, tools)
    }
    pub fn occ_extrude(
        &self,
        entities: &[DimTag],
        dx: f64,
        dy: f64,
        dz: f64,
    ) -> Result<Vec<DimTag>, String> {
        let entities = flattened(entities);
        let mut output = ptr::null_mut();
        let mut length = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelOccExtrude",
            unsafe extern "C" fn(
                *const c_int,
                usize,
                f64,
                f64,
                f64,
                *mut *mut c_int,
                *mut usize,
                *const c_int,
                usize,
                *const f64,
                usize,
                c_int,
                *mut c_int,
            ),
            entities.as_ptr(),
            entities.len(),
            dx,
            dy,
            dz,
            &mut output,
            &mut length,
            ptr::null(),
            0,
            ptr::null(),
            0,
            0,
            &mut ierr
        );
        let values = self.take(output, length);
        self.check(ierr)?;
        paired(values?)
    }
    pub fn occ_heal(
        &self,
        entities: &[DimTag],
        tolerance: f64,
        fix_degenerated: bool,
        small_edges: bool,
        small_faces: bool,
        sew: bool,
        make_solids: bool,
    ) -> Result<Vec<DimTag>, String> {
        let entities = flattened(entities);
        let mut output = ptr::null_mut();
        let mut length = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelOccHealShapes",
            unsafe extern "C" fn(
                *mut *mut c_int,
                *mut usize,
                *const c_int,
                usize,
                f64,
                c_int,
                c_int,
                c_int,
                c_int,
                c_int,
                *mut c_int,
            ),
            &mut output,
            &mut length,
            entities.as_ptr(),
            entities.len(),
            tolerance,
            fix_degenerated as i32,
            small_edges as i32,
            small_faces as i32,
            sew as i32,
            make_solids as i32,
            &mut ierr
        );
        let values = self.take(output, length);
        self.check(ierr)?;
        paired(values?)
    }
    pub fn occ_translate(
        &self,
        entities: &[DimTag],
        dx: f64,
        dy: f64,
        dz: f64,
    ) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelOccTranslate",
            unsafe extern "C" fn(*const c_int, usize, f64, f64, f64, *mut c_int),
            entities.as_ptr(),
            entities.len(),
            dx,
            dy,
            dz,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn occ_rotate(
        &self,
        entities: &[DimTag],
        x: f64,
        y: f64,
        z: f64,
        ax: f64,
        ay: f64,
        az: f64,
        angle: f64,
    ) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelOccRotate",
            unsafe extern "C" fn(
                *const c_int,
                usize,
                f64,
                f64,
                f64,
                f64,
                f64,
                f64,
                f64,
                *mut c_int,
            ),
            entities.as_ptr(),
            entities.len(),
            x,
            y,
            z,
            ax,
            ay,
            az,
            angle,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn occ_dilate(
        &self,
        entities: &[DimTag],
        x: f64,
        y: f64,
        z: f64,
        fx: f64,
        fy: f64,
        fz: f64,
    ) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelOccDilate",
            unsafe extern "C" fn(*const c_int, usize, f64, f64, f64, f64, f64, f64, *mut c_int),
            entities.as_ptr(),
            entities.len(),
            x,
            y,
            z,
            fx,
            fy,
            fz,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn occ_mass(&self, dim: i32, tag: i32) -> Result<f64, String> {
        let mut mass = 0.0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelOccGetMass",
            unsafe extern "C" fn(c_int, c_int, *mut f64, *mut c_int),
            dim,
            tag,
            &mut mass,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(mass)
    }
    pub fn mesh_generate(&self, dim: i32) -> Result<(), String> {
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshGenerate",
            unsafe extern "C" fn(c_int, *mut c_int),
            dim,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_set_order(&self, order: i32) -> Result<(), String> {
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshSetOrder",
            unsafe extern "C" fn(c_int, *mut c_int),
            order,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_optimize(&self, method: &str) -> Result<(), String> {
        let method = text(method)?;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshOptimize",
            unsafe extern "C" fn(*const c_char, c_int, c_int, *const c_int, usize, *mut c_int),
            method.as_ptr(),
            0,
            1,
            ptr::null(),
            0,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn logger_start(&self) -> Result<(),String> {
        let mut ierr=0;
        invoke!(self,"gmshLoggerStart",unsafe extern "C" fn(*mut c_int),&mut ierr);
        self.check(ierr)
    }
    pub fn logger_messages(&self) -> Result<Vec<String>,String> {
        let mut messages=ptr::null_mut();let mut count=0;let mut ierr=0;
        invoke!(self,"gmshLoggerGet",
            unsafe extern "C" fn(*mut *mut *mut c_char,*mut usize,*mut c_int),
            &mut messages,&mut count,&mut ierr);
        let pointers=self.take(messages,count)?;
        let result=pointers.into_iter().map(|pointer|self.take_text(pointer)).collect();
        self.check(ierr)?;
        Ok(result)
    }
    pub fn logger_stop(&self) -> Result<(),String> {
        let mut ierr=0;
        invoke!(self,"gmshLoggerStop",unsafe extern "C" fn(*mut c_int),&mut ierr);
        self.check(ierr)
    }
    pub fn mesh_clear(&self, entities: &[DimTag]) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshClear",
            unsafe extern "C" fn(*const c_int, usize, *mut c_int),
            entities.as_ptr(),
            entities.len(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_set_size(&self, entities: &[DimTag], size: f64) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshSetSize",
            unsafe extern "C" fn(*const c_int, usize, f64, *mut c_int),
            entities.as_ptr(),
            entities.len(),
            size,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_set_periodic(
        &self,
        dim: i32,
        slaves: &[i32],
        masters: &[i32],
        affine: &[f64],
    ) -> Result<(), String> {
        if slaves.len() != masters.len() || affine.len() != 16 {
            return Err("Correspondance périodique : listes et matrice 4×4 attendues".into());
        }
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshSetPeriodic",
            unsafe extern "C" fn(
                c_int,
                *const c_int,
                usize,
                *const c_int,
                usize,
                *const f64,
                usize,
                *mut c_int,
            ),
            dim,
            slaves.as_ptr(),
            slaves.len(),
            masters.as_ptr(),
            masters.len(),
            affine.as_ptr(),
            affine.len(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_get_periodic_nodes(&self, dim: i32, tag: i32) -> Result<PeriodicNodes, String> {
        let mut master = 0;
        let mut nodes = ptr::null_mut();
        let mut node_count = 0;
        let mut masters = ptr::null_mut();
        let mut master_count = 0;
        let mut affine = ptr::null_mut();
        let mut affine_count = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshGetPeriodicNodes",
            unsafe extern "C" fn(
                c_int,
                c_int,
                *mut c_int,
                *mut *mut usize,
                *mut usize,
                *mut *mut usize,
                *mut usize,
                *mut *mut f64,
                *mut usize,
                c_int,
                *mut c_int,
            ),
            dim,
            tag,
            &mut master,
            &mut nodes,
            &mut node_count,
            &mut masters,
            &mut master_count,
            &mut affine,
            &mut affine_count,
            1,
            &mut ierr
        );
        let nodes = self.take(nodes, node_count);
        let masters = self.take(masters, master_count);
        let affine = self.take(affine, affine_count);
        self.check(ierr)?;
        Ok((master, nodes?, masters?, affine?))
    }
    pub fn mesh_field_add(&self, kind: &str) -> Result<i32, String> {
        let kind = text(kind)?;
        let mut ierr = 0;
        let tag = invoke!(
            self,
            "gmshModelMeshFieldAdd",
            unsafe extern "C" fn(*const c_char, c_int, *mut c_int) -> c_int,
            kind.as_ptr(),
            -1,
            &mut ierr
        );
        self.check(ierr)?;
        Ok(tag)
    }
    pub fn mesh_field_set_number(&self, tag: i32, option: &str, value: f64) -> Result<(), String> {
        let option = text(option)?;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshFieldSetNumber",
            unsafe extern "C" fn(c_int, *const c_char, f64, *mut c_int),
            tag,
            option.as_ptr(),
            value,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_field_set_numbers(
        &self,
        tag: i32,
        option: &str,
        values: &[f64],
    ) -> Result<(), String> {
        let option = text(option)?;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshFieldSetNumbers",
            unsafe extern "C" fn(c_int, *const c_char, *const f64, usize, *mut c_int),
            tag,
            option.as_ptr(),
            values.as_ptr(),
            values.len(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_field_set_string(&self, tag: i32, option: &str, value: &str) -> Result<(), String> {
        let option = text(option)?;
        let value = text(value)?;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshFieldSetString",
            unsafe extern "C" fn(c_int, *const c_char, *const c_char, *mut c_int),
            tag,
            option.as_ptr(),
            value.as_ptr(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_field_background(&self, tag: i32) -> Result<(), String> {
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshFieldSetAsBackgroundMesh",
            unsafe extern "C" fn(c_int, *mut c_int),
            tag,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn nodes(&self) -> Result<(Vec<usize>, Vec<[f64; 3]>), String> {
        self.nodes_for(-1, -1, false)
    }
    pub fn nodes_for(&self, dim: i32, entity: i32, include_boundary: bool) -> Result<(Vec<usize>, Vec<[f64; 3]>), String> {
        let mut tags = ptr::null_mut();
        let mut tag_count = 0;
        let mut coords = ptr::null_mut();
        let mut coord_count = 0;
        let mut parametric = ptr::null_mut();
        let mut parametric_count = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshGetNodes",
            unsafe extern "C" fn(
                *mut *mut usize,
                *mut usize,
                *mut *mut f64,
                *mut usize,
                *mut *mut f64,
                *mut usize,
                c_int,
                c_int,
                c_int,
                c_int,
                *mut c_int,
            ),
            &mut tags,
            &mut tag_count,
            &mut coords,
            &mut coord_count,
            &mut parametric,
            &mut parametric_count,
            dim,
            entity,
            include_boundary as i32,
            0,
            &mut ierr
        );
        let tags = self.take(tags, tag_count);
        let coords = self.take(coords, coord_count);
        let parametric = self.take(parametric, parametric_count);
        self.check(ierr)?;
        parametric?;
        let tags = tags?;
        let coords = coords?;
        if coords.len() != tags.len() * 3 {
            return Err("Coordonnées des nœuds Gmsh incohérentes".into());
        }
        Ok((
            tags,
            coords.chunks_exact(3).map(|p| [p[0], p[1], p[2]]).collect(),
        ))
    }
    pub fn set_node(&self, tag: usize, coords: [f64; 3]) -> Result<(), String> {
        if coords.iter().any(|value| !value.is_finite()) {
            return Err("Coordonnées non finies".into());
        }
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshSetNode",
            unsafe extern "C" fn(usize, *const f64, usize, *const f64, usize, *mut c_int),
            tag,
            coords.as_ptr(),
            coords.len(),
            ptr::null(),
            0,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn elements(&self, dim: i32, tag: i32) -> Result<Vec<ElementBlock>, String> {
        let mut types = ptr::null_mut();
        let mut type_count = 0;
        let mut tags = ptr::null_mut();
        let mut tag_lengths = ptr::null_mut();
        let mut tag_count = 0;
        let mut nodes = ptr::null_mut();
        let mut node_lengths = ptr::null_mut();
        let mut node_count = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshGetElements",
            unsafe extern "C" fn(
                *mut *mut c_int,
                *mut usize,
                *mut *mut *mut usize,
                *mut *mut usize,
                *mut usize,
                *mut *mut *mut usize,
                *mut *mut usize,
                *mut usize,
                c_int,
                c_int,
                *mut c_int,
            ),
            &mut types,
            &mut type_count,
            &mut tags,
            &mut tag_lengths,
            &mut tag_count,
            &mut nodes,
            &mut node_lengths,
            &mut node_count,
            dim,
            tag,
            &mut ierr
        );
        let types = self.take(types, type_count);
        let tags = self.take_nested(tags, tag_lengths, tag_count);
        let nodes = self.take_nested(nodes, node_lengths, node_count);
        self.check(ierr)?;
        let types = types?;
        let tags = tags?;
        let nodes = nodes?;
        if types.len() != tags.len() || types.len() != nodes.len() {
            return Err("Blocs d'éléments Gmsh incohérents".into());
        }
        let mut result = Vec::with_capacity(types.len());
        for ((element_type, tags), nodes) in types.into_iter().zip(tags).zip(nodes) {
            let mut name = ptr::null_mut();
            let mut dim = 0;
            let mut order = 0;
            let mut num_nodes = 0;
            let mut num_primary = 0;
            let mut local_coords = ptr::null_mut();
            let mut coord_count = 0;
            let mut ierr = 0;
            invoke!(
                self,
                "gmshModelMeshGetElementProperties",
                unsafe extern "C" fn(
                    c_int,
                    *mut *mut c_char,
                    *mut c_int,
                    *mut c_int,
                    *mut c_int,
                    *mut *mut f64,
                    *mut usize,
                    *mut c_int,
                    *mut c_int,
                ),
                element_type,
                &mut name,
                &mut dim,
                &mut order,
                &mut num_nodes,
                &mut local_coords,
                &mut coord_count,
                &mut num_primary,
                &mut ierr
            );
            let name = self.take_text(name);
            let local_coords = self.take(local_coords, coord_count);
            self.check(ierr)?;
            local_coords?;
            if num_nodes <= 0 || num_primary <= 0 || nodes.len() != tags.len() * num_nodes as usize
            {
                return Err("Connectivité Gmsh incohérente".into());
            }
            result.push(ElementBlock {
                element_type,
                tags,
                nodes,
                name,
                dim,
                order,
                num_nodes: num_nodes as usize,
                num_primary: num_primary as usize,
            });
        }
        Ok(result)
    }
    pub fn element_qualities(&self, tags: &[usize], metric: &str) -> Result<Vec<f64>, String> {
        if tags.is_empty() {
            return Ok(Vec::new());
        }
        let metric = text(metric)?;
        let mut values = ptr::null_mut();
        let mut count = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshGetElementQualities",
            unsafe extern "C" fn(
                *const usize,
                usize,
                *mut *mut f64,
                *mut usize,
                *const c_char,
                usize,
                usize,
                *mut c_int,
            ),
            tags.as_ptr(),
            tags.len(),
            &mut values,
            &mut count,
            metric.as_ptr(),
            0,
            1,
            &mut ierr
        );
        let values = self.take(values, count);
        self.check(ierr)?;
        let values = values?;
        if values.len() != tags.len() {
            return Err("Nombre de qualités Gmsh incohérent".into());
        }
        Ok(values)
    }
    pub fn mesh_classify_surfaces(
        &self,
        angle: f64,
        boundary: bool,
        for_parametrization: bool,
        curve_angle: f64,
    ) -> Result<(), String> {
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshClassifySurfaces",
            unsafe extern "C" fn(f64, c_int, c_int, f64, c_int, *mut c_int),
            angle,
            boundary as i32,
            for_parametrization as i32,
            curve_angle,
            1,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_create_geometry(&self, entities: &[DimTag]) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshCreateGeometry",
            unsafe extern "C" fn(*const c_int, usize, *mut c_int),
            entities.as_ptr(),
            entities.len(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_remove_duplicate_nodes(&self, entities: &[DimTag]) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshRemoveDuplicateNodes",
            unsafe extern "C" fn(*const c_int, usize, *mut c_int),
            entities.as_ptr(),
            entities.len(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn mesh_affine_transform(&self, affine: &[f64], entities: &[DimTag]) -> Result<(), String> {
        if affine.len() != 16 && affine.len() != 12 {
            return Err("Matrice affine de 12 ou 16 valeurs attendue".into());
        }
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelMeshAffineTransform",
            unsafe extern "C" fn(*const f64, usize, *const c_int, usize, *mut c_int),
            affine.as_ptr(),
            affine.len(),
            entities.as_ptr(),
            entities.len(),
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn geo_dilate(
        &self,
        entities: &[DimTag],
        x: f64,
        y: f64,
        z: f64,
        fx: f64,
        fy: f64,
        fz: f64,
    ) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelGeoDilate",
            unsafe extern "C" fn(*const c_int, usize, f64, f64, f64, f64, f64, f64, *mut c_int),
            entities.as_ptr(),
            entities.len(),
            x,
            y,
            z,
            fx,
            fy,
            fz,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn geo_translate(
        &self,
        entities: &[DimTag],
        dx: f64,
        dy: f64,
        dz: f64,
    ) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelGeoTranslate",
            unsafe extern "C" fn(*const c_int, usize, f64, f64, f64, *mut c_int),
            entities.as_ptr(),
            entities.len(),
            dx,
            dy,
            dz,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn geo_rotate(
        &self,
        entities: &[DimTag],
        x: f64,
        y: f64,
        z: f64,
        ax: f64,
        ay: f64,
        az: f64,
        angle: f64,
    ) -> Result<(), String> {
        let entities = flattened(entities);
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelGeoRotate",
            unsafe extern "C" fn(
                *const c_int,
                usize,
                f64,
                f64,
                f64,
                f64,
                f64,
                f64,
                f64,
                *mut c_int,
            ),
            entities.as_ptr(),
            entities.len(),
            x,
            y,
            z,
            ax,
            ay,
            az,
            angle,
            &mut ierr
        );
        self.check(ierr)
    }
    pub fn adjacencies(&self, dim: i32, tag: i32) -> Result<(Vec<i32>, Vec<i32>), String> {
        let mut upward = ptr::null_mut();
        let mut upward_len = 0;
        let mut downward = ptr::null_mut();
        let mut downward_len = 0;
        let mut ierr = 0;
        invoke!(
            self,
            "gmshModelGetAdjacencies",
            unsafe extern "C" fn(
                c_int,
                c_int,
                *mut *mut c_int,
                *mut usize,
                *mut *mut c_int,
                *mut usize,
                *mut c_int,
            ),
            dim,
            tag,
            &mut upward,
            &mut upward_len,
            &mut downward,
            &mut downward_len,
            &mut ierr
        );
        let upward = self.take(upward, upward_len);
        let downward = self.take(downward, downward_len);
        self.check(ierr)?;
        Ok((upward?, downward?))
    }
}

impl Drop for Gmsh {
    fn drop(&mut self) {
        if self.initialized {
            if let Ok(function) = unsafe {
                self.lib
                    .get::<unsafe extern "C" fn(*mut c_int)>(b"gmshFinalize\0")
            } {
                let mut ierr = 0;
                unsafe {
                    function(&mut ierr);
                }
            }
            self.initialized = false;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pairs_are_checked() {
        assert_eq!(paired(vec![2, 7, 3, 9]).unwrap(), vec![(2, 7), (3, 9)]);
        assert!(paired(vec![2]).is_err());
    }
    #[test]
    fn nul_strings_are_rejected() {
        assert!(text("file\0.step").is_err());
    }
}
