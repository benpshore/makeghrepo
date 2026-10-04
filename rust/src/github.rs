//! GitHub setup via the `gh` CLI (which owns auth; we never touch tokens).
//! Every step is idempotent, so a re-run after a partial failure is safe.

use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::Mutex;

use serde_json::{Map, Value, json};

pub const RULESET_NAME: &str = "protect-main";
pub const REVIEW_RULESET_NAME: &str = "require-pr-and-ci";
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

/// History protections apply to everyone, including the owner.
pub fn ruleset_body() -> Value {
    json!({
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_linear_history"},
        ],
    })
}

/// Only the personal repository owner may push without a PR or passing CI.
#[cfg(test)]
pub fn review_ruleset_body(owner_id: u64) -> Value {
    review_ruleset_body_policy(owner_id, false, false)
}

fn review_ruleset_body_policy(owner_id: u64, no_ci: bool, no_pr: bool) -> Value {
    let mut body = ruleset_body();
    body["name"] = json!(REVIEW_RULESET_NAME);
    body["bypass_actors"] = json!([
        {"actor_type": "User", "actor_id": owner_id, "bypass_mode": "always"}
    ]);
    body["rules"] = json!([
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews_on_push": true,
                "require_code_owner_review": false,
                "require_last_push_approval": false,
                "required_review_thread_resolution": true,
                "allowed_merge_methods": ["squash"],
            }},
            {"type": "required_status_checks", "parameters": {
                // Retain the strict live gate on configure reruns (#126).
                "strict_required_status_checks_policy": true,
                "do_not_enforce_on_create": false,
                "required_status_checks": [
                    {"context": REQUIRED_CHECK, "integration_id": GITHUB_ACTIONS_APP_ID}
                ],
            }},
    ]);
    body["rules"].as_array_mut().unwrap().retain(|rule| {
        !(no_pr && rule["type"] == "pull_request"
            || no_ci && rule["type"] == "required_status_checks")
    });
    body
}

pub fn configure_ruleset(repo: &str, no_ci: bool, no_pr: bool) -> Result<(), String> {
    configure_ruleset_policy_with(repo, no_ci, no_pr, false, api)
}

fn configure_ruleset_policy_with(
    repo: &str,
    no_ci: bool,
    no_pr: bool,
    validate_only: bool,
    mut api: impl FnMut(&str, &str, Option<&Value>) -> Result<Value, String>,
) -> Result<(), String> {
    // Resolve the repository owner, never the caller or commit author identity.
    let metadata = api("GET", &format!("repos/{repo}"), None)?;
    let owner = &metadata["owner"];
    let owner_id = owner["id"].as_u64().filter(|id| *id > 0);
    if owner["type"] != "User"
        || owner_id.is_none()
        || !owner["login"]
            .as_str()
            .unwrap_or_default()
            .eq_ignore_ascii_case(repo.split('/').next().unwrap_or_default())
    {
        return Err(
            "owner push bypass requires a personal repository with a verified owner ID".into(),
        );
    }
    let mut bodies = [
        ruleset_body(),
        review_ruleset_body_policy(owner_id.unwrap(), no_ci, no_pr),
    ];
    let mut existing = Vec::new();
    for page in 1.. {
        let batch = api(
            "GET",
            &format!("repos/{repo}/rulesets?includes_parents=false&per_page=100&page={page}"),
            None,
        )?;
        let batch = batch.as_array().ok_or("invalid ruleset listing")?;
        existing.extend(batch.iter().cloned());
        if batch.len() < 100 {
            break;
        }
    }
    let mut ids = [None, None];
    let mut saved_rules = [Map::new(), Map::new()];
    // Validate both full bodies before writing. Refuse customized scopes or
    // conflicting gate policies instead of silently widening the owner bypass.
    for (index, body) in bodies.iter().enumerate() {
        let name = body["name"].as_str().unwrap();
        let matches: Vec<_> = existing.iter().filter(|r| r["name"] == name).collect();
        if matches.len() > 1 {
            return Err(format!(
                "duplicate ruleset {name:?}; reconcile it manually before configuring"
            ));
        }
        if let Some(found) = matches.first() {
            let id = found["id"].as_u64().ok_or("invalid ruleset ID")?;
            ids[index] = Some(id);
            let saved = api("GET", &format!("repos/{repo}/rulesets/{id}"), None)?;
            if ["target", "enforcement", "conditions"]
                .iter()
                .any(|k| saved[*k] != body[*k])
                || (saved["bypass_actors"] != json!([])
                    && saved["bypass_actors"] != body["bypass_actors"])
            {
                return Err(format!(
                    "customized ruleset {name:?}; review its scope/bypasses manually"
                ));
            }
            for rule in saved["rules"].as_array().ok_or("invalid ruleset rules")? {
                let kind = rule["type"].as_str().ok_or("invalid rule type")?;
                if saved_rules[index]
                    .insert(kind.into(), rule.clone())
                    .is_some()
                {
                    return Err(format!("duplicate rule in {name:?}; reconcile it manually"));
                }
            }
        }
    }
    let [legacy, gates] = saved_rules;
    let gate_types: Vec<String> = bodies[1]["rules"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["type"].as_str().unwrap().to_string())
        .collect();
    for kind in ["pull_request", "required_status_checks"] {
        if !gate_types.iter().any(|k| k == kind)
            && (legacy.contains_key(kind) || gates.contains_key(kind))
        {
            return Err(
                "creation opt-outs would weaken existing rules; refusing to change live policy"
                    .into(),
            );
        }
    }
    if (no_ci || no_pr) && legacy.keys().any(|k| gate_types.contains(k)) {
        return Err(
            "creation opt-outs cannot migrate existing history gates; review live policy".into(),
        );
    }
    if gates.keys().any(|k| !gate_types.contains(k)) {
        return Err(format!(
            "customized rules in {REVIEW_RULESET_NAME:?}; cannot grant owner bypass"
        ));
    }
    for default in bodies[1]["rules"].as_array_mut().unwrap() {
        let kind = default["type"].as_str().unwrap();
        if let (Some(old), Some(new)) = (legacy.get(kind), gates.get(kind)) {
            if old != new {
                return Err(format!(
                    "conflicting {kind} rules; reconcile them manually before configuring"
                ));
            }
        }
        // Retain extra checks/review parameters, including fields GitHub adds.
        if let Some(saved) = gates.get(kind).or_else(|| legacy.get(kind)) {
            *default = saved.clone();
        }
    }
    let history = bodies[0]["rules"].as_array_mut().unwrap();
    for (kind, rule) in legacy {
        if !gate_types.contains(&kind) {
            if let Some(existing) = history.iter_mut().find(|r| r["type"] == kind) {
                *existing = rule;
            } else {
                history.push(rule);
            }
        }
    }
    // The replacement gate must succeed before removing the legacy gate.
    // A failed migration therefore leaves an existing repo's PR/CI gate intact.
    if validate_only {
        return Ok(());
    }
    for index in [1, 0] {
        if bodies[index]["rules"].as_array().unwrap().is_empty() {
            continue;
        }
        match ids[index] {
            Some(id) => api(
                "PUT",
                &format!("repos/{repo}/rulesets/{id}"),
                Some(&bodies[index]),
            )?,
            None => api(
                "POST",
                &format!("repos/{repo}/rulesets"),
                Some(&bodies[index]),
            )?,
        };
    }
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
        "allow_squash_merge": true,
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
/// Repo settings settle before the push. Independent steps run concurrently;
/// the ruleset starts after the push, without waiting for Project setup.
pub fn configure_all(
    repo: &str,
    private: bool,
    project: bool,
    policy: (bool, bool),
    push: Option<Step>,
    log: &(dyn Fn(&str) + Sync),
) -> Vec<String> {
    let (no_ci, no_pr) = policy;
    if private && (no_ci || no_pr) {
        log("  ✗ creation policy: --no-ci and --no-pr only apply to public repositories");
        return vec!["creation policy".into()];
    }
    if no_ci || no_pr {
        if let Err(e) = configure_ruleset_policy_with(repo, no_ci, no_pr, true, api) {
            log(&format!("  ✗ creation policy: {e}"));
            return vec!["creation policy".into()];
        }
    }
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
    let settings = api(
        "PATCH",
        &format!("repos/{repo}"),
        Some(&settings_body(private)),
    )
    .map(|_| ());
    let settings_ok = settings.is_ok();
    report("repo settings", settings);
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
    // Publishing is gated on the relevant protection step: public repos need the
    // settings PATCH (secret-scanning push protection) applied, private repos need
    // Actions disabled. Independent settings still run either way (#111).
    let push_blocked = push.is_some() && (!actions_disabled || (!private && !settings_ok));
    let mut push_index: Option<usize> = None;
    match push {
        Some(_) if !actions_disabled => {
            log("  - push main: skipped (couldn't disable Actions; refusing to risk a run)");
            failed.lock().expect("lock").push("push main".into());
        }
        Some(_) if push_blocked => {
            log("  - push main: skipped (couldn't enable push protection; refusing to publish)");
            failed.lock().expect("lock").push("push main".into());
        }
        Some(step) => {
            push_index = Some(fanout.len());
            fanout.push(("push main".into(), step));
        }
        None => {}
    }

    let (results, ruleset_result) = std::thread::scope(|s| {
        let mut handles: Vec<_> = (0..fanout.len()).map(|_| None).collect();
        // Start the push first, so slow independent operations cannot delay it.
        if let Some(i) = push_index {
            handles[i] = Some(s.spawn(&*fanout[i].1));
        }
        for (i, (_, step)) in fanout.iter().enumerate() {
            if Some(i) != push_index {
                handles[i] = Some(s.spawn(&**step));
            }
        }
        let mut push_result = push_index.map(|i| {
            handles[i]
                .take()
                .expect("push handle")
                .join()
                .unwrap_or_else(|_| Err("step panicked".into()))
        });
        let ruleset_result =
            if !private && !push_blocked && push_result.as_ref().is_none_or(Result::is_ok) {
                Some(configure_ruleset(repo, no_ci, no_pr))
            } else {
                None
            };
        let results: Vec<Result<(), String>> = handles
            .into_iter()
            .enumerate()
            .map(|(i, handle)| {
                if Some(i) == push_index {
                    push_result.take().expect("push result")
                } else {
                    handle
                        .expect("step handle")
                        .join()
                        .unwrap_or_else(|_| Err("step panicked".into()))
                }
            })
            .collect();
        (results, ruleset_result)
    });
    for ((name, _), result) in fanout.iter().zip(results) {
        report(name, result);
    }

    if let Some(result) = ruleset_result {
        report(&format!("ruleset '{RULESET_NAME}'"), result);
    }
    failed.into_inner().expect("lock")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn defaults() -> Value {
        // Shared with tests/test_github.py: both implementations must obey it.
        serde_json::from_str(include_str!("../../tests/fixtures/github_defaults.json"))
            .expect("valid policy fixture")
    }

    #[test]
    fn ruleset_matches_shared_policy() {
        assert_eq!(ruleset_body(), defaults()["ruleset"]);
        assert_eq!(review_ruleset_body(123), defaults()["review_ruleset"]);
    }

    #[test]
    fn settings_match_shared_policy() {
        let policy = defaults();
        for private in [false, true] {
            let mut expected = policy["settings"].clone();
            if !private {
                expected["security_and_analysis"] = policy["public_security_and_analysis"].clone();
            }
            assert_eq!(settings_body(private), expected);
        }
    }

    struct RulesetApi {
        state: std::collections::BTreeMap<u64, Value>,
        calls: Vec<(String, String, Option<Value>)>,
        owner: Value,
        fail_write: usize,
    }

    impl RulesetApi {
        fn new(state: impl IntoIterator<Item = (u64, Value)>) -> Self {
            Self {
                state: state.into_iter().collect(),
                calls: Vec::new(),
                owner: json!({"login": "me", "id": 123, "type": "User"}),
                fail_write: 0,
            }
        }

        fn writes(&self) -> Vec<&(String, String, Option<Value>)> {
            self.calls.iter().filter(|c| c.0 != "GET").collect()
        }

        fn configure(&mut self) -> Result<(), String> {
            self.configure_policy(false, false, false)
        }

        fn configure_policy(&mut self, no_ci: bool, no_pr: bool, validate_only: bool) -> Result<(), String> {
            configure_ruleset_policy_with("me/r", no_ci, no_pr, validate_only, |method, path, body| {
                self.calls.push((method.into(), path.into(), body.cloned()));
                if method == "GET" {
                    if path == "repos/me/r" {
                        return Ok(json!({"owner": self.owner}));
                    }
                    if path.contains('?') {
                        let page: usize = path.rsplit("page=").next().unwrap().parse().unwrap();
                        return Ok(json!(
                            self.state
                                .iter()
                                .skip((page - 1) * 100)
                                .take(100)
                                .map(|(id, v)| json!({"id": id, "name": v["name"]}))
                                .collect::<Vec<_>>()
                        ));
                    }
                    let id = path.rsplit('/').next().unwrap().parse::<u64>().unwrap();
                    return Ok(self.state[&id].clone());
                }
                if self.fail_write > 0 && self.writes().len() == self.fail_write {
                    return Err("write rejected".into());
                }
                let id = if method == "PUT" {
                    path.rsplit('/').next().unwrap().parse().unwrap()
                } else {
                    self.state.keys().last().copied().unwrap_or(0) + 1
                };
                self.state.insert(id, body.unwrap().clone());
                Ok(json!({"id": id}))
            })
        }
    }

    #[test]
    fn creation_matrix_matches_shared_fixture_and_survives_resume() {
        let cases: Value = serde_json::from_str(include_str!("../../tests/fixtures/creation_policy.json")).unwrap();
        for case in cases.as_array().unwrap() {
            let (no_ci, no_pr) = (case["no_ci"].as_bool().unwrap(), case["no_pr"].as_bool().unwrap());
            let body = review_ruleset_body_policy(123, no_ci, no_pr);
            let types: Vec<_> = body["rules"].as_array().unwrap().iter().map(|r| r["type"].clone()).collect();
            assert_eq!(json!(types), case["rules"]);
            assert_eq!(body["bypass_actors"], defaults()["review_ruleset"]["bypass_actors"]);
            let mut api = RulesetApi::new([(7, json!({"name": "custom-policy", "rules": [{"type": "creation"}]}))]);
            api.configure_policy(no_ci, no_pr, true).unwrap();
            assert!(api.writes().is_empty());
            for _ in 0..2 {
                api.configure_policy(no_ci, no_pr, false).unwrap();
                assert!(api.state.values().any(|v| v == &ruleset_body()));
                let gate = api.state.values().find(|v| v["name"] == REVIEW_RULESET_NAME);
                if no_ci && no_pr {
                    assert!(gate.is_none());
                } else {
                    assert_eq!(gate.unwrap(), &body);
                }
                assert_eq!(api.state[&7]["rules"], json!([{"type": "creation"}]));
                assert!(api.writes().iter().all(|c| !c.2.as_ref().unwrap()["rules"].as_array().unwrap().is_empty()));
            }
        }
    }

    #[test]
    fn opt_out_never_removes_existing_split_or_legacy_requirements() {
        for (no_ci, no_pr) in [(true, false), (false, true), (true, true)] {
            for legacy in [false, true] {
                let mut api = if legacy {
                    RulesetApi::new([(42, legacy_policy())])
                } else {
                    RulesetApi::new([(42, ruleset_body()), (43, review_ruleset_body(123))])
                };
                let before = api.state.clone();
                assert!(api.configure_policy(no_ci, no_pr, false).is_err());
                assert!(api.writes().is_empty());
                assert_eq!(api.state, before);
            }
        }
    }

    #[test]
    fn opt_out_retains_stricter_remaining_gate() {
        for (no_ci, no_pr) in [(true, false), (false, true)] {
            let mut body = review_ruleset_body_policy(123, no_ci, no_pr);
            if no_ci {
                body["rules"][0]["parameters"]["required_approving_review_count"] = json!(2);
            } else {
                body["rules"][0]["parameters"]["required_status_checks"].as_array_mut().unwrap().push(json!({"context": "security"}));
            }
            let mut api = RulesetApi::new([(42, ruleset_body()), (43, body.clone())]);
            api.configure_policy(no_ci, no_pr, false).unwrap();
            assert_eq!(api.state[&43], body);
        }
    }

    fn legacy_policy() -> Value {
        let policy = defaults();
        let mut legacy = policy["ruleset"].clone();
        legacy["rules"].as_array_mut().unwrap().extend(
            policy["review_ruleset"]["rules"]
                .as_array()
                .unwrap()
                .iter()
                .cloned(),
        );
        legacy
    }

    #[test]
    fn owner_only_bypass_survives_reruns() {
        let policy = defaults();
        for mode in ["new", "legacy", "split", "partial"] {
            let mut api = RulesetApi::new([(7, json!({"name": "another-rule"}))]);
            if mode != "new" {
                api.state.insert(
                    42,
                    if mode == "split" {
                        policy["ruleset"].clone()
                    } else {
                        legacy_policy()
                    },
                );
            }
            if ["split", "partial"].contains(&mode) {
                api.state.insert(43, policy["review_ruleset"].clone());
            }
            for _ in 0..2 {
                api.calls.clear();
                api.configure().unwrap();
                let writes = api.writes();
                assert_eq!(writes.len(), 2);
                assert_eq!(writes[0].2, Some(policy["review_ruleset"].clone()));
                assert_eq!(writes[1].2, Some(policy["ruleset"].clone()));
                assert_eq!(api.state.len(), 3);
                assert_eq!(api.state[&7], json!({"name": "another-rule"}));
            }
            assert!(api.writes().iter().all(|c| c.0 == "PUT"));
        }
    }

    #[test]
    fn migration_retains_extra_checks_and_unrelated_rules() {
        let mut legacy = legacy_policy();
        legacy["rules"][3]["parameters"]["require_extra_approval_for_unattributed_changes"] =
            json!(true);
        legacy["rules"][4]["parameters"]["required_status_checks"]
            .as_array_mut()
            .unwrap()
            .push(json!({"context": "security"}));
        legacy["rules"]
            .as_array_mut()
            .unwrap()
            .push(json!({"type": "required_signatures"}));
        let mut api = RulesetApi::new([(42, legacy.clone())]);
        api.configure().unwrap();
        assert_eq!(
            api.state[&43]["rules"],
            json!([legacy["rules"][3], legacy["rules"][4]])
        );
        assert_eq!(
            api.state[&42]["rules"],
            json!([
                legacy["rules"][0],
                legacy["rules"][1],
                legacy["rules"][2],
                legacy["rules"][5]
            ])
        );
        assert_eq!(api.state[&42]["bypass_actors"], json!([]));
        let expected = api.state.clone();
        api.configure().unwrap();
        assert_eq!(api.state, expected);
    }

    #[test]
    fn failed_migration_retains_legacy_gate() {
        for failure in [1, 2] {
            let legacy = legacy_policy();
            let mut api = RulesetApi::new([(42, legacy.clone())]);
            api.fail_write = failure;
            assert_eq!(api.configure().unwrap_err(), "write rejected");
            assert_eq!(api.state[&42], legacy);
            assert_eq!(api.writes().len(), failure);
        }
    }

    #[test]
    fn unverified_owner_never_grants_bypass() {
        for owner in [
            json!({"type": "Organization", "login": "me", "id": 123}),
            json!({"type": "User", "login": "someone-else", "id": 123}),
            json!({"type": "User", "login": "me"}),
            json!({"type": "User", "login": "me", "id": true}),
            json!({"type": "User", "login": "me", "id": 0}),
        ] {
            let mut api = RulesetApi::new([]);
            api.owner = owner;
            assert!(api.configure().unwrap_err().contains("verified owner ID"));
            assert!(api.writes().is_empty());
        }
    }

    #[test]
    fn ambiguous_policy_refused_before_writes() {
        for customization in [
            "scope",
            "enforcement",
            "bypass",
            "hidden_bypass",
            "duplicate",
            "conflict",
            "extra_gate",
        ] {
            let mut legacy = legacy_policy();
            let mut review = defaults()["review_ruleset"].clone();
            match customization {
                "scope" => legacy["conditions"]["ref_name"]["include"]
                    .as_array_mut()
                    .unwrap()
                    .push(json!("refs/heads/release/*")),
                "enforcement" => legacy["enforcement"] = json!("disabled"),
                "bypass" => {
                    legacy["bypass_actors"] =
                        json!([{"actor_type": "RepositoryRole", "actor_id": 5}])
                }
                "hidden_bypass" => {
                    legacy.as_object_mut().unwrap().remove("bypass_actors");
                }
                "conflict" => {
                    review["rules"][0]["parameters"]["required_approving_review_count"] = json!(2)
                }
                "extra_gate" => review["rules"]
                    .as_array_mut()
                    .unwrap()
                    .push(json!({"type": "required_signatures"})),
                _ => {}
            }
            let mut api = RulesetApi::new([(42, legacy.clone()), (43, review)]);
            if customization == "duplicate" {
                api.state.insert(44, legacy);
            }
            assert!(api.configure().is_err(), "{customization}");
            assert!(api.writes().is_empty(), "{customization}");
        }
    }

    #[test]
    fn ruleset_listing_is_paginated() {
        let mut api = RulesetApi::new((0..100).map(|i| (i, json!({"name": format!("other-{i}")}))));
        api.state.insert(142, legacy_policy());
        api.configure().unwrap();
        assert!(api.calls.iter().any(|c| c.1.ends_with("page=2")));
        assert_eq!(api.calls.last().unwrap().1, "repos/me/r/rulesets/142");
    }
}
