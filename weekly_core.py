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
    使用环境变量中的 SMTP 配置发送邮件。

    需要设置的环境变量：
    - SMTP_HOST
    - SMTP_PORT
    - SMTP_USER
    - SMTP_PASS
    - FROM_EMAIL
    """
    import smtplib
    import ssl

    host = os.environ.get("SMTP_HOST", "")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    from_email = os.environ.get("FROM_EMAIL", user)
    timeout_s = float(os.environ.get("SMTP_TIMEOUT", "20") or "20")
    use_ssl_raw = str(os.environ.get("SMTP_USE_SSL", "") or "").strip().lower()
    starttls_raw = str(os.environ.get("SMTP_STARTTLS", "") or "").strip().lower()
    debug_raw = str(os.environ.get("SMTP_DEBUG", "") or "").strip().lower()

    if not host or not user or not password or not from_email:
        raise RuntimeError("SMTP 配置不完整，请检查环境变量。")

    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    def _as_bool(raw: str, default: bool) -> bool:
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default

    # Convention:
    # - port 465 => implicit SSL (SMTPS)
    # - port 587 => STARTTLS
    use_ssl = _as_bool(use_ssl_raw, default=(port == 465))
    starttls = _as_bool(starttls_raw, default=(not use_ssl))
    debug = _as_bool(debug_raw, default=False)
    ssl_ctx = ssl.create_default_context()

    try:
        if use_ssl:
            server_ctx = smtplib.SMTP_SSL(host, port, timeout=timeout_s, context=ssl_ctx)
        else:
            server_ctx = smtplib.SMTP(host, port, timeout=timeout_s)

        with server_ctx as server:
            if debug:
                server.set_debuglevel(1)
            server.ehlo()
            if starttls and not use_ssl:
                server.starttls(context=ssl_ctx)
                server.ehlo()
            server.login(user, password)
            server.sendmail(from_email, recipients, msg.as_string())
    except TimeoutError as e:
        raise RuntimeError(
            "SMTP 连接超时（无法连接到服务器）。"
            f" host={host!r} port={port}. "
            "这通常是：SMTP 服务商屏蔽了 GitHub Actions 出口 IP / 端口，或该服务要求改用 465(SSL) / 禁用 STARTTLS。"
        ) from e
    except OSError as e:
        raise RuntimeError(
            "SMTP 连接失败。"
            f" host={host!r} port={port}. "
            "请检查 SMTP_HOST/SMTP_PORT 是否正确，以及服务商是否允许外部网络连接。"
        ) from e

