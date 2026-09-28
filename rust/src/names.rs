//! Random adjective-noun repo names, e.g. `quiet-otter`, and name validation.

use std::time::{SystemTime, UNIX_EPOCH};

pub const ADJECTIVES: &[&str] = &[
    "amber", "ancient", "autumn", "bold", "brave", "bright", "brisk", "calm", "clever", "cosmic",
    "crimson", "crisp", "curious", "dapper", "daring", "dusty", "eager", "electric", "fancy",
    "fluffy", "fuzzy", "gentle", "gilded", "glad", "golden", "grand", "happy", "hidden", "humble",
    "icy", "jolly", "keen", "kind", "lively", "lucky", "lunar", "mellow", "misty", "modest",
    "mossy", "nimble", "noble", "odd", "polite", "proud", "quick", "quiet", "rapid", "rustic",
    "shiny", "silent", "silver", "sleepy", "sly", "smooth", "snowy", "solar", "sunny", "swift",
    "tidy", "tiny", "vivid", "wandering", "warm", "witty", "zesty",
];

pub const NOUNS: &[&str] = &[
    "acorn", "badger", "beacon", "bison", "brook", "cactus", "canyon", "comet", "coral", "crane",
    "dune", "ember", "falcon", "fern", "finch", "fjord", "fox", "garden", "geyser", "glacier",
    "harbor", "heron", "hollow", "island", "lagoon", "lantern", "lichen", "lynx", "maple",
    "meadow", "meteor", "moose", "nebula", "newt", "oasis", "orbit", "otter", "owl", "panda",
    "pebble", "pine", "prairie", "quartz", "rabbit", "raven", "reef", "river", "sparrow",
    "spruce", "summit", "system", "thicket", "tiger", "tundra", "valley", "walrus", "willow",
    "wombat", "yak", "zephyr",
];

/// A small xorshift generator: names, not cryptography.
pub struct Rng(u64);

impl Rng {
    pub fn new() -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0x9E37_79B9_7F4A_7C15);
        Self((nanos ^ (u64::from(std::process::id()) << 32)) | 1)
    }

    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.0 = x;
        x
    }

    fn pick<'a>(&mut self, words: &[&'a str]) -> &'a str {
        let i = (self.next() % words.len() as u64) as usize;
        words[i]
    }
}

pub fn random_name(rng: &mut Rng) -> String {
    format!("{}-{}", rng.pick(ADJECTIVES), rng.pick(NOUNS))
}

/// Lowercase and collapse anything that isn't [a-z0-9] into single dashes.
pub fn normalize_name(name: &str) -> String {
    let mut out = String::new();
    let mut dash = false;
    for c in name.trim().to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
            dash = false;
        } else if !dash {
            out.push('-');
            dash = true;
        }
    }
    out.trim_matches('-').to_string()
}

/// Lowercase letters, digits and dashes (not leading/trailing), 1-100 chars.
pub fn validate_name(name: &str) -> Result<String, String> {
    let ok = !name.is_empty()
        && name.len() <= 100
        && name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        && !name.starts_with('-')
        && !name.ends_with('-');
    if ok {
        Ok(name.to_string())
    } else {
        Err(format!(
            "invalid repo name {name:?}: use lowercase letters, digits and dashes (not leading/trailing), max 100 chars"
        ))
    }
}

/// Python import name for a repo name: `quiet-otter` -> `quiet_otter`.
pub fn package_name(name: &str) -> String {
    let pkg = name.replace('-', "_");
    if pkg.chars().next().is_some_and(|c| c.is_ascii_digit()) {
        format!("_{pkg}")
    } else {
        pkg
    }
}

pub fn unique_random_name(is_taken: impl Fn(&str) -> bool) -> Result<String, String> {
    let mut rng = Rng::new();
    for _ in 0..25 {
        let candidate = random_name(&mut rng);
        if !is_taken(&candidate) {
            return Ok(candidate);
        }
    }
    Err("could not find a free random name in 25 attempts".to_string())
}

/// GitHub logins: alphanumerics and single inner hyphens, max 39 chars.
pub fn validate_owner(owner: &str) -> Result<String, String> {
    let bytes = owner.as_bytes();
    let ok = !bytes.is_empty()
        && bytes.len() <= 39
        && bytes.iter().all(|b| b.is_ascii_alphanumeric() || *b == b'-')
        && !owner.starts_with('-')
        && !owner.ends_with('-')
        && !owner.contains("--");
    if ok {
        Ok(owner.to_string())
    } else {
        Err(format!("invalid GitHub owner {owner:?}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes() {
        assert_eq!(normalize_name("  My Repo!! "), "my-repo");
        assert_eq!(normalize_name("a__b"), "a-b");
    }

    #[test]
    fn validates() {
        assert!(validate_name("quiet-otter").is_ok());
        assert!(validate_name("a").is_ok());
        assert!(validate_name("-a").is_err());
        assert!(validate_name("A").is_err());
        assert!(validate_owner("benpshore").is_ok());
        assert!(validate_owner("a--b").is_err());
    }

    #[test]
    fn packages() {
        assert_eq!(package_name("quiet-otter"), "quiet_otter");
        assert_eq!(package_name("1up"), "_1up");
    }

    #[test]
    fn random_names_are_well_formed() {
        let mut rng = Rng::new();
        for _ in 0..50 {
            assert!(validate_name(&random_name(&mut rng)).is_ok());
        }
    }
}
