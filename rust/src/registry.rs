//! The language registry: the same `langs/*.toml` files the Python tool reads,
//! embedded at build time. See `src/makeghrepo/registry.py` for field meanings.

use include_dir::{Dir, include_dir};
use serde::Deserialize;
use serde_json::{Map, Value, json};

static LANGS_DIR: Dir = include_dir!("$CARGO_MANIFEST_DIR/../src/makeghrepo/langs");

pub const SCHEMA: u64 = 1;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)]
pub struct Lang {
    pub schema: u64,
    pub id: String,
    pub order: u64,
    pub aliases: Vec<String>,
    pub kind: String,
    pub flag: String,
    pub groups: Vec<String>,
    pub marker: String,
    pub checks: Vec<Vec<String>>,
    pub setup_len: usize,
    pub required_tool: bool,
    pub ci_job: String,
    pub dependabot: Map<String, Value>,
    pub codeql: Map<String, Value>,
    pub docker_priority: u64,
    pub requires: Vec<String>,
    pub host_os: Vec<String>,
    pub probe: Vec<String>,
    pub leading_letter: bool,
    pub apps: Vec<String>,
    pub lockfile: String,
    pub release: String,
    pub npm_scripts: Map<String, Value>,
    pub npm_dev: Map<String, Value>,
}

/// Every registry entry, ordered by `order`. Panics on a malformed registry:
/// that is a build defect, not a runtime condition.
pub fn load() -> Vec<Lang> {
    let mut langs: Vec<Lang> = Vec::new();
    for file in LANGS_DIR.files() {
        let name = file.path().to_string_lossy().into_owned();
        if !name.ends_with(".toml") {
            continue;
        }
        let text = file.contents_utf8().unwrap_or_else(|| panic!("{name}: not UTF-8"));
        let lang: Lang = toml::from_str(text).unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(lang.schema, SCHEMA, "{name}: schema {} (expected {SCHEMA})", lang.schema);
        assert_eq!(format!("{}.toml", lang.id), name, "{name}: id doesn't match the file name");
        assert!(lang.setup_len <= lang.checks.len(), "{name}: setup_len out of range");
        assert!(!lang.required_tool || !lang.checks.is_empty(), "{name}: required_tool without checks");
        langs.push(lang);
    }
    langs.sort_by_key(|l| l.order);
    let mut words: Vec<&str> = Vec::new();
    for lang in &langs {
        for word in std::iter::once(&lang.id).chain(lang.aliases.iter()) {
            assert!(!words.contains(&word.as_str()), "{}.toml: {word:?} is already an id or alias", lang.id);
            words.push(word);
        }
    }
    let orders: std::collections::BTreeSet<u64> = langs.iter().map(|l| l.order).collect();
    assert_eq!(orders.len(), langs.len(), "two registry entries share an order");
    langs
}

/// The registry id for a command-line word (an id or alias), if any.
pub fn language<'a>(langs: &'a [Lang], word: &str) -> Option<&'a str> {
    let word = word.to_lowercase();
    langs
        .iter()
        .find(|l| l.id == word || l.aliases.iter().any(|a| *a == word))
        .map(|l| l.id.as_str())
}

pub fn ids(langs: &[Lang]) -> Vec<String> {
    langs.iter().map(|l| l.id.clone()).collect()
}

pub fn get<'a>(langs: &'a [Lang], id: &str) -> &'a Lang {
    langs.iter().find(|l| l.id == id).expect("known language id")
}

/// Template data for the chosen languages, in registry order, with shared entries once
/// (mirrors `registry.derived`).
pub fn derived(langs: &[Lang], chosen: &[String]) -> Map<String, Value> {
    let mut ci_jobs: Vec<String> = Vec::new();
    let mut ecosystems: Vec<(String, i64)> = Vec::new();
    let mut codeql: Vec<(String, Map<String, Value>)> = Vec::new();
    let mut npm_scripts = Map::new();
    let mut npm_dev = Map::new();
    for lang in langs {
        if !chosen.iter().any(|c| *c == lang.id) {
            continue;
        }
        if !lang.ci_job.is_empty() && !ci_jobs.contains(&lang.ci_job) {
            ci_jobs.push(lang.ci_job.clone());
        }
        if !lang.dependabot.is_empty() {
            let eco = lang.dependabot["ecosystem"].as_str().unwrap_or_default().to_string();
            let order = lang.dependabot["order"].as_i64().unwrap_or_default();
            if let Some(entry) = ecosystems.iter_mut().find(|(e, _)| *e == eco) {
                entry.1 = order;
            } else {
                ecosystems.push((eco, order));
            }
        }
        if !lang.codeql.is_empty() {
            let language = lang.codeql["language"].as_str().unwrap_or_default().to_string();
            if let Some(entry) = codeql.iter_mut().find(|(l, _)| *l == language) {
                entry.1 = lang.codeql.clone();
            } else {
                codeql.push((language, lang.codeql.clone()));
            }
        }
        for (k, v) in &lang.npm_scripts {
            npm_scripts.insert(k.clone(), v.clone());
        }
        for (k, v) in &lang.npm_dev {
            npm_dev.insert(k.clone(), v.clone());
        }
    }
    ecosystems.sort_by_key(|(_, order)| *order);
    codeql.sort_by_key(|(_, entry)| entry["order"].as_i64().unwrap_or_default());
    let mut dependabot: Vec<Value> = vec![json!("github-actions")];
    dependabot.extend(ecosystems.into_iter().map(|(e, _)| json!(e)));
    let codeql_entries: Vec<Value> = codeql
        .into_iter()
        .map(|(_, e)| {
            let mut m = Map::new();
            for key in ["language", "build_mode", "os"] {
                m.insert(key.to_string(), e[key].clone());
            }
            Value::Object(m)
        })
        .collect();
    let mut out = Map::new();
    out.insert("ci_jobs".into(), json!(ci_jobs));
    out.insert("dependabot_ecosystems".into(), Value::Array(dependabot));
    out.insert("codeql_entries".into(), Value::Array(codeql_entries));
    out.insert("npm_scripts".into(), Value::Object(npm_scripts));
    out.insert("npm_dev".into(), Value::Object(npm_dev));
    out
}

/// The boolean flags copier.yml derives from `languages`: one per registry
/// entry (`flag`) plus one per shared group (`npm`, `cmake`).
pub fn flags(langs: &[Lang], chosen: &[String]) -> Map<String, Value> {
    let mut out = Map::new();
    let mut groups: Vec<String> = Vec::new();
    for lang in langs {
        let on = chosen.iter().any(|c| *c == lang.id);
        out.insert(lang.flag.clone(), Value::Bool(on));
        for g in &lang.groups {
            if !groups.contains(g) {
                groups.push(g.clone());
            }
        }
    }
    for g in groups {
        let on = langs
            .iter()
            .any(|l| l.groups.contains(&g) && chosen.iter().any(|c| *c == l.id));
        out.insert(g, Value::Bool(on));
    }
    out
}
