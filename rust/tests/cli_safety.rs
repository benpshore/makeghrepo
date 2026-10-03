use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

struct TempDir(PathBuf);

impl TempDir {
    fn new() -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "makeghrepo-cli-safety-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn binary() -> Command {
    Command::new(env!("CARGO_BIN_EXE_makeghrepo"))
}

fn output_text(output: &Output) -> String {
    format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
}

#[test]
fn help_lists_every_registry_language() {
    let output = binary().arg("--help").env("PATH", "").output().unwrap();
    assert!(output.status.success(), "{}", output_text(&output));
    let text = output_text(&output);
    let registry = Path::new(env!("CARGO_MANIFEST_DIR")).join("../src/makeghrepo/langs");
    for entry in fs::read_dir(registry).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().is_some_and(|ext| ext == "toml") {
            assert!(text.contains(path.file_stem().unwrap().to_str().unwrap()));
        }
    }
}

#[test]
fn offline_render_normalizes_names_and_keeps_files_inside_destination() {
    let temp = TempDir::new();
    for (raw, normalized) in [("My Repo", "my_repo"), ("../../escaped", "escaped")] {
        let dest = temp.0.join(normalized);
        let output = binary()
            .args([raw, "python", "--render"])
            .arg(&dest)
            .env("PATH", "")
            .output()
            .unwrap();
        assert!(output.status.success(), "{}", output_text(&output));
        assert!(dest.join(format!("src/{normalized}/__init__.py")).is_file());
    }
    assert!(!temp.0.join("escaped/__init__.py").exists());
}

#[test]
fn invalid_offline_inputs_write_nothing() {
    let temp = TempDir::new();
    for args in [
        vec!["..", "python"],
        vec!["7up", "rust"],
        vec!["sample", "python", "--owner", "../other"],
    ] {
        let dest = temp.0.join("output");
        let output = binary()
            .args(args)
            .arg("--render")
            .arg(&dest)
            .env("PATH", "")
            .output()
            .unwrap();
        assert!(!output.status.success(), "{}", output_text(&output));
        assert!(!dest.exists());
    }
}

fn script(path: &Path, source: &str) {
    fs::write(path, source).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
}

#[test]
fn failed_remote_lookup_stops_before_configuration_or_push() {
    let temp = TempDir::new();
    let bin = temp.0.join("bin");
    let dest = temp.0.join("repos/sample");
    let log = temp.0.join("calls.log");
    fs::create_dir(&bin).unwrap();
    fs::create_dir_all(dest.join(".git")).unwrap();
    fs::write(
        dest.join(".git/makeghrepo.json"),
        r#"{"schema":1,"owner":"me","name":"sample","private":true,"languages":[]}"#,
    )
    .unwrap();
    // These executables deliberately provide no real GitHub or git access.
    // Fail only discovery: the former implementation would proceed to push.
    script(
        &bin.join("gh"),
        r#"#!/bin/sh
printf 'gh %s\n' "$*" >> "$SAFETY_CALL_LOG"
case "$*" in
  'api user --jq .login') printf 'me\n';;
  'repo view me/sample --json name') printf '{"name":"sample"}\n';;
  'api -X GET repos/me/sample') printf '{"private":true}\n';;
  *) printf '{}\n';;
esac
"#,
    );
    script(
        &bin.join("git"),
        r#"#!/bin/sh
shift 2
printf 'git %s\n' "$*" >> "$SAFETY_CALL_LOG"
case "$*" in
  'remote') printf 'origin\n';;
  'remote get-url --all origin'|'remote get-url --push --all origin')
    printf 'https://github.com/me/sample.git\n';;
  'ls-remote --heads origin main') printf 'temporary connection failure\n' >&2; exit 128;;
esac
"#,
    );
    let output = binary()
        .arg("sample")
        .env("PATH", &bin)
        .env("MAKEGHREPO_DIR", temp.0.join("repos"))
        .env("SAFETY_CALL_LOG", &log)
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1), "{}", output_text(&output));
    assert!(output_text(&output).contains("Could not check remote main; refusing to push"));
    let calls = fs::read_to_string(log).unwrap();
    assert!(calls.contains("git ls-remote --heads origin main"));
    assert!(!calls.contains("git push"));
    assert!(!calls.contains("gh api -X PATCH"));
    assert!(!calls.contains("gh api -X PUT"));
    assert!(!calls.contains("gh repo create"));
}
