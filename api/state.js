const {
  readStateFile,
  ghFetch,
  getRepoParts,
  getBranch,
  getSecureConfigPath,
} = require("./_github");
const { requirePassword, decryptSecurePayload } = require("./_security");

function jstNow() {
  const now = new Date();
  // Date is in UTC internally; format/compute offsets manually when needed
  return now;
}

function nextTuesdayNoonJst(from = new Date()) {
  // Compute next Tue 12:00 JST, return as Date (UTC time)
  // JST = UTC+9
  const jstOffsetMs = 9 * 60 * 60 * 1000;
  const jst = new Date(from.getTime() + jstOffsetMs);
  const target = new Date(jst);
  target.setHours(12, 0, 0, 0);

  const day = target.getDay(); // Sun=0 Mon=1 Tue=2 ...
  const diffToTue = (2 - day + 7) % 7;
  let addDays = diffToTue;
  if (addDays === 0 && jst >= target) addDays = 7;
  target.setDate(target.getDate() + addDays);

  // Convert back to UTC date
  return new Date(target.getTime() - jstOffsetMs);
}

function nextWednesdayJst(runUtcDate) {
  const jstOffsetMs = 9 * 60 * 60 * 1000;
  const jst = new Date(runUtcDate.getTime() + jstOffsetMs);
  const day = jst.getDay(); // Wed=3
  const diffToWed = (3 - day + 7) % 7;
  const wed = new Date(jst);
  wed.setDate(wed.getDate() + diffToWed);
  wed.setHours(0, 0, 0, 0);
  return new Date(wed.getTime() - jstOffsetMs);
}

function parseIsoDateJst(isoDate) {
  // "YYYY-MM-DD" interpreted as JST midnight, returned as UTC Date
  const [y, m, d] = isoDate.split("-").map((x) => parseInt(x, 10));
  const jstOffsetMs = 9 * 60 * 60 * 1000;
  const utc = Date.UTC(y, m - 1, d, 0, 0, 0, 0) - jstOffsetMs;
  return new Date(utc);
}

function cycleWednesdayUtcForRun(runUtc, startWedUtc) {
  const jstOffsetMs = 9 * 60 * 60 * 1000;
  const runJst = new Date(runUtc.getTime() + jstOffsetMs);
  const runJstMidnight = new Date(runJst);
  runJstMidnight.setHours(0, 0, 0, 0);

  const startJst = new Date(startWedUtc.getTime() + jstOffsetMs);
  const startJstMidnight = new Date(startJst);
  startJstMidnight.setHours(0, 0, 0, 0);

  const diffDays = Math.floor(
    (runJstMidnight.getTime() - startJstMidnight.getTime()) / (24 * 3600 * 1000)
  );
  if (diffDays < 0) return null;

  // Weekly anchor: week_id = floor(diffDays / 7), wed = start_wed + 7*week_id
  const weekId = Math.floor(diffDays / 7);
  const weekWedUtc = new Date(startWedUtc.getTime() + weekId * 7 * 24 * 3600 * 1000);
  // normalize to JST midnight for display stability
  const weekWedJst = new Date(weekWedUtc.getTime() + jstOffsetMs);
  weekWedJst.setHours(0, 0, 0, 0);
  return new Date(weekWedJst.getTime() - jstOffsetMs);
}

function computeNextSend({ skip_weeks_remaining }, env) {
  const startWed = parseIsoDateJst(env.JC_START_WED || "2026-04-08");
  let remaining = Math.max(0, parseInt(skip_weeks_remaining || 0, 10) || 0);
  let run = nextTuesdayNoonJst(new Date());
  for (let i = 0; i < 260; i++) {
    if (remaining > 0) {
      remaining -= 1;
    } else {
      const weekWed = cycleWednesdayUtcForRun(run, startWed);
      if (weekWed) {
        // "event_wed" depends on rotate/hold:
        // - rotate week: this week's Wed (Tue+1 day)
        // - hold week: next week's Wed (Tue+8 days)
        const sendStep = Math.max(0, parseInt(env.JC_SEND_STEP || "0", 10) || 0);
        const isRotate = sendStep % 2 === 0;
        const eventWed = isRotate
          ? weekWed
          : new Date(weekWed.getTime() + 7 * 24 * 3600 * 1000);
        return { next_run_utc: run.toISOString(), event_wed_utc: eventWed.toISOString() };
      }
    }
    run = new Date(run.getTime() + 7 * 24 * 3600 * 1000);
  }
  return { next_run_utc: null, event_wed_utc: null };
}

async function readSecureDocStartWed(defaultStartWed) {
  try {
    const { owner, repo } = getRepoParts();
    const branch = getBranch();
    const path = getSecureConfigPath();
    const res = await ghFetch(
      `/repos/${owner}/${repo}/contents/${encodeURIComponent(path)}?ref=${encodeURIComponent(branch)}`
    );
    const data = await res.json();
    const content = Buffer.from(data.content || "", "base64").toString("utf8");
    const encrypted = JSON.parse(content || "{}");
    const plain = decryptSecurePayload(encrypted);
    return String(plain.jc_start_wed || defaultStartWed);
  } catch {
    return defaultStartWed;
  }
}

module.exports = async (req, res) => {
  try {
    if (!requirePassword(req, res, {})) return;
    const { json } = await readStateFile();
    const skip = Math.max(0, parseInt(json.skip_weeks_remaining || 0, 10) || 0);
    const sendStep = Math.max(0, parseInt(json.send_step || 0, 10) || 0);
    const jcStartWed = await readSecureDocStartWed("2026-04-08");
    const next = computeNextSend(
      { skip_weeks_remaining: skip },
      { ...process.env, JC_START_WED: jcStartWed, JC_SEND_STEP: String(sendStep) }
    );
    res.status(200).json({
      skip_weeks_remaining: skip,
      ...next,
    });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message ? e.message : e) });
  }
};

