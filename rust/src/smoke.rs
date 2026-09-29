//! Run each chosen language's lint/test/build locally (mirrors `scaffold.smoke_test`).
//! Setup commands run first and in order; the rest run concurrently, and the
//! first failure stops every other unit before its next command.

use std::collections::HashSet;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use crate::registry::Lang;

pub type Cmd = Vec<String>;
/// A unit's log lines and its error, if any.
type UnitResult = (Vec<String>, Option<String>);

/// Plugin subcommands shipped as separate, sometimes-missing binaries.
const CARGO_PLUGIN_SUBCOMMANDS: &[&str] = &["fmt", "clippy"];
const SCRUBBED_PREFIXES: &[&str] = &["GH_", "GITHUB_"];
const SCRUBBED_NAMES: &[&str] = &[
    "DBUS_SESSION_BUS_ADDRESS",
    "SSH_AUTH_SOCK",
    "GIT_ASKPASS",
    "SSH_ASKPASS",
];

/// The environment local checks run in: the user's, minus credential channels.
pub fn check_env() -> Vec<(OsString, OsString)> {
    std::env::vars_os()
        .filter(|(k, _)| {
            let key = k.to_string_lossy();
            !SCRUBBED_PREFIXES.iter().any(|p| key.starts_with(p))
                && !SCRUBBED_NAMES.contains(&key.as_ref())
        })
        .collect()
}

pub fn which(name: &str) -> Option<PathBuf> {
    use std::os::unix::fs::PermissionsExt;
    if name.contains('/') {
        let p = PathBuf::from(name);
        return p.is_file().then_some(p);
    }
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|p| {
            p.is_file()
                && p.metadata()
                    .map(|m| m.permissions().mode() & 0o111 != 0)
                    .unwrap_or(false)
        })
}

fn probe_ok(probe: &[String], env: &[(OsString, OsString)]) -> bool {
    if probe.is_empty() || which(&probe[0]).is_none() {
        return false;
    }
    let child = Command::new(&probe[0])
        .args(&probe[1..])
        .env_clear()
        .envs(env.iter().map(|(k, v)| (k, v)))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
    let Ok(mut child) = child else { return false };
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(100)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

fn host_os() -> &'static str {
    match std::env::consts::OS {
        "macos" => "Darwin",
        "linux" => "Linux",
        "windows" => "Windows",
        other => other,
    }
}

/// Chosen languages whose local checks can't run on this host, with the reason.
pub fn unavailable(
    langs: &[Lang],
    languages: &[String],
    env: &[(OsString, OsString)],
) -> Vec<(String, String)> {
    let mut reasons = Vec::new();
    for id in languages {
        let Some(lang) = langs.iter().find(|l| l.id == *id) else {
            continue;
        };
        if !lang.host_os.is_empty() && !lang.host_os.iter().any(|os| os == host_os()) {
            reasons.push((id.clone(), format!("needs {}", lang.host_os.join(" or "))));
        } else if !lang.probe.is_empty() && !probe_ok(&lang.probe, env) {
            reasons.push((id.clone(), format!("`{}` failed", lang.probe.join(" "))));
        }
    }
    reasons
}

fn run_cmd_for(cmd: &[String]) -> Cmd {
    if cmd.len() >= 2 && cmd[0] == "uv" && cmd[1] == "run" {
        let mut out = vec![cmd[0].clone(), cmd[1].clone(), "--no-sync".to_string()];
        out.extend(cmd[2..].iter().cloned());
        out
    } else {
        cmd.to_vec()
    }
}

/// The binary name `cmd` actually needs, if it's not on PATH.
fn missing_tool(cmd: &[String]) -> Option<String> {
    if which(&cmd[0]).is_none() {
        return Some(cmd[0].clone());
    }
    if cmd[0] == "cargo" && cmd.len() > 1 && CARGO_PLUGIN_SUBCOMMANDS.contains(&cmd[1].as_str()) {
        let plugin = format!("cargo-{}", cmd[1]);
        if which(&plugin).is_none() {
            return Some(plugin);
        }
    }
    None
}

struct Plan {
    skip: bool,
    lockfile_cmds: HashSet<Cmd>,
    blocked: Vec<(Cmd, String)>,
    env: Vec<(OsString, OsString)>,
    dest: PathBuf,
}

impl Plan {
    /// A skip message if cmd won't actually run, else None.
    fn announce(&self, cmd: &[String]) -> Option<String> {
        let joined = cmd.join(" ");
        if self.skip && !self.lockfile_cmds.contains(cmd) {
            return Some(format!(
                "  - skip {joined} (MAKEGHREPO_SKIP_LOCAL_CHECKS set; CI will run it)"
            ));
        }
        if let Some((_, reason)) = self.blocked.iter().find(|(c, _)| c == cmd) {
            return Some(format!("  - skip {joined} ({reason}; CI will run it)"));
        }
        if let Some(missing) = missing_tool(cmd) {
            return Some(format!(
                "  - skip {joined} ({missing} not installed; CI will run it)"
            ));
        }
        None
    }

    /// Run cmd for real (already past the skip checks).
    fn execute(&self, cmd: &[String]) -> Result<(), String> {
        let run_cmd = run_cmd_for(cmd);
        let out = Command::new(&run_cmd[0])
            .args(&run_cmd[1..])
            .current_dir(&self.dest)
            .env_clear()
            .envs(self.env.iter().map(|(k, v)| (k, v)))
            .stdin(Stdio::null())
            .output()
            .map_err(|e| format!("failed: {}\n{e}", run_cmd.join(" ")))?;
        if out.status.success() {
            Ok(())
        } else {
            Err(format!(
                "failed: {}\n{}{}",
                run_cmd.join(" "),
                String::from_utf8_lossy(&out.stdout),
                String::from_utf8_lossy(&out.stderr)
            ))
        }
    }
}

pub fn smoke_test(
    dest: &Path,
    langs: &[Lang],
    languages: &[String],
    log: &(dyn Fn(&str) + Sync),
) -> Result<(), String> {
    let skip = std::env::var_os("MAKEGHREPO_SKIP_LOCAL_CHECKS").is_some_and(|v| !v.is_empty());
    let required: Vec<&Lang> = langs.iter().filter(|l| l.required_tool).collect();
    for lang in &required {
        if languages.contains(&lang.id) && which(&lang.checks[0][0]).is_none() {
            return Err(format!(
                "{} needs {} installed locally to create its lockfile",
                lang.id, lang.checks[0][0]
            ));
        }
    }
    let lockfile_cmds: HashSet<Cmd> = required.iter().map(|l| l.checks[0].clone()).collect();
    let env = check_env();
    // One CMake project serves c/cpp/objc/objcpp, so an unrunnable objc blocks all of it.
    let mut blocked: Vec<(Cmd, String)> = Vec::new();
    // Probes (notably `docker info`) are checks too. Required lockfile
    // commands still run when checks are skipped; daemon probes need not.
    for (id, reason) in if skip {
        Vec::new()
    } else {
        unavailable(langs, languages, &env)
    } {
        let lang = langs.iter().find(|l| l.id == id).expect("known");
        for cmd in &lang.checks {
            blocked.push((cmd.clone(), format!("{id} {reason}")));
        }
    }

    let mut seen: HashSet<Cmd> = HashSet::new();
    let mut setup: Vec<Cmd> = Vec::new();
    let mut units: Vec<Vec<Cmd>> = Vec::new(); // each is one sequential unit to pool
    for id in languages {
        let lang = langs.iter().find(|l| l.id == *id).expect("known");
        let mut cmds: Vec<Cmd> = lang.checks.clone();
        if id == "sql" && languages.iter().any(|l| l == "postgres") {
            for c in &mut cmds {
                if c.len() > 1 && c[1].starts_with("sqlfluff") {
                    c.push("db".to_string());
                }
            }
        }
        if lang.setup_len == 0 {
            let whole: Vec<Cmd> = cmds
                .into_iter()
                .filter(|c| seen.insert(c.clone()))
                .collect();
            if !whole.is_empty() {
                units.push(whole);
            }
            continue;
        }
        for (i, cmd) in cmds.into_iter().enumerate() {
            if !seen.insert(cmd.clone()) {
                continue;
            }
            if i < lang.setup_len {
                setup.push(cmd);
            } else {
                units.push(vec![cmd]);
            }
        }
    }

    let plan = Plan {
        skip,
        lockfile_cmds,
        blocked,
        env,
        dest: dest.to_path_buf(),
    };
    for cmd in &setup {
        if let Some(msg) = plan.announce(cmd) {
            log(&msg);
            continue;
        }
        log(&format!("  $ {}", run_cmd_for(cmd).join(" ")));
        plan.execute(cmd)?;
    }
    if units.is_empty() {
        return Ok(());
    }

    let stop = AtomicBool::new(false);
    let next = AtomicUsize::new(0);
    let results: Vec<Mutex<Option<UnitResult>>> = units.iter().map(|_| Mutex::new(None)).collect();
    let run_unit = |unit: &[Cmd]| -> UnitResult {
        let mut lines = Vec::new();
        for cmd in unit {
            if stop.load(Ordering::SeqCst) {
                lines.push(format!(
                    "  - not run: {} (another check failed)",
                    cmd.join(" ")
                ));
                break;
            }
            if let Some(msg) = plan.announce(cmd) {
                lines.push(msg);
                continue;
            }
            lines.push(format!("  $ {}", run_cmd_for(cmd).join(" ")));
            if let Err(e) = plan.execute(cmd) {
                stop.store(true, Ordering::SeqCst);
                return (lines, Some(e));
            }
        }
        (lines, None)
    };
    let workers = units.len().min(4);
    std::thread::scope(|s| {
        for _ in 0..workers {
            s.spawn(|| {
                loop {
                    let i = next.fetch_add(1, Ordering::SeqCst);
                    if i >= units.len() {
                        break;
                    }
                    let result = run_unit(&units[i]);
                    *results[i].lock().expect("lock") = Some(result);
                }
            });
        }
    });
    // Report in declared order, not completion order, so output is deterministic.
    for slot in results {
        let (lines, error) = slot.into_inner().expect("lock").unwrap_or_default();
        for line in lines {
            log(&line);
        }
        if let Some(e) = error {
            return Err(e);
        }
    }
    Ok(())
}
