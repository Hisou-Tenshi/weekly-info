const crypto = require("crypto");

const attempts = new Map();
const MAX_FAILURES = 5;
const LOCK_MS = 10 * 60 * 1000;
const BASE_DELAY_MS = 800;
const MAX_DELAY_MS = 12_000;

function getClientId(req) {
  const fwd = String(req.headers["x-forwarded-for"] || "");
  const ip = fwd.split(",")[0].trim() || req.socket?.remoteAddress || "unknown";
  return `${ip}`;
}

function nowMs() {
  return Date.now();
}

function cleanExpiredRecord(record, now) {
  if (!record) return null;
  if (record.lockUntil > now) return record;
  if (record.nextAllowedAt > now) return record;
  if (record.failures <= 0) return null;
  return { ...record, failures: 0, nextAllowedAt: 0, lockUntil: 0 };
}

function getPanelPasswordHash() {
  return process.env.PANEL_PASSWORD_HASH || "";
}

function verifyPanelPassword(password) {
  const expected = getPanelPasswordHash().trim();
  if (!expected) return false;
  const hashHex = crypto.createHash("sha256").update(String(password || ""), "utf8").digest("hex");
  const left = Buffer.from(hashHex, "utf8");
  const right = Buffer.from(expected, "utf8");
  if (left.length !== right.length) return false;
  return crypto.timingSafeEqual(left, right);
}

function registerFailure(clientId) {
  const now = nowMs();
  const prev = cleanExpiredRecord(attempts.get(clientId), now) || {
    failures: 0,
    nextAllowedAt: 0,
    lockUntil: 0,
  };
  const failures = prev.failures + 1;
  const delay = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * Math.pow(2, Math.max(0, failures - 1)));
  const lockUntil = failures >= MAX_FAILURES ? now + LOCK_MS : 0;
  const nextAllowedAt = lockUntil ? lockUntil : now + delay;
  const next = { failures, nextAllowedAt, lockUntil };
  attempts.set(clientId, next);
  return next;
}

function clearFailures(clientId) {
  attempts.delete(clientId);
}

function checkThrottle(req, res) {
  const clientId = getClientId(req);
  const now = nowMs();
  const record = cleanExpiredRecord(attempts.get(clientId), now);
  if (!record) return { ok: true, clientId };
  attempts.set(clientId, record);
  if (record.lockUntil > now) {
    res.status(429).json({
      error: "Too many failed attempts. Temporarily locked.",
      retry_after_ms: record.lockUntil - now,
    });
    return { ok: false, clientId };
  }
  if (record.nextAllowedAt > now) {
    res.status(429).json({
      error: "Too many requests. Please slow down.",
      retry_after_ms: record.nextAllowedAt - now,
    });
    return { ok: false, clientId };
  }
  return { ok: true, clientId };
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (chunk) => {
      data += chunk;
    });
    req.on("end", () => {
      if (!data) return resolve({});
      try {
        resolve(JSON.parse(data));
      } catch (e) {
        reject(e);
      }
    });
  });
}

function requirePassword(req, res, body) {
  const throttle = checkThrottle(req, res);
  if (!throttle.ok) return false;
  const pwd = (body && body.password) || req.headers["x-panel-password"] || "";
  if (!verifyPanelPassword(String(pwd))) {
    const next = registerFailure(throttle.clientId);
    res.status(401).json({
      error: "Unauthorized",
      retry_after_ms: Math.max(0, (next.nextAllowedAt || 0) - nowMs()),
      locked_until_ms: next.lockUntil || 0,
    });
    return false;
  }
  clearFailures(throttle.clientId);
  return true;
}

function envRequired(name) {
  const v = process.env[name];
  if (!v) throw new Error(`Missing env: ${name}`);
  return v;
}

function envPem(name) {
  return envRequired(name).replace(/\\n/g, "\n");
}

function normalizePem(raw) {
  let text = String(raw || "").trim();
  if (
    (text.startsWith('"') && text.endsWith('"')) ||
    (text.startsWith("'") && text.endsWith("'"))
  ) {
    text = text.slice(1, -1).trim();
  }
  text = text.replace(/\r/g, "").replace(/\\n/g, "\n");
  if (/-----BEGIN [^-]+-----/.test(text)) return text;

  const compact = text.replace(/\s+/g, "");
  if (!compact) return text;

  try {
    const decoded = Buffer.from(compact, "base64").toString("utf8").trim();
    if (/-----BEGIN [^-]+-----/.test(decoded)) {
      return decoded.replace(/\r/g, "");
    }
  } catch {}

  return text;
}

function looksLikeBase64(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  if (/-----BEGIN [^-]+-----/.test(t)) return false;
  if (!/^[A-Za-z0-9+/=\s]+$/.test(t)) return false;
  const compact = t.replace(/\s+/g, "");
  if (compact.length < 64) return false;
  if (compact.length % 4 !== 0) return false;
  return true;
}

function getPublicKey() {
  const keyText = normalizePem(envPem("JC_RSA_PUBLIC_KEY_PEM"));
  const compactB64 = keyText.replace(/\s+/g, "");
  try {
    return crypto.createPublicKey(keyText);
  } catch (e1) {
    try {
      return crypto.createPublicKey({ key: keyText, format: "pem", type: "spki" });
    } catch (e2) {
      if (looksLikeBase64(keyText)) {
        try {
          const der = Buffer.from(compactB64, "base64");
          return crypto.createPublicKey({ key: der, format: "der", type: "spki" });
        } catch (e3) {
          // fallthrough to final error
        }
      }
      const msg = e1 && e1.message ? e1.message : String(e1);
      throw new Error(
        `Invalid JC_RSA_PUBLIC_KEY_PEM: ${msg} (expected PEM, or base64 DER spki)`
      );
    }
  }
}

function getPrivateKey() {
  const keyText = normalizePem(envPem("JC_RSA_PRIVATE_KEY_PEM"));
  if (/-----BEGIN ENCRYPTED PRIVATE KEY-----/.test(keyText)) {
    throw new Error(
      "Invalid JC_RSA_PRIVATE_KEY_PEM: encrypted private keys are not supported. Please use an unencrypted private key (BEGIN PRIVATE KEY / BEGIN RSA PRIVATE KEY)."
    );
  }
  const compactB64 = keyText.replace(/\s+/g, "");
  try {
    return crypto.createPrivateKey(keyText);
  } catch (e1) {
    try {
      return crypto.createPrivateKey({ key: keyText, format: "pem", type: "pkcs8" });
    } catch (e2) {
      // If the env var is base64 of DER bytes, try DER imports.
      if (looksLikeBase64(keyText)) {
        const der = Buffer.from(compactB64, "base64");
        const candidates = [
          { format: "der", type: "pkcs8" },
          { format: "der", type: "pkcs1" },
        ];
        for (const c of candidates) {
          try {
            return crypto.createPrivateKey({ key: der, ...c });
          } catch {}
        }
      }
      // Also try PEM pkcs1 explicitly for older key styles.
      try {
        return crypto.createPrivateKey({ key: keyText, format: "pem", type: "pkcs1" });
      } catch {}

      const msg = e1 && e1.message ? e1.message : String(e1);
      throw new Error(
        `Invalid JC_RSA_PRIVATE_KEY_PEM: ${msg} (expected PEM, or base64 DER pkcs8/pkcs1)`
      );
    }
  }
}

function encryptSecurePayload(payloadObj) {
  const publicKey = getPublicKey();
  const plaintext = Buffer.from(JSON.stringify(payloadObj), "utf8");
  const aesKey = crypto.randomBytes(32);
  const nonce = crypto.randomBytes(12);

  const cipher = crypto.createCipheriv("aes-256-gcm", aesKey, nonce);
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();
  const cipherCombined = Buffer.concat([ciphertext, tag]);

  const encryptedKey = crypto.publicEncrypt(
    {
      key: publicKey,
      oaepHash: "sha256",
      padding: crypto.constants.RSA_PKCS1_OAEP_PADDING,
    },
    aesKey
  );

  return {
    version: 1,
    algo: "rsa-oaep-sha256 + aes-256-gcm",
    encrypted_key_b64: encryptedKey.toString("base64"),
    nonce_b64: nonce.toString("base64"),
    ciphertext_b64: cipherCombined.toString("base64"),
  };
}

function decryptSecurePayload(doc) {
  const privateKey = getPrivateKey();
  const encryptedKey = Buffer.from(doc.encrypted_key_b64 || "", "base64");
  const nonce = Buffer.from(doc.nonce_b64 || "", "base64");
  const cipherCombined = Buffer.from(doc.ciphertext_b64 || "", "base64");

  if (!encryptedKey.length || !nonce.length || !cipherCombined.length) {
    return {
      jc_members: [],
      jc_template: "",
      jc_start_wed: "2026-04-08",
      jc_subject: "",
      jc_anchor_presenter: "",
    };
  }

  const aesKey = crypto.privateDecrypt(
    {
      key: privateKey,
      oaepHash: "sha256",
      padding: crypto.constants.RSA_PKCS1_OAEP_PADDING,
    },
    encryptedKey
  );

  const tag = cipherCombined.subarray(cipherCombined.length - 16);
  const ciphertext = cipherCombined.subarray(0, cipherCombined.length - 16);
  const decipher = crypto.createDecipheriv("aes-256-gcm", aesKey, nonce);
  decipher.setAuthTag(tag);
  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  return JSON.parse(plaintext.toString("utf8"));
}

module.exports = {
  readJsonBody,
  requirePassword,
  encryptSecurePayload,
  decryptSecurePayload,
};

