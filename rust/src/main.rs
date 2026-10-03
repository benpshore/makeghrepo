//! makeghrepo [NAME] [LANGUAGE]... [--private] [--lib]
//!
//! The Rust port of the Python tool: same template, same registry, same steps.

mod github;
mod gitops;
mod names;
mod registry;
mod render;
mod smoke;

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{CommandFactory, FromArgMatches, Parser};
use serde_json::{Value, json};

fn version() -> &'static str {
    option_env!("MAKEGHREPO_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"))
}

#[derive(Parser, Debug)]
#[command(
    name = "makeghrepo",
    version = version(),
    about = "Create a new GitHub repo, fully configured. Re-run the same command to resume."
)]
struct Cli {
    /// [NAME] [LANGUAGE]...
    #[arg(value_name = "[NAME] [LANGUAGE]...")]
    words: Vec<String>,
    #[arg(long)]
    private: bool,
    /// python: library layout, no console script (like uv init --lib)
    #[arg(long)]
    lib: bool,
    /// Project license: none by default; MIT only when requested.
    #[arg(long, value_parser = ["none", "MIT"])]
    license: Option<String>,
    /// Dry run: render the project into DIR and stop (no checks, no git, no GitHub).
    #[arg(long, value_name = "DIR")]
    render: Option<PathBuf>,
    /// Print the rendered project in golden-snapshot format and exit (no git, no GitHub).
    #[arg(long)]
    snapshot: bool,
    /// With --snapshot: a tests/golden combo name, e.g. `rust+docker` or `python-lib`.
    #[arg(long, value_name = "NAME", requires = "snapshot")]
    combo: Option<String>,
    /// Fixed inputs for --render/--snapshot (defaults match tests/golden_snapshots.py).
    #[arg(long, hide = true)]
    owner: Option<String>,
    #[arg(long, hide = true)]
    author: Option<String>,
    #[arg(long, hide = true)]
    description: Option<String>,
    #[arg(long, hide = true)]
    year: Option<String>,
}

fn fail(msg: &str) -> ExitCode {
    eprintln!("\x1b[31m{msg}\x1b[0m");
    ExitCode::from(1)
}

fn echo(line: &str) {
    println!("{line}");
}

fn current_year() -> String {
    // Days since the epoch -> civil year (Howard Hinnant's algorithm); no chrono needed.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let z = (secs / 86_400) as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }).to_string()
}

/// Split a tests/golden combo name into (languages, private, lib, MIT).
fn parse_combo(
    langs: &[registry::Lang],
    name: &str,
) -> Result<(Vec<String>, bool, bool, bool), String> {
    let (body, mit) = match name.strip_suffix("-mit") {
        Some(b) => (b, true),
        None => (name, false),
    };
    let (body, private) = match body.strip_suffix("-private") {
        Some(b) => (b, true),
        None => (body, false),
    };
    let (body, lib) = match body.strip_suffix("-lib") {
        Some(b) => (b, true),
        None => (body, false),
    };
    let languages = match body {
        "base" => Vec::new(),
        "all" => registry::ids(langs),
        _ => body
            .split('+')
            .map(|w| {
                registry::language(langs, w)
                    .map(str::to_string)
                    .ok_or(format!("unknown combo {name:?}"))
            })
            .collect::<Result<Vec<_>, _>>()?,
    };
    Ok((languages, private, lib, mit))
}

fn validate_resume_license(marker: &Value, requested: Option<&str>) -> Result<(), String> {
    // Older makeghrepo versions always generated MIT, before recording a choice.
    let recorded = match marker.get("license") {
        None => "MIT",
        Some(value) => value.as_str().unwrap_or_default(),
    };
    if !matches!(recorded, "none" | "MIT") {
        return Err("invalid recorded license; review the resume marker".into());
    }
    if requested.is_some_and(|license| license != recorded) {
        return Err(format!(
            "project was created with license {recorded}; \
             a resume never changes licensing. Re-run without --license"
        ));
    }
    Ok(())
}

fn temp_dir(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    std::env::temp_dir().join(format!("makeghrepo-{tag}-{}-{nanos}", std::process::id()))
}

fn project_name(raw: &str, langs: &[registry::Lang], chosen: &[String]) -> Result<String, String> {
    let name = names::validate_name(&names::normalize_name(raw))?;
    if let Some(first) = chosen
        .iter()
        .find(|id| registry::get(langs, id).leading_letter)
    {
        if !name.chars().next().is_some_and(|c| c.is_ascii_alphabetic()) {
            return Err(format!(
                "{name:?} can't be a {first} package name: it must start with a letter. Pick another name"
            ));
        }
    }
    Ok(name)
}

fn main() -> ExitCode {
    let langs = registry::load();
    let all: Vec<String> = registry::ids(&langs);
    let matches = Cli::command()
        .after_help(format!("Languages (any number, or none): {}.", all.join(", ")))
        .get_matches();
    let cli = Cli::from_arg_matches(&matches).unwrap_or_else(|error| error.exit());

    // A leading non-language word is the name; otherwise pick a random one.
    let mut words = cli.words.clone();
    let raw_name = if words
        .first()
        .is_some_and(|w| registry::language(&langs, w).is_none())
    {
        Some(words.remove(0))
    } else {
        None
    };
    let mut chosen: Vec<String> = Vec::new();
    for word in &words {
        let Some(id) = registry::language(&langs, word) else {
            return fail(&format!(
                "unknown language {word:?}. Choose from: {}",
                all.join(", ")
            ));
        };
        if !chosen.iter().any(|c| c == id) {
            chosen.push(id.to_string());
        }
    }
    if cli.lib && !chosen.iter().any(|c| c == "python") {
        return fail("--lib only applies to python; add `python` to the language list");
    }

    // Offline modes: render only, or render and print the golden serialization.
    if cli.snapshot || cli.render.is_some() {
        let (languages, private, lib, mit) = match &cli.combo {
            Some(name) => match parse_combo(&langs, name) {
                Ok(c) => c,
                Err(e) => return fail(&e),
            },
            None => (chosen.clone(), cli.private, cli.lib, false),
        };
        let name = match project_name(
            raw_name.as_deref().unwrap_or("quiet-otter"),
            &langs,
            &languages,
        ) {
            Ok(name) => name,
            Err(e) => return fail(&e),
        };
        let data = render::Data {
            package_name: names::package_name(&name),
            project_name: name,
            description: cli
                .description
                .clone()
                .unwrap_or_else(|| "a test project".into()),
            author_name: cli.author.clone().unwrap_or_else(|| "Test User".into()),
            github_owner: cli.owner.clone().unwrap_or_else(|| "someone".into()),
            year: cli.year.clone().unwrap_or_else(|| "2026".into()),
            private,
            py_lib: lib,
            project_license: cli
                .license
                .as_deref()
                .unwrap_or(if mit { "MIT" } else { "none" })
                .into(),
            languages,
        };
        if let Some(dir) = &cli.render {
            return match render::render(dir, &langs, &data) {
                Ok(()) => {
                    echo(&format!("rendered {}", dir.display()));
                    ExitCode::SUCCESS
                }
                Err(e) => fail(&e),
            };
        }
        let dir = temp_dir("snapshot");
        let result = render::render(&dir.join("quiet-otter"), &langs, &data)
            .and_then(|()| render::serialize(&dir.join("quiet-otter")));
        let _ = std::fs::remove_dir_all(&dir);
        return match result {
            Ok(text) => {
                print!("{text}");
                ExitCode::SUCCESS
            }
            Err(e) => fail(&e),
        };
    }

    let base_dir = std::env::var_os("MAKEGHREPO_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            let home = std::env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("."));
            home.join("code").join("GitHub")
        });
    let owner = match github::current_user().and_then(|u| names::validate_owner(&u)) {
        Ok(o) => o,
        Err(e) => return fail(&e),
    };
    let name = match &raw_name {
        Some(raw) => match names::validate_name(&names::normalize_name(raw)) {
            Ok(n) => n,
            Err(e) => return fail(&e),
        },
        None => {
            let taken = |n: &str| {
                base_dir.join(n).exists()
                    || github::repo_exists(&format!("{owner}/{n}")).unwrap_or(true)
            };
            match names::unique_random_name(taken) {
                Ok(n) => n,
                Err(e) => return fail(&e),
            }
        }
    };
    let repo = format!("{owner}/{name}");
    let dest = base_dir.join(&name);
    let resume = dest.exists();
    let on_github = match github::repo_exists(&repo) {
        Ok(b) => b,
        Err(e) => return fail(&e),
    };

    let private: bool;
    let mut want_private = cli.private;
    if resume {
        if !dest.join(".git").is_dir() {
            return fail(&format!(
                "{} exists but isn't a git repo; pick another name",
                dest.display()
            ));
        }
        let Some(marker) = gitops::read_marker(&dest) else {
            return fail(&format!(
                "{} is a git repo makeghrepo didn't create (no .git/makeghrepo.json); pick another name",
                dest.display()
            ));
        };
        if marker["owner"] != owner.as_str() || marker["name"] != name.as_str() {
            return fail(&format!(
                "{}'s recorded owner/name does not match {repo}; \
                 restore the original account and folder name before resuming",
                dest.display()
            ));
        }
        if let Err(e) = gitops::validate_origin(&dest, &repo) {
            return fail(&e);
        }
        let marker_private = marker["private"].as_bool().unwrap_or(false);
        if cli.private && !marker_private {
            return fail(&format!(
                "{repo} was created public; re-run without --private or pick another name"
            ));
        }
        want_private = marker_private;
        if let Err(e) = validate_resume_license(&marker, cli.license.as_deref()) {
            return fail(&format!("{repo}: {e}"));
        }
        let recorded: Vec<String> = marker["languages"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| v.as_str().map(str::to_string))
            .collect();
        if !chosen.is_empty() && chosen != recorded {
            let shown = if recorded.is_empty() {
                "none".to_string()
            } else {
                recorded.join(", ")
            };
            echo(&format!(
                "note: ignoring languages on resume; using {shown}"
            ));
        }
        chosen = recorded;
        echo(&format!("resuming {}", dest.display()));
    } else if on_github {
        return fail(&format!("{repo} already exists on GitHub"));
    } else {
        if let Err(e) = project_name(&name, &langs, &chosen) {
            return fail(&e);
        }
        let shown = if chosen.is_empty() {
            "any language".to_string()
        } else {
            chosen.join(", ")
        };
        echo(&format!("creating {} [{shown}]", dest.display()));
        let (git_name, _) = gitops::author_from_git_config();
        let author_name = if git_name.is_empty() {
            owner.clone()
        } else {
            git_name
        };
        let data = render::Data {
            project_name: name.clone(),
            package_name: names::package_name(&name),
            description: name.clone(),
            author_name: author_name.clone(),
            github_owner: owner.clone(),
            year: current_year(),
            private: cli.private,
            py_lib: cli.lib,
            project_license: cli.license.clone().unwrap_or_else(|| "none".into()),
            languages: chosen.clone(),
        };
        if let Err(e) = render::render(&dest, &langs, &data) {
            return fail(&e);
        }
        if let Err(e) = gitops::init(&dest) {
            return fail(&e);
        }
        if let Err(e) = gitops::ensure_identity(
            &dest,
            &author_name,
            &format!("{owner}@users.noreply.github.com"),
        ) {
            return fail(&e);
        }
        let mut marker: BTreeMap<String, Value> = BTreeMap::new();
        marker.insert("schema".into(), json!(1));
        marker.insert("name".into(), json!(name));
        marker.insert("owner".into(), json!(owner));
        marker.insert("private".into(), json!(cli.private));
        marker.insert("languages".into(), json!(chosen));
        marker.insert("lib".into(), json!(cli.lib));
        marker.insert(
            "license".into(),
            json!(cli.license.as_deref().unwrap_or("none")),
        );
        marker.insert(
            "created_by".into(),
            json!(format!("makeghrepo {} (rust)", version())),
        );
        if let Err(e) = gitops::write_marker(&dest, &marker) {
            return fail(&e);
        }
    }

    if !resume || !gitops::has_commits(&dest) {
        let result = smoke::smoke_test(&dest, &langs, &chosen, &echo)
            .and_then(|()| gitops::commit_all(&dest, "setup"));
        if let Err(e) = result {
            return fail(&format!(
                "{e}\nNothing was published. Fix it, then re-run the same command."
            ));
        }
    }

    if on_github {
        let actual = match github::is_private(&repo) {
            Ok(p) => p,
            Err(e) => return fail(&format!("{e}\nLocal project is intact; re-run to retry.")),
        };
        // want_private carries the marker's intent on a retry, so a project created
        // private can never be published to a remote that has become public (#110).
        if want_private && !actual {
            return fail(&format!(
                "{repo} is public on GitHub, but this project was created private. \
                 Refusing to publish; makeghrepo never changes visibility. \
                 Restore the remote's private visibility before retrying"
            ));
        }
        private = actual;
    } else {
        private = want_private;
        if let Err(e) = github::create_repo(&repo, &dest, &name, private) {
            return fail(&format!("{e}\nLocal project is intact; re-run to retry."));
        }
    }

    // Bootstrap only: a configuration retry must never publish later local work.
    let pushed = if on_github {
        match gitops::remote_has_main(&dest) {
            Ok(pushed) => pushed,
            Err(e) => {
                return fail(&format!(
                    "{e}\nCould not check remote main; refusing to push. \
                     Local project is intact; re-run to retry."
                ));
            }
        }
    } else {
        false
    };
    let push_dest = dest.clone();
    let push: Option<Box<dyn Fn() -> Result<(), String> + Send + Sync>> = if pushed {
        None
    } else {
        Some(Box::new(move || gitops::push_main(&push_dest)))
    };
    let failed = github::configure_all(&repo, private, true, push, &echo);
    echo(&format!(
        "\nhttps://github.com/{repo}\ncd {}",
        dest.display()
    ));
    if failed.is_empty() {
        ExitCode::SUCCESS
    } else {
        echo(&format!(
            "{} step(s) failed. Fix, then re-run: makeghrepo {name}",
            failed.len()
        ));
        ExitCode::from(2)
    }
}

#[cfg(test)]
mod license_tests {
    use super::*;

    #[test]
    fn cli_requires_an_explicit_supported_license() {
        assert!(Cli::try_parse_from(["makeghrepo"]).unwrap().license.is_none());
        for license in ["none", "MIT"] {
            assert_eq!(
                Cli::try_parse_from(["makeghrepo", "--license", license])
                    .unwrap()
                    .license
                    .as_deref(),
                Some(license)
            );
        }
        assert!(Cli::try_parse_from(["makeghrepo", "--license", "arbitrary"]).is_err());
    }

    #[test]
    fn resume_keeps_the_recorded_license_including_legacy_markers() {
        for marker in [json!({}), json!({"license": "MIT"})] {
            assert!(validate_resume_license(&marker, None).is_ok());
            assert!(validate_resume_license(&marker, Some("MIT")).is_ok());
            assert!(validate_resume_license(&marker, Some("none")).is_err());
        }
        let marker = json!({"license": "none"});
        assert!(validate_resume_license(&marker, None).is_ok());
        assert!(validate_resume_license(&marker, Some("none")).is_ok());
        assert!(validate_resume_license(&marker, Some("MIT")).is_err());
        for invalid in [json!(null), json!(false), json!("arbitrary")] {
            assert!(validate_resume_license(&json!({"license": invalid}), None).is_err());
        }
    }
}
