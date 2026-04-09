import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Dict, Any, Tuple


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
STATE_PATH = BASE_DIR / "state.json"


@dataclass
class Message:
    subject: str
    body: str


@dataclass
class Config:
    emails: List[str]
    messages: List[Message]
    skip_weeks: int = 0


@dataclass
class State:
    current_index: int = 0
    skip_weeks_remaining: int = 0


def _default_config() -> Config:
    return Config(
        emails=[],
        messages=[Message(subject="示例主题", body="示例正文")],
        skip_weeks=0,
    )


def _default_state() -> State:
    return State(current_index=0, skip_weeks_remaining=0)


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        cfg = _default_config()
        save_config(cfg)
        return cfg
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    emails = raw.get("emails", [])
    messages_raw = raw.get("messages", [])
    messages = [
        Message(subject=m.get("subject", ""), body=m.get("body", ""))
        for m in messages_raw
    ]
    skip_weeks = int(raw.get("skip_weeks", 0) or 0)
    return Config(emails=emails, messages=messages, skip_weeks=skip_weeks)


def save_config(cfg: Config) -> None:
    data: Dict[str, Any] = {
        "emails": cfg.emails,
        "messages": [asdict(m) for m in cfg.messages],
        "skip_weeks": cfg.skip_weeks,
    }
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_state() -> State:
    if not STATE_PATH.exists():
        st = _default_state()
        save_state(st)
        return st
    with STATE_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return State(
        current_index=int(raw.get("current_index", 0) or 0),
        skip_weeks_remaining=int(raw.get("skip_weeks_remaining", 0) or 0),
    )


def save_state(st: State) -> None:
    data = {
        "current_index": st.current_index,
        "skip_weeks_remaining": st.skip_weeks_remaining,
    }
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def compute_next_send(cfg: Config, st: State) -> Tuple[bool, int]:
    """
    返回 (should_send, message_index)

    跳过逻辑：
    - 如果 skip_weeks_remaining > 0，本周不发，减 1，索引不前进
    - 否则本周发送 current_index，对应邮件；然后 current_index + 1 (mod N)
    """
    if not cfg.messages:
        return False, 0
    if st.skip_weeks_remaining > 0:
        st.skip_weeks_remaining -= 1
        return False, st.current_index
    return True, st.current_index % len(cfg.messages)


def apply_after_send(cfg: Config, st: State) -> None:
    if cfg.messages:
        st.current_index = (st.current_index + 1) % len(cfg.messages)


def set_skip_weeks(st: State, weeks: int) -> None:
    st.skip_weeks_remaining = max(0, int(weeks))


def format_debug_status() -> Dict[str, Any]:
    cfg = load_config()
    st = load_state()
    now_utc = datetime.now(timezone.utc).isoformat()
    return {
        "now_utc": now_utc,
        "emails": cfg.emails,
        "messages_count": len(cfg.messages),
        "current_index": st.current_index,
        "skip_weeks_remaining": st.skip_weeks_remaining,
    }


def send_email_via_smtp(
    subject: str,
    body: str,
    recipients: List[str],
) -> None:
    """
    发送邮件（优先 HTTPS 邮件 API，其次 SMTP）。

    说明：
    - GitHub Actions runner 对外连 SMTP（25/465/587 等）经常会被网络策略阻断，表现为 connect timeout。
      因此在 CI 环境中推荐使用邮件服务商提供的 HTTPS API（如 Resend / SendGrid / Mailgun 等）。
    - 为保持兼容性，函数名仍叫 send_email_via_smtp，但内部会根据环境变量选择传输方式。

    需要设置的环境变量：
    - （推荐）RESEND_API_KEY: 使用 Resend API 发信
    - FROM_EMAIL: 发件人邮箱（SMTP/Resend 都用）

    仅 SMTP 模式需要：
    - SMTP_HOST
    - SMTP_PORT（默认 587）
    - SMTP_USER
    - SMTP_PASS
    """
    transport = (os.environ.get("MAIL_TRANSPORT", "") or "").strip().lower()
    resend_key = (os.environ.get("RESEND_API_KEY", "") or "").strip()
    if transport in ("resend", "http") or (transport == "" and resend_key):
        _send_email_via_resend(subject, body, recipients)
        return

    _send_email_via_smtp_starttls(subject, body, recipients)


def _send_email_via_smtp_starttls(subject: str, body: str, recipients: List[str]) -> None:
    import smtplib

    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("FROM_EMAIL", user)

    if not host or not user or not password or not from_email:
        raise RuntimeError(
            "SMTP 配置不完整。若在 GitHub Actions 里发送，建议改用邮件服务 HTTPS API（例如设置 RESEND_API_KEY）。"
        )

    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    timeout_s = float(os.environ.get("SMTP_TIMEOUT", "15") or 15)
    try:
        with smtplib.SMTP(host, port, timeout=timeout_s) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.sendmail(from_email, recipients, msg.as_string())
    except TimeoutError as e:
        raise RuntimeError(
            f"SMTP 连接超时（{host}:{port}）。GitHub Actions runner 可能无法直连 SMTP，建议改用 HTTPS 邮件 API（例如 Resend）。"
        ) from e


def _send_email_via_resend(subject: str, body: str, recipients: List[str]) -> None:
    """
    使用 Resend 的 HTTPS API 发信。

    环境变量：
    - RESEND_API_KEY（必需）
    - FROM_EMAIL（必需，需为 Resend 已验证域名/地址）
    """
    import urllib.request
    import urllib.error

    api_key = (os.environ.get("RESEND_API_KEY", "") or "").strip()
    from_email = (os.environ.get("FROM_EMAIL", "") or "").strip()
    if not api_key or not from_email:
        raise RuntimeError("RESEND_API_KEY / FROM_EMAIL 未配置，无法使用 Resend 发信。")

    payload = json.dumps(
        {
            "from": from_email,
            "to": recipients,
            "subject": subject,
            "text": body,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if not (200 <= int(resp.status) < 300):
                raise RuntimeError(f"Resend API 返回异常状态码：{resp.status}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            detail = ""
        raise RuntimeError(f"Resend API HTTP {e.code}: {detail}".strip()) from e

