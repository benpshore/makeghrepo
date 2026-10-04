# Owner pushes

Public personal repositories use two rulesets on the default branch:

| Ruleset | Rules | Bypass |
| --- | --- | --- |
| `protect-main` | No deletion, force-push, or merge commits; any additional existing restrictions stay here | None |
| `require-pr-and-ci` | Squash PR, resolved review threads, up-to-date passing GitHub Actions `ci`; zero required approvals | Exact repository owner: `User`, numeric owner ID, `always` |

The owner can push their own work without opening a PR or waiting for CI. CI
still runs on pushes and PRs. Existing release validation is unchanged; Python
auto-releases still wait for CI. Secret scanning and push protection are unchanged.
Local bootstrap checks still run before makeghrepo publishes a new repository.

GitHub authorizes the **pushing account**, not the commit's author name or email.
An agent using the owner's credentials has the same server-side bypass. Generated
`AGENTS.md` therefore requires agents to use branches, PRs, and CI even under that
account. For server-enforced separation, use a distinct GitHub App or account for
agent pushes, without bypass access. No credential setup is performed by makeghrepo.

GitHub supports individual `User` bypass actors on repository rulesets; see its
[announcement](https://github.blog/changelog/2026-05-07-repository-rulesets-user-bypass-and-branch-renaming/)
and [REST schema](https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset).
makeghrepo resolves the personal owner's ID from repository metadata. It refuses
an organization or unverified owner rather than exempting all administrators.
Private repositories retain the existing GitHub Free behavior: no rulesets,
Actions disabled, and local checks only.

## Existing repositories

Installing or merging this code does **not** update live rules. After review and
approval of the policy change, upgrade the existing uv tool installation and
rerun `makeghrepo NAME` for a project it created. A rerun reapplies configuration
but never pushes subsequent local commits. A failed remote-branch lookup stops
the rerun before configuration or a push; restore access and retry. Copier and
the Rust distribution are retained; both implementations use the same policy
contract.

Migration creates or updates `require-pr-and-ci` before removing PR/CI rules from
`protect-main`. Extra checks and PR parameters are copied intact, and unrelated
restrictions stay in the no-bypass ruleset. A failed write leaves the old gate in
place; rerunning completes the migration. Customized scopes, hidden or unexpected
bypasses, duplicate names, conflicting gate policies, or unrelated restrictions
inside `require-pr-and-ci` require manual review before any ruleset is written.
Other rulesets and classic branch protection are untouched and may still block
owner pushes; inspect those separately rather than deleting their requirements.

For a repository without makeghrepo's local resume marker, apply the reviewed
two-ruleset change directly in GitHub settings. Do not fabricate a marker or run
the bootstrap command against an unrelated checkout.

## Creation opt-outs

For public projects, `--no-ci` omits the required-status-check rule and `--no-pr`
omits the PR rule and its review policy. They are independent; passing both omits
the review ruleset entirely. History protections and security settings remain.
Workflows and local bootstrap checks are retained, and release workflows still
wait for CI. The owner's verified bypass applies to any remaining gate.

Resume restores the tool marker's original policy (legacy markers require both).
Conflicting flags, private projects, and invalid policy fields are refused. An
opt-out must never remove an existing live rule or migrate a history gate into an
owner-bypass gate; the read-only preflight refuses such configuration before any
settings write or publishing. Existing unrelated rules and stricter parameters
on the remaining rule are retained. These options do not update this repository's
own protections.
