//! Render the embedded copier template with minijinja, and serialize a rendered
//! tree in the golden-snapshot format (`tests/golden_snapshots.py`).

use std::collections::BTreeMap;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Component, Path, PathBuf};

use base64::Engine;
use include_dir::{Dir, File, include_dir};
use minijinja::syntax::SyntaxConfig;
use minijinja::value::Kwargs;
use minijinja::{AutoEscape, Environment, Error, ErrorKind, Value};
use serde_json::{Map, Value as Json};

use crate::names;
use crate::registry::{self, Lang};

static TEMPLATE_DIR: Dir = include_dir!("$CARGO_MANIFEST_DIR/../src/makeghrepo/templates/project");

const SUBDIR: &str = "template";
const SUFFIX: &str = ".jinja";

fn walk<'a>(dir: &'a Dir<'a>, out: &mut Vec<&'a File<'a>>) {
    for f in dir.files() {
        out.push(f);
    }
    for d in dir.dirs() {
        walk(d, out);
    }
}

fn to_json_value(value: &Value) -> Result<Json, Error> {
    serde_json::to_value(value)
        .map_err(|e| Error::new(ErrorKind::InvalidOperation, format!("to_json: {e}")))
}

/// copier's `to_json` (`json.dumps(value, ensure_ascii=False)`).
fn to_json(value: Value, _kwargs: Kwargs) -> Result<Value, Error> {
    let json = to_json_value(&value)?;
    Ok(Value::from(
        serde_json::to_string(&json).expect("serializable"),
    ))
}

/// copier's `to_nice_json` (`json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)`).
fn to_nice_json(value: Value, _kwargs: Kwargs) -> Result<Value, Error> {
    let json = to_json_value(&value)?;
    Ok(Value::from(
        serde_json::to_string_pretty(&json).expect("serializable"),
    ))
}

fn environment() -> Environment<'static> {
    let mut env = Environment::new();
    env.set_syntax(
        SyntaxConfig::builder()
            .block_delimiters("[%", "%]")
            .variable_delimiters("[[", "]]")
            .comment_delimiters("[#", "#]")
            .build()
            .expect("valid delimiters"),
    );
    env.set_trim_blocks(true);
    env.set_lstrip_blocks(true);
    env.set_keep_trailing_newline(true);
    env.set_auto_escape_callback(|_| AutoEscape::None);
    env.add_filter("to_json", to_json);
    env.add_filter("to_nice_json", to_nice_json);
    let mut files = Vec::new();
    walk(&TEMPLATE_DIR, &mut files);
    for f in files {
        let name = f.path().to_str().expect("utf-8 template path");
        if let Some(src) = f.contents_utf8() {
            if name.ends_with(SUFFIX) {
                env.add_template(name, src)
                    .unwrap_or_else(|e| panic!("{name}: {e}"));
            }
        }
    }
    env
}

/// Everything the template needs, mirroring what `scaffold.render` passes copier.
pub struct Data {
    pub project_name: String,
    pub package_name: String,
    pub description: String,
    pub author_name: String,
    pub github_owner: String,
    pub year: String,
    pub private: bool,
    pub py_lib: bool,
    pub project_license: String,
    pub languages: Vec<String>,
}

fn context(langs: &[Lang], data: &Data) -> Value {
    let mut ctx: Map<String, Json> = Map::new();
    ctx.insert("year".into(), Json::String(data.year.clone()));
    for (k, v) in registry::derived(langs, &data.languages) {
        ctx.insert(k, v);
    }
    ctx.insert(
        "project_name".into(),
        Json::String(data.project_name.clone()),
    );
    ctx.insert(
        "package_name".into(),
        Json::String(data.package_name.clone()),
    );
    ctx.insert("description".into(), Json::String(data.description.clone()));
    ctx.insert("author_name".into(), Json::String(data.author_name.clone()));
    ctx.insert(
        "github_owner".into(),
        Json::String(data.github_owner.clone()),
    );
    ctx.insert("private".into(), Json::Bool(data.private));
    ctx.insert("py_lib".into(), Json::Bool(data.py_lib));
    ctx.insert(
        "project_license".into(),
        Json::String(data.project_license.clone()),
    );
    ctx.insert(
        "languages".into(),
        serde_json::to_value(&data.languages).expect("strings"),
    );
    for (k, v) in registry::flags(langs, &data.languages) {
        ctx.insert(k, v);
    }
    Value::from_serialize(&ctx)
}

/// Render the template into `dest` (which must not exist or must be empty).
pub fn render(dest: &Path, langs: &[Lang], data: &Data) -> Result<(), String> {
    names::validate_name(&data.project_name)?;
    names::validate_owner(&data.github_owner)?;
    if data.package_name != names::package_name(&data.project_name) {
        return Err("package name does not match the validated project name".into());
    }
    if !matches!(data.project_license.as_str(), "none" | "MIT") {
        return Err("project license must be none or MIT".into());
    }
    if dest.exists()
        && fs::read_dir(dest)
            .map_err(|e| e.to_string())?
            .next()
            .is_some()
    {
        return Err(format!(
            "{} already exists and is not empty",
            dest.display()
        ));
    }
    let env = environment();
    let ctx = context(langs, data);
    let mut files = Vec::new();
    walk(&TEMPLATE_DIR, &mut files);
    for f in files {
        let raw = f.path().to_str().expect("utf-8 template path");
        let Some(rel) = raw.strip_prefix(&format!("{SUBDIR}/")) else {
            continue;
        };
        let is_template = rel.ends_with(SUFFIX);
        let rel = rel.strip_suffix(SUFFIX).unwrap_or(rel);
        let mut out = PathBuf::from(dest);
        let mut skip = false;
        for part in rel.split('/') {
            let rendered = env
                .render_str(part, &ctx)
                .map_err(|e| format!("{raw}: {e}"))?;
            if rendered.is_empty() {
                skip = true;
                break;
            }
            if rendered.contains(['/', '\\'])
                || !matches!(
                    Path::new(&rendered).components().next(),
                    Some(Component::Normal(_))
                )
            {
                return Err(format!("{raw}: unsafe rendered path component"));
            }
            out.push(rendered);
        }
        if skip {
            continue;
        }
        if let Some(parent) = out.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
        }
        let bytes: Vec<u8> = if is_template {
            env.get_template(raw)
                .and_then(|t| t.render(&ctx))
                .map_err(|e| format!("{raw}: {e}"))?
                .into_bytes()
        } else {
            f.contents().to_vec()
        };
        fs::write(&out, bytes).map_err(|e| format!("{}: {e}", out.display()))?;
        // include_dir keeps no modes; scripts are the only executables in the template.
        let mode = if rel.ends_with(".sh") { 0o755 } else { 0o644 };
        fs::set_permissions(&out, fs::Permissions::from_mode(mode))
            .map_err(|e| format!("{}: {e}", out.display()))?;
    }
    Ok(())
}

fn collect(root: &Path, dir: &Path, out: &mut BTreeMap<String, PathBuf>) -> Result<(), String> {
    for entry in fs::read_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        let meta = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
        let rel = path
            .strip_prefix(root)
            .map_err(|e| e.to_string())?
            .to_string_lossy()
            .replace('\\', "/");
        if meta.file_type().is_symlink() {
            return Err(format!("symlink in rendered output: {rel}"));
        }
        if meta.is_dir() {
            collect(root, &path, out)?;
        } else {
            out.insert(rel, path);
        }
    }
    Ok(())
}

/// One deterministic text document describing every file under `root`
/// (byte-identical to `golden_snapshots.serialize`).
pub fn serialize(root: &Path) -> Result<String, String> {
    let mut files = BTreeMap::new();
    collect(root, root, &mut files)?;
    let mut out = String::new();
    for (rel, path) in files {
        let data = fs::read(&path).map_err(|e| format!("{rel}: {e}"))?;
        let mode = fs::metadata(&path)
            .map_err(|e| e.to_string())?
            .permissions()
            .mode();
        let executable = u8::from(mode & 0o100 != 0);
        let (text, encoding) = match std::str::from_utf8(&data) {
            Ok(s) => (s.to_string(), "utf-8"),
            Err(_) => (
                base64::engine::general_purpose::STANDARD.encode(&data),
                "base64",
            ),
        };
        out.push_str(&format!(
            "### FILE {rel} exec={executable} bytes={} encoding={encoding}\n{text}\n### END\n",
            data.len()
        ));
    }
    Ok(out)
}
