//! GitHub setup via the `gh` CLI (which owns auth; we never touch tokens).
//! Every step is idempotent, so a re-run after a partial failure is safe.

use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::Mutex;

use serde_json::{Value, json};

pub const RULESET_NAME: &str = "protect-main";
pub const REQUIRED_CHECK: &str = "ci";
pub const GITHUB_ACTIONS_APP_ID: u64 = 15368;
pub const LABELS: &[(&str, &str)] = &[("epic", "3E4B9E"), ("task", "C5DEF5")];

pub fn gh(args: &[&str], body: Option<&Value>) -> Result<String, String> {
    let mut cmd = Command::new("gh");
    cmd.args(args);
    if body.is_some() {
        cmd.args(["--input", "-"]);
    }
    cmd.stdin(if body.is_some() {
        Stdio::piped()
    } else {
        Stdio::null()
    });
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| format!("gh: {e}"))?;
    if let Some(body) = body {
        let mut stdin = child.stdin.take().expect("piped stdin");
        stdin
            .write_all(body.to_string().as_bytes())
            .map_err(|e| format!("gh stdin: {e}"))?;
    }
    let out = child.wait_with_output().map_err(|e| format!("gh: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        let err = String::from_utf8_lossy(&out.stderr);
        let msg = if err.trim().is_empty() {
            String::from_utf8_lossy(&out.stdout)
        } else {
            err
        };
        Err(format!("gh {}\n{}", args.join(" "), msg.trim()))
    }
}

pub fn api(method: &str, path: &str, body: Option<&Value>) -> Result<Value, String> {
    let out = gh(&["api", "-X", method, path], body)?;
    if out.trim().is_empty() {
        Ok(Value::Null)
    } else {
        serde_json::from_str(&out).map_err(|e| format!("gh api {path}: bad JSON: {e}"))
    }
}

pub fn current_user() -> Result<String, String> {
    Ok(gh(&["api", "user", "--jq", ".login"], None)?
        .trim()
        .to_string())
}

pub fn repo_exists(full_name: &str) -> Result<bool, String> {
    match gh(&["repo", "view", full_name, "--json", "name"], None) {
        Ok(_) => Ok(true),
        Err(e) if e.contains("Could not resolve to a Repository") => Ok(false),
        Err(e) => Err(e), // network/auth/rate-limit: don't treat as "name is free"
    }
}

pub fn is_private(repo: &str) -> Result<bool, String> {
    Ok(api("GET", &format!("repos/{repo}"), None)?["private"]
        .as_bool()
        .unwrap_or(false))
}

/// Create an empty repo and add it as `origin`. The push happens later, in
/// `configure_all`, once push protection is on.
pub fn create_repo(
    repo: &str,
    source: &Path,
    description: &str,
    private: bool,
) -> Result<(), String> {
    let src = source.to_string_lossy();
    gh(
        &[
            "repo",
            "create",
            repo,
            if private { "--private" } else { "--public" },
            "--description",
            description,
            "--source",
            &src,
            "--remote",
            "origin",
        ],
        None,
    )
    .map(|_| ())
}

/// PRs only, CI must pass, no force-push/delete. Zero approvals: you can't
/// approve your own PR on a solo repo.
pub fn ruleset_body() -> Value {
    json!({
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_linear_history"},
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": true,
                "require_code_owner_review": false,
                "require_last_push_approval": false,
                "required_review_thread_resolution": true,
                "allowed_merge_methods": ["squash"],
            }},
            {"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": false,
                "required_status_checks": [
                    {"context": REQUIRED_CHECK, "integration_id": GITHUB_ACTIONS_APP_ID}
                ],
            }},
        ],
    })
}

pub fn configure_ruleset(repo: &str) -> Result<(), String> {
    let existing = api(
        "GET",
        &format!("repos/{repo}/rulesets?includes_parents=false"),
        None,
    )?;
    let found = existing
        .as_array()
        .into_iter()
        .flatten()
        .find(|r| r["name"] == RULESET_NAME)
        .and_then(|r| r["id"].as_u64());
    match found {
        Some(id) => api(
            "PUT",
            &format!("repos/{repo}/rulesets/{id}"),
            Some(&ruleset_body()),
        )?,
        None => api(
            "POST",
            &format!("repos/{repo}/rulesets"),
            Some(&ruleset_body()),
        )?,
    };
    Ok(())
}

const FIND_PROJECT_QUERY: &str = "
query($owner: String!, $title: String!) {
  user(login: $owner) {
    projectsV2(first: 100, query: $title) {
      nodes { number title }
    }
  }
}
";

fn find_project_by_title(owner: &str, title: &str) -> Result<u64, String> {
    let listing = gh(
        &[
            "api",
            "graphql",
            "-f",
            &format!("query={FIND_PROJECT_QUERY}"),
            "-f",
            &format!("owner={owner}"),
            "-f",
            &format!("title={title}"),
        ],
        None,
    )?;
    let parsed: Value = serde_json::from_str(&listing).map_err(|e| format!("graphql: {e}"))?;
    let nodes = parsed["data"]["user"]["projectsV2"]["nodes"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let number = nodes
        .iter()
        .find(|p| p["title"] == title)
        .and_then(|p| p["number"].as_u64())
        .unwrap_or(0);
    if number != 0 || nodes.len() < 100 {
        return Ok(number);
    }
    let full = gh(
        &[
            "project", "list", "--owner", owner, "--closed", "--limit", "1000", "--format", "json",
        ],
        None,
    )?;
    let parsed: Value = serde_json::from_str(&full).map_err(|e| format!("project list: {e}"))?;
    Ok(parsed["projects"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|p| p["title"] == title)
        .and_then(|p| p["number"].as_u64())
        .unwrap_or(0))
}

/// Link the repo to a same-named project, creating one if it doesn't exist.
pub fn configure_project(repo: &str) -> Result<(), String> {
    let (owner, title) = repo.split_once('/').ok_or("repo must be owner/name")?;
    let mut number = find_project_by_title(owner, title)?;
    if number == 0 {
        let created = gh(
            &[
                "project", "create", "--owner", owner, "--title", title, "--format", "json",
            ],
            None,
        )?;
        let parsed: Value =
            serde_json::from_str(&created).map_err(|e| format!("project create: {e}"))?;
        number = parsed["number"]
            .as_u64()
            .ok_or("project create: no number")?;
    }
    gh(
        &[
            "project",
            "link",
            &number.to_string(),
            "--owner",
            owner,
            "--repo",
            repo,
        ],
        None,
    )
    .map(|_| ())
}

/// Turn off GitHub Actions entirely for this repo (private repos have no minutes to spare).
pub fn disable_actions(repo: &str) -> Result<(), String> {
    api(
        "PUT",
        &format!("repos/{repo}/actions/permissions"),
        Some(&json!({"enabled": false})),
    )
    .map(|_| ())
}

pub fn settings_body(private: bool) -> Value {
    let mut body = json!({
        "has_wiki": false,
        "allow_merge_commit": false,
        "allow_rebase_merge": false,
        "allow_auto_merge": true,
        "allow_update_branch": true,
        "delete_branch_on_merge": true,
        "squash_merge_commit_title": "PR_TITLE",
        "squash_merge_commit_message": "PR_BODY",
    });
    if !private {
        body["security_and_analysis"] = json!({
            "secret_scanning": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "enabled"},
        });
    }
    body
}

type Step = Box<dyn Fn() -> Result<(), String> + Send + Sync>;

/// Apply every setting; return the names of failed steps (the rest still run).
/// Three phases: repo settings (serial), every independent step including the
/// push (concurrent), then the ruleset, which needs the pushed `main`.
pub fn configure_all(
    repo: &str,
    private: bool,
    project: bool,
    push: Option<Step>,
    log: &(dyn Fn(&str) + Sync),
) -> Vec<String> {
    let failed: Mutex<Vec<String>> = Mutex::new(Vec::new());
    let report = |name: &str, result: Result<(), String>| {
        match &result {
            Ok(()) => log(&format!("  ✓ {name}")),
            Err(e) => log(&format!("  ✗ {name}: {e}")),
        }
        if result.is_err() {
            failed.lock().expect("lock").push(name.to_string());
        }
    };

    if private {
        log("  - private repo: skipping ruleset, secret scanning, vuln reporting (need paid plan)");
    }
    let repo_s = repo.to_string();
    report(
        "repo settings",
        api(
            "PATCH",
            &format!("repos/{repo}"),
            Some(&settings_body(private)),
        )
        .map(|_| ()),
    );
    let mut actions_disabled = true;
    if private {
        let result = disable_actions(repo);
        actions_disabled = result.is_ok();
        report("disable actions", result);
    }

    let mut fanout: Vec<(String, Step)> = Vec::new();
    {
        let r = repo_s.clone();
        fanout.push((
            "dependabot alerts + security fixes".into(),
            Box::new(move || {
                api("PUT", &format!("repos/{r}/vulnerability-alerts"), None)?;
                api("PUT", &format!("repos/{r}/automated-security-fixes"), None)?;
                Ok(())
            }),
        ));
    }
    if !private {
        let r = repo_s.clone();
        fanout.push((
            "private vulnerability reporting".into(),
            Box::new(move || {
                api(
                    "PUT",
                    &format!("repos/{r}/private-vulnerability-reporting"),
                    None,
                )
                .map(|_| ())
            }),
        ));
    }
    {
        let r = repo_s.clone();
        let names: Vec<&str> = LABELS.iter().map(|(n, _)| *n).collect();
        fanout.push((
            format!("labels: {}", names.join(", ")),
            Box::new(move || {
                for (name, color) in LABELS {
                    gh(
                        &[
                            "label", "create", name, "--repo", &r, "--color", color, "--force",
                        ],
                        None,
                    )?;
                }
                Ok(())
            }),
        ));
    }
    {
        let r = repo_s.clone();
        fanout.push((
            "mute notifications (watch: ignore)".into(),
            Box::new(move || {
                api(
                    "PUT",
                    &format!("repos/{r}/subscription"),
                    Some(&json!({"subscribed": false, "ignored": true})),
                )
                .map(|_| ())
            }),
        ));
    }
    if project {
        let r = repo_s.clone();
        fanout.push((
            "project board".into(),
            Box::new(move || configure_project(&r)),
        ));
    }
    let mut push_index: Option<usize> = None;
    match push {
        Some(_) if !actions_disabled => {
            log("  - push main: skipped (couldn't disable Actions; refusing to risk a run)");
            failed.lock().expect("lock").push("push main".into());
        }
        Some(step) => {
            push_index = Some(fanout.len());
            fanout.push(("push main".into(), step));
        }
        None => {}
    }

    let results: Vec<Result<(), String>> = std::thread::scope(|s| {
        let handles: Vec<_> = fanout
            .iter()
            .map(|(_, step)| s.spawn(move || step()))
            .collect();
        handles
            .into_iter()
            .map(|h| h.join().unwrap_or_else(|_| Err("step panicked".into())))
            .collect()
    });
    let mut push_failed = false;
    for (i, ((name, _), result)) in fanout.iter().zip(results).enumerate() {
        if Some(i) == push_index && result.is_err() {
            push_failed = true;
        }
        report(name, result);
    }

    if !private && !push_failed {
        report(
            &format!("ruleset '{RULESET_NAME}'"),
            configure_ruleset(repo),
        );
    }
    failed.into_inner().expect("lock")
}
