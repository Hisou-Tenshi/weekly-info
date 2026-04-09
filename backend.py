from __future__ import annotations

import argparse
from dataclasses import asdict
from typing import Any, Dict, List

from flask import Flask, jsonify, request

from weekly_core import (
    Config,
    Message,
    apply_after_send,
    compute_next_send,
    format_debug_status,
    load_config,
    load_state,
    save_config,
    save_state,
    send_email_via_smtp,
    set_skip_weeks,
)


app = Flask(__name__)


@app.get("/api/config")
def get_config() -> Any:
    cfg = load_config()
    st = load_state()
    return jsonify(
        {
            "emails": cfg.emails,
            "messages": [asdict(m) for m in cfg.messages],
            "skip_weeks_default": cfg.skip_weeks,
            "state": {
                "current_index": st.current_index,
                "skip_weeks_remaining": st.skip_weeks_remaining,
            },
        }
    )


@app.post("/api/config")
def update_config() -> Any:
    data: Dict[str, Any] = request.get_json(force=True, silent=True) or {}
    emails_raw = data.get("emails", [])
    if isinstance(emails_raw, str):
        emails = [e.strip() for e in emails_raw.split(",") if e.strip()]
    else:
        emails = [str(e).strip() for e in emails_raw if str(e).strip()]

    messages_raw = data.get("messages", [])
    messages: List[Message] = []
    for m in messages_raw:
        if not isinstance(m, dict):
            continue
        subject = str(m.get("subject", "")).strip()
        body = str(m.get("body", "")).strip()
        if subject or body:
            messages.append(Message(subject=subject, body=body))

    skip_weeks = int(data.get("skip_weeks", 0) or 0)

    cfg = Config(emails=emails, messages=messages or [Message(subject="", body="")], skip_weeks=skip_weeks)
    save_config(cfg)

    st = load_state()
    # 当用户更新「跳过周数」时，立即覆盖剩余跳过周数
    set_skip_weeks(st, skip_weeks)
    save_state(st)

    return jsonify({"ok": True})


@app.post("/api/skip")
def api_skip() -> Any:
    data = request.get_json(force=True, silent=True) or {}
    weeks = int(data.get("weeks", 0) or 0)
    st = load_state()
    set_skip_weeks(st, weeks)
    save_state(st)
    return jsonify({"ok": True, "skip_weeks_remaining": st.skip_weeks_remaining})


@app.post("/api/send-now")
def api_send_now() -> Any:
    """
    立即模拟「本周执行一次」：
    - 按当前跳过逻辑决定是否发送
    - 如果发送，则通过 SMTP 发出
    - 更新状态
    """
    cfg = load_config()
    st = load_state()
    should_send, idx = compute_next_send(cfg, st)
    result: Dict[str, Any] = {
        "should_send": should_send,
        "index": idx,
    }
    if should_send and cfg.emails and cfg.messages:
        msg = cfg.messages[idx]
        try:
            send_email_via_smtp(msg.subject, msg.body, cfg.emails)
            result["sent"] = True
        except Exception as e:  # noqa: BLE001
            result["sent"] = False
            result["error"] = str(e)
        apply_after_send(cfg, st)
    save_state(st)
    return jsonify(result)


@app.get("/api/status")
def api_status() -> Any:
    return jsonify(format_debug_status())


def cron_once() -> None:
    """
    供定时任务（如 GitHub Actions）调用：
    - 读取配置 / 状态
    - 按规则决定是否发送
    - 如需发送，则调用 SMTP
    - 更新状态
    """
    cfg = load_config()
    st = load_state()
    should_send, idx = compute_next_send(cfg, st)
    if should_send and cfg.emails and cfg.messages:
        msg = cfg.messages[idx]
        send_email_via_smtp(msg.subject, msg.body, cfg.emails)
        apply_after_send(cfg, st)
    save_state(st)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cron-once",
        action="store_true",
        help="执行一次定时逻辑（用于 workflow / 定时任务）。",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Flask 监听地址。",
    )
    parser.add_argument(
        "--port",
        default=8000,
        type=int,
        help="Flask 监听端口。",
    )
    args = parser.parse_args()

    if args.cron_once:
        cron_once()
    else:
        app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()

