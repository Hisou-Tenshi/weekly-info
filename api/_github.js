const GH_API = "https://api.github.com";

function requiredEnv(name) {
  const v = process.env[name];
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

function getRepoParts() {
  const repo = requiredEnv("GITHUB_REPO"); // owner/repo
  const [owner, name] = repo.split("/");
  if (!owner || !name) throw new Error("GITHUB_REPO must be owner/repo");
  return { owner, repo: name };
}

function getBranch() {
  return process.env.GITHUB_BRANCH || "main";
}

function getStatePath() {
  return process.env.STATE_PATH || "state.json";
}

function getSecureConfigPath() {
  return process.env.SECURE_CONFIG_PATH || "secure_config.json";
}

async function ghFetch(path, opts = {}) {
  const token = requiredEnv("GITHUB_TOKEN");
  const res = await fetch(`${GH_API}${path}`, {
    ...opts,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`GitHub API ${res.status}: ${text}`);
  }
  return res;
}

async function readStateFile() {
  const { owner, repo } = getRepoParts();
  const branch = getBranch();
  const path = getStatePath();

  const res = await ghFetch(
    `/repos/${owner}/${repo}/contents/${encodeURIComponent(path)}?ref=${encodeURIComponent(branch)}`
  );
  const data = await res.json();
  const content = Buffer.from(data.content || "", "base64").toString("utf8");
  const json = JSON.parse(content || "{}");
  return { json, sha: data.sha };
}

async function writeStateFile(nextJson, sha) {
  const { owner, repo } = getRepoParts();
  const branch = getBranch();
  const path = getStatePath();

  const body = {
    message: "chore: update state.json",
    content: Buffer.from(JSON.stringify(nextJson, null, 2) + "\n", "utf8").toString(
      "base64"
    ),
    branch,
    sha,
  };

  const res = await ghFetch(`/repos/${owner}/${repo}/contents/${encodeURIComponent(path)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
  return res.json();
}

module.exports = {
  readStateFile,
  writeStateFile,
  getRepoParts,
  getBranch,
  getSecureConfigPath,
  ghFetch,
};

