const { ghFetch, getRepoParts, getBranch } = require("./_github");
const { readJsonBody, requirePassword } = require("./_security");

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "Method Not Allowed" });
    return;
  }

  try {
    const body = await readJsonBody(req);
    if (!requirePassword(req, res, body)) return;

    const { owner, repo } = getRepoParts();
    const branch = getBranch();
    await ghFetch(`/repos/${owner}/${repo}/actions/workflows/weekly_mail.yml/dispatches`, {
      method: "POST",
      body: JSON.stringify({
        ref: branch,
        inputs: { send_only: "true" },
      }),
    });

    res.status(200).json({ ok: true, message: "已触发立即发送任务。" });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message ? e.message : e) });
  }
};

