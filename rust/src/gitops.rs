//! Local git operations, by shelling out to `git` (hooks and signing are honored).

use std::collections::BTreeMap;
use std::fs;
use std::path::Path;
use std::process::Command;

use regex::Regex;
use serde_json::Value;

pub const MARKER: &str = "makeghrepo.json"; // lives inside .git, so it is never committed or pushed

/// Same pattern as the `repo` job in the generated ci.yml.
pub const JUNK_PATTERN: &str =
    r"(?i)(^|/)(\.DS_Store|\.env(\..+)?|[^/]+\.(db|sqlite3?|duckdb|pem|key|p12|pfx))$";

pub fn git(path: &Path, args: &[&str]) -> Result<String, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(path)
        .args(args)
        .output()
        .map_err(|e| format!("git: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        let msg = if err.trim().is_empty() {
            String::from_utf8_lossy(&out.stdout)
        } else {
            err
        };
        Err(format!("git {}\n{}", args.join(" "), msg.trim()))
    }
}

/// The paths CI would reject: OS junk, databases, private keys and .env files.
pub fn junk_files(names: &[String]) -> Vec<String> {
    let re = Regex::new(JUNK_PATTERN).expect("valid pattern");
    names
        .iter()
        .filter(|n| re.is_match(n) && !n.ends_with(".env.example"))
        .cloned()
        .collect()
}

pub fn write_marker(path: &Path, data: &BTreeMap<String, Value>) -> Result<(), String> {
    let text = serde_json::to_string_pretty(data).expect("serializable") + "\n";
    fs::write(path.join(".git").join(MARKER), text).map_err(|e| format!("marker: {e}"))
}

/// The marker written by `write_marker`, or None if missing, unreadable or not schema 1.
pub fn read_marker(path: &Path) -> Option<Value> {
    let text = fs::read_to_string(path.join(".git").join(MARKER)).ok()?;
    let data: Value = serde_json::from_str(&text).ok()?;
    let obj = data.as_object()?;
    if obj.get("schema")?.as_i64()? != 1 {
        return None;
    }
    obj.get("private")?.as_bool()?;
    let langs = obj.get("languages")?.as_array()?;
    if !langs.iter().all(Value::is_string) {
        return None;
    }
    Some(data)
}

/// (name, email) from the user's global git config, or empty strings.
pub fn author_from_git_config() -> (String, String) {
    let get = |key: &str| {
        Command::new("git")
            .args(["config", "--global", "--get", key])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default()
    };
    (get("user.name"), get("user.email"))
}

pub fn init(path: &Path) -> Result<(), String> {
    git(path, &["init", "-q", "-b", "main"]).map(|_| ())
}

/// Set commit identity on this repo only, if none is set at any config level.
pub fn ensure_identity(path: &Path, name: &str, email: &str) -> Result<(), String> {
    for (key, value) in [("user.name", name), ("user.email", email)] {
        if git(path, &["config", "--get", key]).is_err() {
            git(path, &["config", key, value])?;
        }
    }
    Ok(())
}

pub fn has_commits(path: &Path) -> bool {
    git(path, &["rev-parse", "--verify", "-q", "HEAD"]).is_ok()
}

/// `git add --all && git commit -m <message>`, refusing anything CI would reject.
pub fn commit_all(path: &Path, message: &str) -> Result<(), String> {
    git(path, &["add", "--all"])?;
    let staged: Vec<String> = git(path, &["diff", "--cached", "--name-only"])?
        .lines()
        .map(str::to_string)
        .collect();
    let junk = junk_files(&staged);
    if !junk.is_empty() {
        return Err(format!(
            "refusing to commit junk or secrets: {}",
            junk.join(", ")
        ));
    }
    git(path, &["commit", "-q", "-m", message]).map(|_| ())
}

pub fn push_main(path: &Path) -> Result<(), String> {
    git(path, &["push", "-q", "-u", "origin", "main"]).map(|_| ())
}

pub fn remote_has_main(path: &Path) -> bool {
    git(path, &["ls-remote", "--heads", "origin", "main"])
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn junk_matches_ci_pattern() {
        let names: Vec<String> = [
            ".env",
            "config/.env.local",
            ".env.example",
            "data.db",
            "x/.DS_Store",
            "key.pem",
            "src/main.rs",
            "notes.md",
            "SECRETS.KEY",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();
        assert_eq!(
            junk_files(&names),
            vec![
                ".env",
                "config/.env.local",
                "data.db",
                "x/.DS_Store",
                "key.pem",
                "SECRETS.KEY"
            ]
        );
    }
}
