const { readStateFile, writeStateFile } = require("./_github");
const { readJsonBody, requirePassword } = require("./_security");

module.exports = async (req, res) => {
  if (req.method !== "POST") {
    res.status(405).json({ error: "Method Not Allowed" });
    return;
  }

  try {
    const body = await readJsonBody(req);
    if (!requirePassword(req, res, body)) return;
    const weeks = Math.max(0, parseInt(body.weeks || 0, 10) || 0);

    const { json, sha } = await readStateFile();
    const next = {
      ...(json || {}),
      skip_weeks_remaining: weeks,
    };
    await writeStateFile(next, sha);

    res.status(200).json({ ok: true, skip_weeks_remaining: weeks });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message ? e.message : e) });
  }
};

