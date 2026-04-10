const crypto = require("crypto");
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

function baseWednesdayUtcForRun(runUtc) {
  // "当周周三" = 自动触发周二的次日周三
  return nextWednesdayJst(runUtc);
}

function sortedMembersFromPlain(plain) {
  const raw = Array.isArray(plain?.jc_members) ? plain.jc_members : [];
  return raw
    .map((x) => String(x).trim())
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

function anchorSignature(plain) {
  const sorted = sortedMembersFromPlain(plain);
  const start = String(plain?.jc_start_wed || "2026-04-08");
  const anch = String(plain?.jc_anchor_presenter || "").trim();
  const raw = `${start}\n${anch}\n${sorted.join("\n")}`;
  return crypto.createHash("sha256").update(raw, "utf8").digest("hex");
}

function computeBootstrapPreview(stateJson, plain) {
  const sig = anchorSignature(plain);
  const sorted = sortedMembersFromPlain(plain);
  if (!sorted.length) {
    return {
      ...stateJson,
      members_queue: [],
      send_step: 0,
      last_presenter: null,
      bootstrapped_v1: true,
      anchor_sig: sig,
    };
  }
  const anchor = String(plain?.jc_anchor_presenter || "").trim();
  let rotated;
  let presenterDone;
  if (anchor && sorted.includes(anchor)) {
    const rest = sorted.filter((x) => x !== anchor);
    rotated = [...rest, anchor];
    presenterDone = anchor;
  } else {
    const first = sorted[0];
    rotated = sorted.length > 1 ? [...sorted.slice(1), first] : [...sorted];
    presenterDone = first;
  }
  return {
    ...stateJson,
    members_queue: rotated,
    send_step: 1,
    last_presenter: presenterDone,
    bootstrapped_v1: true,
    anchor_sig: sig,
  };
}

function bootstrapIfNeeded(stateJson, plain) {
  const sig = anchorSignature(plain);
  const st = stateJson || {};
  if (st.bootstrapped_v1 === true && st.anchor_sig === sig) {
    return { ...st };
  }
  return computeBootstrapPreview(st, plain);
}

function computeNextSend(stateJson, plain, nowUtc = new Date()) {
  const startWed = parseIsoDateJst(plain.jc_start_wed || "2026-04-08");
  let remaining = Math.max(0, parseInt(stateJson?.skip_weeks_remaining || 0, 10) || 0);
  let run = nextTuesdayNoonJst(nowUtc);
  const st = bootstrapIfNeeded(stateJson || {}, plain);
  const sendStep = Math.max(0, parseInt(st.send_step || 0, 10) || 0);
  const isRotate = sendStep % 2 === 0;
  const queue = Array.isArray(st.members_queue) ? st.members_queue : [];
  const presenter = queue.length ? String(queue[0]) : "";
  const mode = isRotate ? "rotate" : "hold";
  for (let i = 0; i < 260; i++) {
    if (remaining > 0) {
      remaining -= 1;
    } else {
      const baseWed = baseWednesdayUtcForRun(run);
      if (baseWed && baseWed.getTime() >= startWed.getTime()) {
        const eventWed = isRotate
          ? baseWed
          : new Date(baseWed.getTime() + 7 * 24 * 3600 * 1000);
        return {
          next_run_utc: run.toISOString(),
          event_wed_utc: eventWed.toISOString(),
          next_presenter: presenter,
          next_mode: mode,
        };
      }
    }
    run = new Date(run.getTime() + 7 * 24 * 3600 * 1000);
  }
  return { next_run_utc: null, event_wed_utc: null, next_presenter: presenter, next_mode: mode };
}

async function readSecureDocPlain(defaultStartWed) {
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
    return {
      jc_start_wed: String(plain.jc_start_wed || defaultStartWed),
      jc_members: Array.isArray(plain.jc_members) ? plain.jc_members : [],
      jc_anchor_presenter: String(plain.jc_anchor_presenter || "").trim(),
    };
  } catch {
    return { jc_start_wed: defaultStartWed, jc_members: [], jc_anchor_presenter: "" };
  }
}

module.exports = async (req, res) => {
  try {
    if (!requirePassword(req, res, {})) return;
    const { json } = await readStateFile();
    const skip = Math.max(0, parseInt(json.skip_weeks_remaining || 0, 10) || 0);
    const plain = await readSecureDocPlain("2026-04-08");
    const next = computeNextSend({ ...json, skip_weeks_remaining: skip }, plain, new Date());
    res.status(200).json({
      skip_weeks_remaining: skip,
      ...next,
    });
  } catch (e) {
    res.status(500).json({ error: String(e && e.message ? e.message : e) });
  }
};

