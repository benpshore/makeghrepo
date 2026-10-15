// Trusted default-branch machinery. Never execute or check out PR contents.
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

export const REPOSITORY = "benpshore/makeghrepo";
// Immutable migration boundary, not recalculated from the latest open PR.
export const MIGRATION_CUTOFF = 178;
const MAX_ATTEMPTS = 3;
const MAX_PAGES = 100;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function require(condition, message) {
  if (!condition) throw new Error(message);
}

export function validatePolicy(policy) {
  const keys = ["schema", "repository", "mode", "migration_cutoff", "retained_pr", "task_issue"];
  require(policy && Object.keys(policy).sort().join() === keys.sort().join(), "Invalid policy fields");
  require(policy.schema === 1 && policy.repository === REPOSITORY, "Unexpected policy repository/schema");
  require(policy.migration_cutoff === MIGRATION_CUTOFF, "Migration cutoff must remain immutable");
  require(["audit", "enforce"].includes(policy.mode), "Invalid policy mode");
  for (const key of ["retained_pr", "task_issue"]) {
    require(policy[key] === null || (Number.isSafeInteger(policy[key]) && policy[key] > 0), `Invalid ${key}`);
  }
  require(policy.mode !== "enforce" || policy.task_issue !== null, "Enforcement needs a real task issue");
  return policy;
}

export function retryable(error) {
  return error?.status === 429 || error?.status >= 500 ||
    (error?.status === 403 && error?.retryAfter !== null && error?.retryAfter !== undefined) ||
    error?.name === "TypeError" || error?.name === "TimeoutError";
}

export async function retryRead(operation, sleep = pause) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await operation();
    } catch (error) {
      if (!retryable(error) || attempt + 1 === MAX_ATTEMPTS) throw error;
      await sleep(retryDelay(error, attempt));
    }
  }
}

function retryDelay(error, attempt) {
  const delay = error.retryAfter === null || error.retryAfter === undefined ? 0 : Number(error.retryAfter) * 1000;
  require(Number.isFinite(delay) && delay >= 0 && delay <= 30000, "Rate limit requires a later reconciliation");
  return Math.max(delay, 250 * 2 ** attempt);
}

function validatePull(pr) {
  require(pr && Number.isSafeInteger(pr.number) && pr.number > 0, "Invalid PR number");
  require(["open", "closed"].includes(pr.state), "Invalid PR state");
  require(pr.base?.repo?.full_name === REPOSITORY, "Unexpected PR repository");
  require(/^[a-f0-9]{40}$/.test(pr.head?.sha ?? ""), "Invalid PR head");
  require(typeof pr.created_at === "string" && Number.isFinite(Date.parse(pr.created_at)), "Invalid PR creation time");
  return pr;
}

export async function listOpen(api, sleep = pause) {
  const result = [];
  const seen = new Set();
  for (let page = 1; page <= MAX_PAGES; page++) {
    const response = await retryRead(() => api.listPage(page), sleep);
    require(Array.isArray(response.items) && response.items.length <= 100 && typeof response.hasNext === "boolean", "Invalid PR page");
    for (const pr of response.items) {
      validatePull(pr);
      require(pr.state === "open" && !seen.has(pr.number), "Unstable or duplicate PR pagination; retry later");
      seen.add(pr.number);
      result.push(pr); // Drafts and bots count, without exceptions.
    }
    if (!response.hasNext) return result.sort((a, b) => a.number - b.number);
    require(response.items.length > 0, "Empty intermediate PR page");
  }
  throw new Error("PR pagination exceeded bound; no partial reconciliation");
}

function fingerprint(pulls) {
  return JSON.stringify(pulls.map((pr) => [pr.number, pr.head.sha, pr.body, pr.updated_at]));
}

export async function stableSnapshot(api, sleep = pause) {
  let prior = await listOpen(api, sleep);
  for (let attempt = 1; attempt < MAX_ATTEMPTS; attempt++) {
    const current = await listOpen(api, sleep);
    if (fingerprint(prior) === fingerprint(current)) return current;
    prior = current;
  }
  throw new Error("Open PR inventory changed; retry reconciliation without writes");
}

export function chooseRetained(pulls, policy) {
  return pulls.find((pr) => pr.number === policy.retained_pr) ??
    [...pulls].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at) || a.number - b.number)[0] ?? null;
}

export function taskNumber(body) {
  // This repository already uses a task-labelled issue and a standalone Closes # line.
  // Ignore examples/comments instead of treating arbitrary mentions as task bindings.
  const text = (body ?? "").replace(/<!--[\s\S]*?-->/g, "").replace(/^\s*(```|~~~)[\s\S]*?^\s*\1[^\n]*$/gm, "");
  const lines = text.split(/\r?\n/).filter((line) => /^\s*(?:closes|fixes|resolves)\b/i.test(line));
  require(lines.length === 1, "PR needs exactly one standalone Closes #<task> binding");
  const match = /^Closes #([1-9][0-9]*)\s*$/i.exec(lines[0].trim());
  require(match && Number.isSafeInteger(Number(match[1])), "Use one same-repository Closes #<task> line");
  return Number(match[1]);
}

async function validateTask(api, pr, policy, sleep) {
  const number = taskNumber(pr.body);
  require(number === policy.task_issue, "Retained PR is not bound to the approved task issue");
  const issue = await retryRead(() => api.getIssue(number), sleep);
  require(issue.number === number && !issue.pull_request && issue.state === "open", "Task must be an open issue, not a PR");
  require(issue.labels?.some((label) => (typeof label === "string" ? label : label.name) === "task"), "Issue must use the existing task label");
}

async function getPull(api, number, sleep) {
  const pr = validatePull(await retryRead(() => api.getPull(number), sleep));
  require(pr.number === number, "PR readback number mismatch");
  return pr;
}

async function closeExtra(api, candidate, retained, policy, sleep, onAttempt) {
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    const current = await getPull(api, candidate.number, sleep);
    if (current.state === "closed") return "already-closed";
    require(current.head.sha === candidate.head.sha, `PR #${candidate.number} changed; retain new work and retry later`);
    require(current.number > MIGRATION_CUTOFF && current.number !== retained.number, "Refusing protected PR closure");
    const keeper = await getPull(api, retained.number, sleep);
    require(keeper.state === "open", "Retained PR closed during reconciliation; stop and recompute later");
    await validateTask(api, keeper, policy, sleep);
    let failure;
    try {
      onAttempt(attempt + 1);
      await api.closePull(current.number);
    } catch (error) {
      failure = error;
    }
    // A timeout/5xx can follow a successful server-side close. Read before retrying.
    const observed = await getPull(api, current.number, sleep);
    if (observed.state === "closed") return failure ? "confirmed-after-uncertain-response" : "closed";
    if (failure && !retryable(failure)) throw failure;
    require(attempt + 1 < MAX_ATTEMPTS, `Closure of #${current.number} remains unconfirmed; retry later`);
    await sleep(retryDelay(failure ?? {}, attempt));
  }
}

export async function reconcile({ api, policy, apply = false, sleep = pause, report = () => {} }) {
  validatePolicy(policy);
  const pulls = await stableSnapshot(api, sleep);
  const retained = chooseRetained(pulls, policy);
  const result = {
    mode: policy.mode,
    applied: apply && policy.mode === "enforce",
    retained: retained?.number ?? null,
    open: pulls.map((pr) => pr.number),
    protected: pulls.filter((pr) => pr.number <= MIGRATION_CUTOFF && pr.number !== retained?.number).map((pr) => pr.number),
    candidates: pulls.filter((pr) => pr.number > MIGRATION_CUTOFF && pr.number !== retained?.number).map((pr) => pr.number),
    effects: [],
  };
  report(result);
  if (!retained) return result; // Maximum one permits zero and never creates a PR.
  try {
    await validateTask(api, retained, policy, sleep);
  } catch (error) {
    result.binding_error = error.message;
    report(result);
    if (result.applied) throw error;
    return result;
  }
  if (!result.applied) return result;
  for (const number of result.candidates) {
    const candidate = pulls.find((pr) => pr.number === number);
    result.effects.push({ number, head_sha: candidate.head.sha, state: "planned" });
    report(result);
    const effect = result.effects.at(-1);
    try {
      effect.state = await closeExtra(api, candidate, retained, policy, sleep, (attempt) => {
        effect.state = "attempted";
        effect.attempt = attempt;
        report(result); // Durable Actions log before each effect; never log tokens/PR text.
      });
      report(result);
      const keeper = await getPull(api, retained.number, sleep);
      require(keeper.state === "open", "Retained PR closed after an effect; inspect the recorded closures before retrying");
    } catch (error) {
      effect.detail = error.message;
      report(result);
      throw error;
    }
  }
  result.remaining = (await stableSnapshot(api, sleep)).map((pr) => pr.number);
  result.needs_migration = result.remaining.length > 1;
  report(result);
  return result;
}

export function githubAPI(token, transport = fetch) {
  require(typeof token === "string" && token.length > 0, "Missing Actions token");
  async function request(path, method = "GET", body) {
    const response = await transport(`https://api.github.com/repos/${REPOSITORY}/${path}`, {
      method,
      redirect: "error",
      signal: AbortSignal.timeout(30000),
      headers: { authorization: `Bearer ${token}`, accept: "application/vnd.github+json", "x-github-api-version": "2022-11-28", "content-type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      const error = new Error(`GitHub ${method} ${path.split("?")[0]} returned ${response.status}`);
      error.status = response.status;
      error.retryAfter = response.headers.get("retry-after");
      throw error;
    }
    return { data: await response.json(), next: /;\s*rel="next"/.test(response.headers.get("link") ?? "") };
  }
  const positive = (number) => {
    require(Number.isSafeInteger(number) && number > 0, "Invalid API number");
    return number;
  };
  return {
    listPage: async (page) => {
      const response = await request(`pulls?state=open&sort=created&direction=asc&per_page=100&page=${positive(page)}`);
      return { items: response.data, hasNext: response.next };
    },
    getPull: async (number) => (await request(`pulls/${positive(number)}`)).data,
    getIssue: async (number) => (await request(`issues/${positive(number)}`)).data,
    closePull: async (number) => (await request(`pulls/${positive(number)}`, "PATCH", { state: "closed" })).data,
  };
}

export async function main(env = process.env) {
  require(env.GITHUB_ACTIONS === "true" && env.GITHUB_REPOSITORY === REPOSITORY, "Run only in this repository's Actions workflow");
  const event = JSON.parse(await readFile(env.GITHUB_EVENT_PATH, "utf8"));
  require(["pull_request_target", "schedule", "workflow_dispatch"].includes(env.GITHUB_EVENT_NAME), "Unsupported guard event");
  const branch = event.repository?.default_branch;
  require(event.repository?.full_name === REPOSITORY && branch && env.GITHUB_REF === `refs/heads/${branch}`, "Guard requires the trusted default-branch ref");
  if (env.GITHUB_EVENT_NAME === "pull_request_target") {
    require(event.pull_request?.base?.ref === branch && event.pull_request?.base?.repo?.full_name === REPOSITORY, "Unexpected PR base");
  }
  const policy = JSON.parse(await readFile(new URL("./policy.json", import.meta.url), "utf8"));
  const result = await reconcile({ api: githubAPI(env.GH_TOKEN), policy, apply: env.ONE_OPEN_PR_APPLY === "1", report: (entry) => console.log(JSON.stringify(entry)) });
  console.log(JSON.stringify(result));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => { console.error(error.message); process.exitCode = 1; });
}
