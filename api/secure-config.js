const {
  ghFetch,
  getRepoParts,
  getBranch,
  getSecureConfigPath,
} = require("./_github");
const {
  readJsonBody,
  requirePassword,
  encryptSecurePayload,
  decryptSecurePayload,
} = require("./_security");

async function readSecureDoc() {
  const { owner, repo } = getRepoParts();
  const branch = getBranch();
  const path = getSecureConfigPath();
  const res = await ghFetch(
    `/repos/${owner}/${repo}/contents/${encodeURIComponent(path)}?ref=${encodeURIComponent(branch)}`
  );
  const data = await res.json();
  const content = Buffer.from(data.content || "", "base64").toString("utf8");
  const json = JSON.parse(content || "{}");
  return { json, sha: data.sha };
}

async function writeSecureDoc(next, sha) {
  const { owner, repo } = getRepoParts();
  const branch = getBranch();
  const path = getSecureConfigPath();
  const body = {
    message: "chore: update secure_config.json",
    content: Buffer.from(JSON.stringify(next, null, 2) + "\n", "utf8").toString("base64"),
    branch,
    sha,
  };
  const res = await ghFetch(`/repos/${owner}/${repo}/contents/${encodeURIComponent(path)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
  return res.json();
}

module.exports = async (req, res) => {
  try {
    const body = req.method === "POST" ? await readJsonBody(req) : {};
    if (!requirePassword(req, res, body)) return;

    if (req.method === "GET") {
      const { json } = await readSecureDoc();
      const plain = decryptSecurePayload(json || {});
      res.status(200).json({
        jc_members: plain.jc_members || [],
        jc_template: plain.jc_template || "",
        jc_start_wed: plain.jc_start_wed || "2026-04-08",
        jc_subject: plain.jc_subject || "",
      });
      return;
    }

    if (req.method === "POST") {
      const jc_members = Array.isArray(body.jc_members)
        ? body.jc_members.map((x) => String(x).trim()).filter(Boolean)
        : [];
      const jc_template = String(body.jc_template || "");
      const jc_start_wed = String(body.jc_start_wed || "2026-04-08");
      const jc_subject = String(body.jc_subject || "");
      const payload = { jc_members, jc_template, jc_start_wed, jc_subject };
      const encrypted = encryptSecurePayload(payload);
      const { sha } = await readSecureDoc();
      await writeSecureDoc(encrypted, sha);
      res.status(200).json({ ok: true });
      return;
    }

    res.status(405).json({ error: "Method Not Allowed" });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message ? e.message : e) });
  }
};

