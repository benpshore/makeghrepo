import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  MIGRATION_CUTOFF, REPOSITORY, chooseRetained, githubAPI, listOpen,
  main, reconcile, retryRead, stableSnapshot, taskNumber, validatePolicy,
} from "./reconcile.mjs";

const noSleep = async () => {};
const execution = { defaultBranch: "main", headSHA: "d".repeat(40) };
const policy = (overrides = {}) => ({
  schema: 1, repository: REPOSITORY, mode: "enforce", migration_cutoff: 178,
  retained_pr: 178, task_issue: 123, ...overrides,
});
const pull = (number, overrides = {}) => ({
  number, state: "open", body: "Closes #123", draft: false,
  user: { login: "someone", type: "User" },
  base: { repo: { full_name: REPOSITORY } }, head: { sha: "a".repeat(40) },
  created_at: new Date(Date.UTC(2026, 9, 1, 0, 0, number)).toISOString(),
  updated_at: "2026-10-15T00:00:00Z", ...overrides,
});
const httpError = (status, retryAfter = null) => Object.assign(new Error(`HTTP ${status}`), { status, retryAfter });

class FakeAPI {
  constructor(pulls) {
    this.pulls = structuredClone(pulls);
    this.calls = [];
    this.closed = [];
    this.issue = { number: 123, state: "open", labels: [{ name: "task" }] };
    this.repository = { full_name: REPOSITORY, default_branch: "main" };
    this.defaultHead = execution.headSHA;
    this.livePolicy = policy();
    this.beforeRead = () => {};
    this.onClose = (pr) => { pr.state = "closed"; };
  }
  async listPage(page) {
    this.calls.push(["page", page]);
    const all = this.pulls.filter((pr) => pr.state === "open");
    return { items: structuredClone(all.slice((page - 1) * 100, page * 100)), hasNext: all.length > page * 100 };
  }
  async getPull(number) {
    this.calls.push(["get", number]);
    this.beforeRead(number);
    const pr = this.pulls.find((pr) => pr.number === number);
    if (!pr) throw httpError(404);
    return structuredClone(pr);
  }
  async getIssue(number) {
    this.calls.push(["issue", number]);
    return structuredClone(this.issue);
  }
  async getRepository() {
    this.calls.push(["repository"]);
    return structuredClone(this.repository);
  }
  async getDefaultHead(branch) {
    this.calls.push(["default-head", branch]);
    return { ref: `refs/heads/${branch}`, object: { type: "commit", sha: this.defaultHead } };
  }
  async getPolicy(sha) {
    this.calls.push(["policy", sha]);
    return structuredClone(this.livePolicy);
  }
  async closePull(number) {
    this.calls.push(["close", number]);
    this.closed.push(number);
    this.onClose(this.pulls.find((pr) => pr.number === number));
  }
}

const run = (api, overrides = {}) => reconcile({ api, policy: policy(), execution, apply: true, sleep: noSleep, ...overrides });

test("reviewed policy starts disabled with an immutable cutoff and retained PR178", async () => {
  const checkedIn = JSON.parse(await readFile(new URL("./policy.json", import.meta.url)));
  assert.equal(validatePolicy(checkedIn), checkedIn);
  assert.equal(checkedIn.mode, "audit");
  assert.equal(checkedIn.retained_pr, 178);
  assert.equal(checkedIn.task_issue, null);
  assert.equal(MIGRATION_CUTOFF, 178);
  for (const changed of [policy({ migration_cutoff: 177 }), policy({ migration_cutoff: 179 }),
    policy({ repository: "other/repo" }), policy({ task_issue: null }), policy({ retained_pr: true }),
    { ...policy(), extra: true }]) assert.throws(() => validatePolicy(changed));
});

test("PR178 wins deterministically over older backlog and newer bot drafts", async () => {
  const api = new FakeAPI([pull(200, { draft: true, user: { login: "dependabot[bot]", type: "Bot" } }), pull(130), pull(178), pull(179)]);
  const result = await run(api);
  assert.equal(result.retained, 178);
  assert.deepEqual(result.protected, [130]);
  assert.deepEqual(api.closed, [179, 200]);
  assert.deepEqual(result.remaining, [130, 178]);
  assert.equal(result.needs_migration, true);
});

test("audit mode and explicit apply switch each prevent every write", async () => {
  for (const overrides of [{ policy: policy({ mode: "audit" }) }, { apply: false }]) {
    const api = new FakeAPI([pull(178), pull(200)]);
    assert.equal((await run(api, overrides)).applied, false);
    assert.deepEqual(api.closed, []);
  }
});

test("each closure rechecks the live default head and pinned policy immediately before PATCH", async () => {
  const api = new FakeAPI([pull(178), pull(200), pull(201)]);
  await run(api);
  for (const [index, call] of api.calls.entries()) {
    if (call[0] === "close") assert.deepEqual(api.calls.slice(index - 5, index), [
      ["repository"], ["default-head", "main"], ["policy", execution.headSHA],
      ["repository"], ["default-head", "main"],
    ]);
  }
});

test("queued and rerun executions from before a default-branch pause cannot close PRs", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  api.defaultHead = "e".repeat(40);
  api.livePolicy.mode = "audit";
  for (let delivery = 0; delivery < 2; delivery++) await assert.rejects(run(api), /Stale guard execution/);
  assert.deepEqual(api.closed, []);
});

test("live policy changes, default branch changes and missing execution provenance fail closed", async () => {
  for (const change of [
    (api) => { api.livePolicy.mode = "audit"; },
    (api) => { api.livePolicy.task_issue = 124; },
    (api) => { api.repository.default_branch = "new-default"; },
    (api) => { api.repository.full_name = "other/repo"; },
    (api) => { api.getDefaultHead = async () => ({ ref: "refs/tags/main", object: { type: "commit", sha: execution.headSHA } }); },
  ]) {
    const api = new FakeAPI([pull(178), pull(200)]);
    change(api);
    await assert.rejects(run(api));
    assert.deepEqual(api.closed, []);
  }
  for (const context of [undefined, {}, { ...execution, headSHA: "main" }]) {
    const api = new FakeAPI([pull(178), pull(200)]);
    await assert.rejects(run(api, { execution: context }), /execution provenance/);
    assert.deepEqual(api.closed, []);
  }
});

test("a default head changing while its policy is read prevents the close", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  const getPolicy = api.getPolicy.bind(api);
  api.getPolicy = async (sha) => {
    const saved = await getPolicy(sha);
    api.defaultHead = "e".repeat(40);
    return saved;
  };
  await assert.rejects(run(api), /Stale guard execution/);
  assert.deepEqual(api.closed, []);
});

test("a pause between closures or before a retry stops all later PATCH requests", async () => {
  for (const uncertain of [false, true]) {
    const api = new FakeAPI([pull(178), pull(200), pull(201)]);
    api.onClose = (pr) => {
      api.defaultHead = "e".repeat(40);
      api.livePolicy.mode = "audit";
      if (uncertain) throw httpError(502);
      pr.state = "closed";
    };
    await assert.rejects(run(api), /Stale guard execution/);
    assert.deepEqual(api.closed, [200]);
  }
});

test("unreadable live authorization never falls back to the event-time policy", async () => {
  for (const method of ["getRepository", "getDefaultHead", "getPolicy"]) {
    for (const status of [403, 404, 503]) {
      const api = new FakeAPI([pull(178), pull(200)]);
      let calls = 0;
      api[method] = async () => { calls++; throw httpError(status); };
      await assert.rejects(run(api), new RegExp(String(status)));
      assert.equal(calls, status === 503 ? 3 : 1);
      assert.deepEqual(api.closed, []);
    }
  }
});

test("zero PRs is permitted and one future PR is retained without a creation ban", async () => {
  const empty = new FakeAPI([]);
  assert.equal((await run(empty)).retained, null);
  assert.deepEqual(empty.closed, []);
  const single = new FakeAPI([pull(200)]);
  assert.equal((await run(single)).retained, 200);
  assert.deepEqual(single.closed, []);
});

test("fallback retention is stable by creation date then number, independent of page order", () => {
  const same = "2026-10-15T00:00:00Z";
  assert.equal(chooseRetained([pull(201, { created_at: same }), pull(200, { created_at: same })], policy()).number, 200);
});

test("complete pagination includes all bot drafts beyond the first 100", async () => {
  const prs = [pull(178), ...Array.from({ length: 204 }, (_, i) => pull(200 + i, { draft: true, user: { type: "Bot" } }))];
  const api = new FakeAPI(prs);
  const result = await run(api);
  assert.equal(result.candidates.length, 204);
  assert.equal(api.closed.length, 204);
  assert.deepEqual(result.remaining, [178]);
  assert.ok(api.calls.some(([kind, page]) => kind === "page" && page === 3));
});

test("partial pagination errors cannot close any PR", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  api.listPage = async (page) => {
    if (page === 1) return { items: [pull(178), pull(200)], hasNext: true };
    throw httpError(403);
  };
  await assert.rejects(run(api), /403/);
  assert.deepEqual(api.closed, []);
});

test("duplicate pages and unstable snapshots fail before mutations", async () => {
  const duplicate = new FakeAPI([]);
  duplicate.listPage = async () => ({ items: [pull(178)], hasNext: true });
  await assert.rejects(listOpen(duplicate, noSleep), /duplicate/);
  const changing = new FakeAPI([]);
  let count = 0;
  changing.listPage = async () => ({ items: [pull(200 + count++)], hasNext: false });
  await assert.rejects(run(changing), /inventory changed/);
  assert.deepEqual(changing.closed, []);
});

test("an inventory that stabilizes is recovered within the bounded read retries", async () => {
  const api = new FakeAPI([pull(178)]);
  let count = 0;
  api.listPage = async () => ({ items: count++ ? [pull(178), pull(200)] : [pull(178)], hasNext: false });
  assert.deepEqual((await stableSnapshot(api, noSleep)).map((pr) => pr.number), [178, 200]);
});

test("transient read errors back off; permission errors and retry exhaustion fail closed", async () => {
  let attempts = 0;
  const delays = [];
  assert.equal(await retryRead(async () => {
    if (++attempts < 3) throw httpError(503);
    return "ok";
  }, async (ms) => delays.push(ms)), "ok");
  assert.deepEqual(delays, [250, 500]);
  attempts = 0;
  await assert.rejects(retryRead(async () => { attempts++; throw httpError(403); }, noSleep), /403/);
  assert.equal(attempts, 1);
  attempts = 0;
  await assert.rejects(retryRead(async () => { attempts++; throw httpError(503); }, noSleep), /503/);
  assert.equal(attempts, 3);
});

test("rate limits honor a bounded Retry-After and defer long waits", async () => {
  let attempts = 0;
  const waits = [];
  await retryRead(async () => { if (!attempts++) throw httpError(429, "2"); }, async (ms) => waits.push(ms));
  assert.deepEqual(waits, [2000]);
  await assert.rejects(retryRead(async () => { throw httpError(403, "60"); }, noSleep), /later reconciliation/);
});

test("a lost close response is read back before any retry", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  api.onClose = (pr) => { pr.state = "closed"; throw new TypeError("connection lost"); };
  const result = await run(api);
  assert.deepEqual(api.closed, [200]);
  assert.equal(result.effects[0].state, "confirmed-after-uncertain-response");
  assert.deepEqual(result.effects[0].readbacks.at(-1), {
    attempt: 1, phase: "post-close", state: "closed", head_sha: "a".repeat(40),
  });
});

test("post-close head races are recorded and stop subsequent effects even after a lost response", async () => {
  for (const outcome of ["closed", "closed-lost-response", "open-transient-error"]) {
    const api = new FakeAPI([pull(178), pull(200), pull(201)]);
    api.onClose = (pr) => {
      pr.head.sha = "b".repeat(40); // New work arrived after the preflight read.
      if (outcome.startsWith("closed")) pr.state = "closed";
      if (outcome === "closed-lost-response") throw new TypeError("connection lost");
      if (outcome === "open-transient-error") throw httpError(502);
    };
    const records = [];
    await assert.rejects(run(api, { report: (r) => records.push(structuredClone(r)) }), /head changed during closure/);
    assert.deepEqual(api.closed, [200]);
    const effect = records.at(-1).effects[0];
    assert.equal(records.at(-1).effects.length, 1);
    assert.equal(effect.state, "head-mismatch");
    assert.equal(effect.head_sha, "a".repeat(40));
    assert.deepEqual(effect.readbacks.at(-1), { attempt: 1, phase: "post-close",
      state: outcome.startsWith("closed") ? "closed" : "open", head_sha: "b".repeat(40) });
  }
});

test("unavailable post-close readback preserves uncertainty and stops further effects", async () => {
  const api = new FakeAPI([pull(178), pull(200), pull(201)]);
  api.beforeRead = (number) => { if (number === 200 && api.closed.length) throw httpError(403); };
  const records = [];
  await assert.rejects(run(api, { report: (r) => records.push(structuredClone(r)) }), /403/);
  assert.deepEqual(api.closed, [200]);
  const effect = records.at(-1).effects[0];
  assert.equal(effect.state, "attempted");
  assert.equal(effect.readbacks.length, 1);
  assert.equal(effect.readbacks[0].phase, "preflight");
  assert.equal(effect.authorized_head_sha, execution.headSHA);
});

test("retrying an unapplied transient close revalidates retained PR and task", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  api.onClose = (pr) => { if (api.closed.length === 1) throw httpError(502); pr.state = "closed"; };
  await run(api);
  assert.deepEqual(api.closed, [200, 200]);
  assert.ok(api.calls.filter(([kind, number]) => kind === "get" && number === 178).length >= 3);
});

test("a permanent close failure stops rather than retrying blindly", async () => {
  const api = new FakeAPI([pull(178), pull(200), pull(201)]);
  api.onClose = () => { throw httpError(403); };
  await assert.rejects(run(api), /403/);
  assert.deepEqual(api.closed, [200]);
});

test("repeated unconfirmed closes leave a bounded, inspectable effect record", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  api.onClose = () => {};
  const records = [];
  await assert.rejects(run(api, { report: (r) => records.push(structuredClone(r)) }), /unconfirmed/);
  assert.equal(api.closed.length, 3);
  assert.equal(records.at(-1).effects[0].state, "attempted");
  assert.match(records.at(-1).effects[0].detail, /unconfirmed/);
});

test("retained PR closing before a write stops all closure requests", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  api.beforeRead = (number) => { if (number === 178) api.pulls[0].state = "closed"; };
  await assert.rejects(run(api), /Retained PR closed/);
  assert.deepEqual(api.closed, []);
});

test("retained PR closing after an effect stops later closures and records recovery targets", async () => {
  const api = new FakeAPI([pull(178), pull(200), pull(201)]);
  api.onClose = (pr) => { pr.state = "closed"; api.pulls[0].state = "closed"; };
  const records = [];
  await assert.rejects(run(api, { report: (r) => records.push(structuredClone(r)) }), /after an effect/);
  assert.deepEqual(api.closed, [200]);
  assert.equal(records.at(-1).effects[0].number, 200);
  assert.equal(records.at(-1).effects[0].state, "closed");
});

test("new commits arriving after the inventory are retained for another reconciliation", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  api.beforeRead = (number) => { if (number === 200) api.pulls[1].head.sha = "b".repeat(40); };
  await assert.rejects(run(api), /changed; retain new work/);
  assert.deepEqual(api.closed, []);
});

test("missing PR readback never implies it is closed or safe to replace", async () => {
  const api = new FakeAPI([pull(178), pull(200)]);
  api.getPull = async () => { throw httpError(404); };
  await assert.rejects(run(api), /404/);
  assert.deepEqual(api.closed, []);
});

test("serialized concurrent event deliveries converge and retries are idempotent", async () => {
  const api = new FakeAPI([pull(178), pull(200), pull(201)]);
  // The workflow's repository-scoped queue supplies this serialization in Actions.
  let queue = Promise.resolve();
  const dispatch = () => (queue = queue.then(() => run(api)));
  const results = await Promise.all([dispatch(), dispatch(), dispatch()]);
  assert.deepEqual(api.closed, [200, 201]);
  assert.deepEqual(results.map((r) => r.remaining), [[178], [178], [178]]);
});

test("a timer sweep finds a bot PR even when its creation event was suppressed", async () => {
  const api = new FakeAPI([pull(178)]);
  await run(api);
  api.pulls.push(pull(202, { draft: true, user: { type: "Bot", login: "github-actions[bot]" } }));
  await run(api); // No event payload PR number is needed.
  assert.deepEqual(api.closed, [202]);
});

test("task bindings use the existing Closes # convention, without guessing ticket IDs", () => {
  assert.equal(taskNumber("## What\nCloses #123\n"), 123);
  assert.equal(taskNumber("<!-- Closes #99 -->\nCloses #123"), 123);
  assert.equal(taskNumber("```md\nCloses #99\n```\nCloses #123"), 123);
  for (const body of ["", "Closes #", "Closes #1\nCloses #2", "Closes #1, #2", "Fixes #1", "Closes other/repo#1", "Closes #0", "Closes #9007199254740992"]) {
    assert.throws(() => taskNumber(body));
  }
});

test("indented Markdown code examples cannot supply task bindings", () => {
  for (const indent of ["    ", "     ", "\t", " \t", "   \t", "\t "]) {
    assert.throws(() => taskNumber(`${indent}Closes #123`));
    assert.equal(taskNumber(`${indent}Closes #99\r\n\r\nCloses #123`), 123);
  }
  assert.equal(taskNumber("   Closes #123\r\n"), 123);
});

test("unclosed and nested Markdown fences cannot provide a binding", () => {
  for (const body of ["```md\nCloses #123", "~~~\nCloses #123", "````md\n```\nCloses #123\n````"]) {
    assert.throws(() => taskNumber(body));
  }
  assert.equal(taskNumber("  ~~~~md\nCloses #99\n  ~~~~\nCloses #123"), 123);
});

test("unterminated HTML comments hide task examples through end of body", () => {
  for (const body of ["<!--\nCloses #123", "<!-- example\r\nCloses #123\r\n"]) {
    assert.throws(() => taskNumber(body), /binding/);
  }
  assert.equal(taskNumber("Closes #123\n<!--\nCloses #99"), 123);
});

test("a task binding hidden in an unterminated comment prevents every mutation", async () => {
  const api = new FakeAPI([pull(178, { body: "<!--\nCloses #123" }), pull(200), pull(201)]);
  const records = [];
  await assert.rejects(run(api, { report: (r) => records.push(structuredClone(r)) }), /binding/);
  assert.deepEqual(api.closed, []);
  assert.deepEqual(records.at(-1).effects, []);
  assert.match(records.at(-1).binding_error, /binding/);
});

test("missing, conflicting, closed and non-task ticket bindings prevent mutations", async () => {
  for (const change of [
    (api) => { api.pulls[0].body = "No bound ticket"; },
    (api) => { api.pulls[0].body = "Closes #124"; },
    (api) => { api.issue.state = "closed"; },
    (api) => { api.issue.pull_request = {}; },
    (api) => { api.issue.labels = []; },
  ]) {
    const api = new FakeAPI([pull(178), pull(200)]);
    change(api);
    await assert.rejects(run(api));
    assert.deepEqual(api.closed, []);
  }
  const audit = new FakeAPI([pull(178, { body: "" }), pull(200)]);
  assert.match((await run(audit, { policy: policy({ mode: "audit", task_issue: null }) })).binding_error, /binding/);
  assert.deepEqual(audit.closed, []);
});

test("HTTP adapter has only fixed repository reads and a close-only PATCH", async () => {
  const calls = [];
  const api = githubAPI("SYNTHETIC_TEST_TOKEN", async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify([]), { headers: { link: '<https://untrusted.invalid/>; rel="next"' } });
  });
  assert.equal((await api.listPage(1)).hasNext, true);
  await api.listPage(2);
  await api.getPull(178);
  await api.getIssue(123);
  await api.closePull(200);
  assert.ok(calls.every(({ url, options }) => url.startsWith(`https://api.github.com/repos/${REPOSITORY}/`) && options.redirect === "error"));
  assert.equal(calls.at(-1).options.method, "PATCH");
  assert.deepEqual(JSON.parse(calls.at(-1).options.body), { state: "closed" });
  await assert.rejects(api.getPull("../settings"));
});

test("HTTP live authorization reads an exact default ref and commit-pinned policy as data", async () => {
  const calls = [];
  const content = Buffer.from(JSON.stringify(policy()));
  const api = githubAPI("SYNTHETIC_TEST_TOKEN", async (url, options) => {
    calls.push({ url, options });
    const data = url.includes("/contents/") ? {
      type: "file", encoding: "base64", size: content.length, content: content.toString("base64"),
      download_url: "https://untrusted.invalid/never-follow-this",
    } : {};
    return new Response(JSON.stringify(data));
  });
  await api.getRepository();
  await api.getDefaultHead("release/main");
  assert.deepEqual(await api.getPolicy(execution.headSHA), policy());
  assert.deepEqual(calls.map(({ url }) => url), [
    `https://api.github.com/repos/${REPOSITORY}`,
    `https://api.github.com/repos/${REPOSITORY}/git/ref/heads/release%2Fmain`,
    `https://api.github.com/repos/${REPOSITORY}/contents/.github/one-open-pr/policy.json?ref=${execution.headSHA}`,
  ]);
  assert.ok(calls.every(({ options }) => options.method === "GET" && options.redirect === "error"));
  await assert.rejects(api.getPolicy("main"), /exact commit/);
  await assert.rejects(api.getDefaultHead(""), /Invalid default branch/);
  assert.equal(calls.length, 3);
});

test("malformed and oversized live policy files fail closed", async () => {
  const content = Buffer.from(JSON.stringify(policy()));
  const valid = { type: "file", encoding: "base64", size: content.length, content: content.toString("base64") };
  for (const change of [{ type: "symlink" }, { encoding: "none" }, { size: 16385 },
    { content: "x".repeat(32769) }, { size: content.length - 1 }, { size: 1, content: "ew==" }]) {
    const api = githubAPI("SYNTHETIC_TEST_TOKEN", async () => new Response(JSON.stringify({ ...valid, ...change })));
    await assert.rejects(api.getPolicy(execution.headSHA));
  }
});

test("workflow trust, concurrency, bot recovery and disabled rollout are explicit", async () => {
  const workflow = await readFile(new URL("../workflows/one-open-pr.yml", import.meta.url), "utf8");
  const tests = await readFile(new URL("../workflows/one-open-pr-tests.yml", import.meta.url), "utf8");
  const ci = await readFile(new URL("../workflows/ci.yml", import.meta.url), "utf8");
  assert.match(workflow, /pull_request_target:\n\s+branches: \[main\]/);
  assert.match(workflow, /group: one-open-pr-\$\{\{ github.repository_id \}\}/);
  assert.match(workflow, /cancel-in-progress: false\n\s+queue: max/);
  assert.match(workflow, /github.ref == format\('refs\/heads\/\{0\}', github.event.repository.default_branch\)/);
  assert.match(workflow, /ref: \$\{\{ github.sha \}\}/);
  assert.match(workflow, /persist-credentials: false/);
  assert.match(workflow, /schedule:[\s\S]*cron: "17 \* \* \* \*"/);
  assert.match(workflow, /workflow_dispatch:/);
  assert.match(workflow, /ONE_OPEN_PR_APPLY: "0"/);
  assert.doesNotMatch(workflow, /github\.event\.pull_request\.head\.(ref|sha)|github\.actor\s*[!=]=/);
  assert.match(tests, /permissions:\n\s+contents: read/);
  assert.doesNotMatch(tests, /: write|GH_TOKEN|secrets\./);
  assert.match(ci, /needs: \[.*one-open-pr-tests\]/);
  assert.match(ci, /uses: \.\/\.github\/workflows\/one-open-pr-tests.yml/);
  for (const source of [workflow, tests]) {
    for (const action of source.matchAll(/uses: (\S+)/g)) assert.match(action[1], /@[a-f0-9]{40}$/);
  }
});

test("entrypoint refuses local or unrelated repository execution", async () => {
  await assert.rejects(main({}), /only in this repository/);
  await assert.rejects(main({ GITHUB_ACTIONS: "true", GITHUB_REPOSITORY: "other/repo" }), /only in this repository/);
});

test("entrypoint rejects non-default manual refs before token or API use", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "one-pr-event-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "event.json");
  await writeFile(path, JSON.stringify({ repository: { full_name: REPOSITORY, default_branch: "main" } }));
  await assert.rejects(main({ GITHUB_ACTIONS: "true", GITHUB_REPOSITORY: REPOSITORY,
    GITHUB_EVENT_PATH: path, GITHUB_EVENT_NAME: "workflow_dispatch", GITHUB_REF: "refs/heads/untrusted" }), /trusted default-branch ref/);
});

test("entrypoint rejects alternate PR bases and ordinary PR events before API use", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "one-pr-event-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "event.json");
  await writeFile(path, JSON.stringify({ repository: { full_name: REPOSITORY, default_branch: "main" },
    pull_request: { base: { ref: "untrusted", repo: { full_name: REPOSITORY } } } }));
  const env = { GITHUB_ACTIONS: "true", GITHUB_REPOSITORY: REPOSITORY, GITHUB_EVENT_PATH: path,
    GITHUB_EVENT_NAME: "pull_request_target", GITHUB_REF: "refs/heads/main" };
  await assert.rejects(main(env), /Unexpected PR base/);
  await assert.rejects(main({ ...env, GITHUB_EVENT_NAME: "pull_request" }), /Unsupported guard event/);
});
