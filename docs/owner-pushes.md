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

## Organizations

`makeghrepo NAME --owner ORG` (or `MAKEGHREPO_OWNER=ORG`) creates the repository
in an organization. makeghrepo resolves the owner's type from authenticated GitHub
metadata (`gh api users/ORG`) before any effect: the caller's own login is the
personal path above, another user's account is refused, and an organization takes
this path. The push identity and commit identity remain the authenticated user.

| Ruleset | Rules | Bypass |
| --- | --- | --- |
| `protect-main` | Unchanged | None |
| `require-pr-and-ci` | Unchanged | **None** |

Nobody bypasses the gate in an organization repository: not organization owners,
not repository admins, not apps. The personal exception exists only because a
single account cannot tell its human apart from its agents; an organization can
hand agents a GitHub App or a member account with no bypass, which is the
server-enforced separation the personal section asks for. Generated `README.md`
and `AGENTS.md` say so instead of describing an owner exception. Private
organization repositories keep the GitHub Free behavior (no rulesets, Actions
disabled, local checks only); GitHub Free offers rulesets on public repositories
only, whoever owns them.

A personal repository transferred into an organization keeps its `User` bypass.
A rerun refuses that repository as a customized ruleset rather than silently
narrowing the bypass; remove the bypass actor in the repository's ruleset
settings first if the organization policy is wanted there. Whether the
authenticated user may create a repository of the requested visibility in the
organization is checked by GitHub at creation time, not predicted by makeghrepo;
a refusal stops the run before any push. Organization project boards are looked
up with the organization GraphQL query; linking still needs the `project` scope.
This implements the owner/organization resolution and bypass-free organization
policy parts of roadmap items 19, 22 and 29. Still open from those items: a
pre-flight capability check for organization repository creation and visibility,
and owner/visibility-organized daughter directories.

## Existing repositories

Installing or merging this code does **not** update live rules. After review and
approval of the policy change, upgrade the existing uv tool installation and
rerun `makeghrepo NAME` for a project it created. A rerun reapplies configuration
but never pushes subsequent local commits. A failed remote-branch lookup stops
the rerun before configuration or a push; restore access and retry. Copier is
retained. The parked Rust implementation (`rust` branch, #150) used the same
policy contract.

Migration creates or updates `require-pr-and-ci` before removing PR/CI rules from
`protect-main`. Extra checks and PR parameters are copied intact, and unrelated
restrictions stay in the no-bypass ruleset. Python independently reads back the replacement gate before removing the legacy
gate. A failed write or ineffective replacement leaves the old gate in place;
rerunning completes a valid partial migration. Customized scopes, hidden or unexpected
bypasses, duplicate names, conflicting gate policies, or unrelated restrictions
inside `require-pr-and-ci` require manual review before any ruleset is written.
Other rulesets and classic branch protection are untouched and may still block
owner pushes; inspect those separately rather than deleting their requirements.

For a repository without makeghrepo's local resume marker, apply the reviewed
two-ruleset change directly in GitHub settings. Do not fabricate a marker or run
the bootstrap command against an unrelated checkout.


## Python publication gate

Python waits for all required setup and independent effective-state readbacks
before its first push, including the configured two-ruleset policy. The existing
personal-repository migration installs the exact owner's configured bypass; it
does not preserve a previously stricter no-bypass review rule unchanged. The
organization path adds no bypass. This repair does not broaden either policy.
Rust remains parked on its separate branch and lacks this readback gate.
Shared policy defaults and renderer tests establish neither ordering nor recovery
parity; see [bootstrap publication](bootstrap-publication.md).
