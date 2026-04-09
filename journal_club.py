from __future__ import annotations

import argparse
import json
import os
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from weekly_core import send_email_via_smtp


JST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "state.json"
SECURE_CONFIG_PATH = BASE_DIR / "secure_config.json"


def _get_env_list(name: str) -> List[str]:
    raw = os.environ.get(name, "") or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


def _load_members() -> List[str]:
    """
    从环境变量 JC_MEMBERS 读取轮值名单（逗号分隔）。
    """
    members = _load_secure_config().get("jc_members", [])
    if not members:
        # 默认示例顺序，不含邮箱，仅姓名
        members = [
            "Liu Yuting",
            "Sun Lifu",
            "Bao Xuanrong",
            "Cheng Boyi",
            "Li Peizhe",
            "Liu Zhaoyang",
        ]
    # 初始队列规则：按字母顺序排
    return sorted(members, key=lambda s: s.casefold())


def _decrypt_secure_config() -> dict:
    if not SECURE_CONFIG_PATH.exists():
        return {}
    with SECURE_CONFIG_PATH.open("r", encoding="utf-8") as f:
        doc = json.load(f) or {}
    encrypted_key_b64 = doc.get("encrypted_key_b64", "")
    nonce_b64 = doc.get("nonce_b64", "")
    ciphertext_b64 = doc.get("ciphertext_b64", "")
    if not encrypted_key_b64 or not nonce_b64 or not ciphertext_b64:
        return {}

    private_key_pem = os.environ.get("JC_RSA_PRIVATE_KEY_PEM", "").replace("\\n", "\n")
    if not private_key_pem:
        return {}
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )

    aes_key = private_key.decrypt(
        base64.b64decode(encrypted_key_b64),
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    combined = base64.b64decode(ciphertext_b64)
    nonce = base64.b64decode(nonce_b64)
    ciphertext = combined[:-16]
    tag = combined[-16:]
    aesgcm = AESGCM(aes_key)
    plain = aesgcm.decrypt(nonce, ciphertext + tag, None)
    return json.loads(plain.decode("utf-8"))


def _load_secure_config() -> dict:
    try:
        return _decrypt_secure_config()
    except Exception:  # noqa: BLE001
        return {}


def _load_recipients() -> List[str]:
    """
    从环境变量 JC_TO 读取收件人邮箱组（逗号分隔）。
    """
    return _get_env_list("JC_TO")


def _load_start_wednesday() -> datetime:
    """
    轮换起始的“周三”日期，JST。
    环境变量 JC_START_WED 默认为 2026-04-08（示例中的 4 月 8 日，周三）。
    """
    raw = str(_load_secure_config().get("jc_start_wed", "2026-04-08"))
    return datetime.fromisoformat(raw).replace(tzinfo=JST)


def _compute_cycle(now_jst: datetime) -> Tuple[bool, int, datetime]:
    """
    每周二发送一封邮件，但“成员 + 日期（周三）”每两周轮换一次。

    返回：
    - should_send: 是否允许发送（在起始周三之前不发）
    - cycle_id: 当前所处的 2 周周期编号（从 0 开始）
    - cycle_wed: 当前周期对应的周三日期（固定为 start_wed + 14*cycle_id）
    """
    start_wed = _load_start_wednesday().replace(hour=0, minute=0, second=0, microsecond=0)
    now_day = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    if now_day < start_wed:
        return False, 0, start_wed
    diff_days = (now_day - start_wed).days
    cycle_id = diff_days // 14
    cycle_wed = start_wed + timedelta(days=14 * cycle_id)
    return True, cycle_id, cycle_wed


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {"skip_weeks_remaining": 0, "cycle_id": None, "cycle_presenter": None, "members_queue": []}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f) or {"skip_weeks_remaining": 0, "cycle_id": None, "cycle_presenter": None, "members_queue": []}
    except Exception:  # noqa: BLE001
        return {"skip_weeks_remaining": 0, "cycle_id": None, "cycle_presenter": None, "members_queue": []}


def _save_state(state: dict) -> None:
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _get_skip_weeks_remaining(state: dict) -> int:
    try:
        return max(0, int(state.get("skip_weeks_remaining", 0) or 0))
    except Exception:  # noqa: BLE001
        return 0


def _set_skip_weeks_remaining(state: dict, weeks: int) -> None:
    state["skip_weeks_remaining"] = max(0, int(weeks))


def compute_next_send_info(now_jst: datetime, skip_weeks_remaining: int) -> dict:
    """
    计算下次真正发送的时间点（JST），以及对应的周三日期。

    - workflow 固定每周二 12:00 JST 运行一次
    - 发送周：由 start_wed 起每 14 天一次（按对应周三判断）
    - 跳过：每次 workflow 运行若 skip_weeks_remaining>0，则本周不发送并 -1
    """
    start_wed = _load_start_wednesday()
    next_run = now_jst.replace(hour=12, minute=0, second=0, microsecond=0)
    # 找到下一个“周二 12:00”
    days_until_tue = (1 - next_run.weekday()) % 7  # Tuesday=1
    if days_until_tue == 0 and now_jst >= next_run:
        days_until_tue = 7
    next_run = next_run + timedelta(days=days_until_tue)

    remaining = max(0, int(skip_weeks_remaining))
    for _ in range(0, 260):  # 最多向后 5 年左右，避免死循环
        run_time = next_run
        run_jst = run_time
        # 每周二都可能发送，但成员/日期每两周轮换
        should_send, _, cycle_wed = _compute_cycle(run_jst)
        if remaining > 0:
            remaining -= 1
        elif should_send:
            return {
                "next_run_jst": run_jst.isoformat(),
                "event_wed_jst": cycle_wed.isoformat(),
            }
        next_run = next_run + timedelta(days=7)
    return {
        "next_run_jst": None,
        "event_wed_jst": None,
    }


def _ensure_queue(state: dict) -> List[str]:
    desired = _load_members()
    current = state.get("members_queue") or []
    if not isinstance(current, list):
        current = []
    current = [str(x) for x in current if str(x)]

    # 保留现有顺序中的仍存在成员
    desired_set = {m for m in desired}
    merged = [m for m in current if m in desired_set]
    # 追加新成员（按字母顺序）
    for m in desired:
        if m not in merged:
            merged.append(m)
    state["members_queue"] = merged
    return merged


def _pick_presenter_for_cycle(state: dict, cycle_id: int) -> str:
    queue = _ensure_queue(state)
    saved_cycle_id = state.get("cycle_id", None)
    saved_presenter = state.get("cycle_presenter", None)
    if saved_cycle_id == cycle_id and isinstance(saved_presenter, str) and saved_presenter:
        return saved_presenter
    return queue[0] if queue else ""


def _after_send_update_state(state: dict, cycle_id: int, presenter: str) -> None:
    queue = _ensure_queue(state)
    saved_cycle_id = state.get("cycle_id", None)
    # 周期第一次发送成功后才“讲过的人挪到最后”
    if saved_cycle_id != cycle_id:
        if queue and queue[0] == presenter:
            queue.append(queue.pop(0))
        state["cycle_id"] = cycle_id
        state["cycle_presenter"] = presenter
        state["members_queue"] = queue


def build_mail(
    now_jst: datetime, state: dict
) -> Tuple[bool, str, str, List[str], Optional[datetime], str, int]:
    """
    返回 (should_send, subject, body, recipients, cycle_wed, presenter, cycle_id)
    """
    should_send, cycle_id, cycle_wed = _compute_cycle(now_jst)
    recipients = _load_recipients()
    if not should_send or not recipients:
        return False, "", "", recipients, None, "", cycle_id

    presenter = _pick_presenter_for_cycle(state, cycle_id)
    month = cycle_wed.month
    day = cycle_wed.day

    secure_subject = str(_load_secure_config().get("jc_subject", "") or "")
    subject = secure_subject or f"文献分享（Journal Club） - {presenter}（{month}月{day}日）"

    # 正文模板可以通过环境变量 JC_TEMPLATE 覆盖，这样实际内容只存在于机密环境中
    secure_template = _load_secure_config().get("jc_template", "")
    template = secure_template or (
        "文献分享（Journal Club）（每两周）\n\n"
        "每两周由一位成员介绍领域内最新的研究论文，制作 PPT。\n\n"
        f"本期轮值：{presenter}（{month}月{day}日）\n\n"
        "文献分享轮值名单：\n"
        + "\n".join(_ensure_queue(state) or _load_members())
        + "\n\n请大家提前准备，期待讨论！"
    )
    template = (
        template.replace("\\n", "\n")
        .replace("{{presenter}}", presenter)
        .replace("{{month}}", str(month))
        .replace("{{day}}", str(day))
    )

    return True, subject, template, recipients, cycle_wed, presenter, cycle_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--send-only",
        action="store_true",
        help="立即发送一封邮件，但不更改任何 state。",
    )
    args = parser.parse_args()

    now_jst = datetime.now(tz=timezone.utc).astimezone(JST)
    state = _load_state()
    if args.send_only:
        should_send, subject, body, recipients, _, _, _ = build_mail(now_jst, state)
        if should_send:
            send_email_via_smtp(subject, body, recipients)
        return

    skip = _get_skip_weeks_remaining(state)
    # 跳过优先：只要剩余跳过周数 > 0，本周不发送，并递减 1（一次性，跨周递减）
    if skip > 0:
        _set_skip_weeks_remaining(state, skip - 1)
        _save_state(state)
        return

    should_send, subject, body, recipients, cycle_wed, presenter, cycle_id = build_mail(now_jst, state)
    if not should_send:
        return

    send_email_via_smtp(subject, body, recipients)
    if cycle_wed is not None and presenter:
        _after_send_update_state(state, cycle_id, presenter)
    _save_state(state)


if __name__ == "__main__":
    main()

