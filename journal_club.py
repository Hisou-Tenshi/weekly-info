from __future__ import annotations

import argparse
import hashlib
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


def _default_member_names() -> List[str]:
    return [
        "Liu Yuting",
        "Sun Lifu",
        "Bao Xuanrong",
        "Cheng Boyi",
        "Li Peizhe",
        "Liu Zhaoyang",
    ]


def _members_sorted_from_secure(secure: dict) -> List[str]:
    members = secure.get("jc_members") or []
    if not members:
        members = _default_member_names()
    return sorted((str(m).strip() for m in members if str(m).strip()), key=lambda s: s.casefold())


def _anchor_signature(secure: dict) -> str:
    """与前端 / api/state 使用同一规则，配置变更后需重新 bootstrap。"""
    m = _members_sorted_from_secure(secure)
    start = str(secure.get("jc_start_wed", "2026-04-08"))
    anch = str(secure.get("jc_anchor_presenter", "") or "").strip()
    raw = start + "\n" + anch + "\n" + "\n".join(m)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_members() -> List[str]:
    """
    从环境变量 JC_MEMBERS 读取轮值名单（逗号分隔）。
    """
    return _members_sorted_from_secure(_load_secure_config())


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


def _compute_week(now_jst: datetime) -> Tuple[bool, int, datetime]:
    """
    每周二发送一封邮件，轮值“轮换一次-保持一次”交替进行（由 state 控制，与日期无关）。

    返回：
    - should_send: 是否允许发送（在起始周三之前不发）
    - week_id: 当前所处的周编号（从 0 开始）
    - week_wed: 当前周对应的周三日期（固定为 start_wed + 7*week_id）
    """
    start_wed = _load_start_wednesday().replace(hour=0, minute=0, second=0, microsecond=0)
    now_day = now_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    if now_day < start_wed:
        return False, 0, start_wed
    diff_days = (now_day - start_wed).days
    week_id = diff_days // 7
    week_wed = start_wed + timedelta(days=7 * week_id)
    return True, week_id, week_wed


def _effective_run_time_jst(now_jst: datetime) -> datetime:
    """
    手动发送可能发生在任意日期/时间；为保证“发表日期”的换算与自动任务一致，
    这里把 now 映射到“本次发送对应的周二 12:00 JST 触发点”。

    - 自动任务（周二 12:00 左右）会落在“本周周二 12:00”
    - 其他时刻的手动发送会落在“下一个周二 12:00”
    """
    base = now_jst.replace(hour=12, minute=0, second=0, microsecond=0)
    # Python weekday(): Mon=0 ... Tue=1
    days_until_tue = (1 - base.weekday()) % 7
    if days_until_tue == 0 and now_jst >= base:
        # 今天就是周二且已经过了 12:00，则视为“本周触发点”
        return base
    if days_until_tue == 0:
        # 今天周二但还没到 12:00，则走下一个周二
        days_until_tue = 7
    return base + timedelta(days=days_until_tue)


def _bootstrap_state_if_needed(state: dict) -> bool:
    """
    根据加密配置中的「锚点周三 + 该周轮换讲者」推导初始队列（锚点周三已完成一轮轮换周）：

    - 成员名单在配置中按字母序规范化
    - jc_anchor_presenter = 该锚点周三轮换周的讲者（轮换前队首，轮换后移到队尾）
    - 推导队列：其余成员按字母序 + 该讲者在末尾；下一次发送为保持周（send_step=1）

    使用 anchor_sig 检测配置是否变更；变更后需重新 bootstrap（由保存配置 API 清除标记，或此处发现 sig 不一致）。

    返回：是否修改了 state（用于决定是否需要保存）。
    """
    secure = _load_secure_config()
    sig = _anchor_signature(secure)
    if state.get("bootstrapped_v1") is True and state.get("anchor_sig") == sig:
        return False

    sorted_members = _members_sorted_from_secure(secure)
    if not sorted_members:
        state["members_queue"] = []
        state["send_step"] = 0
        state["last_presenter"] = None
        state["bootstrapped_v1"] = True
        state["anchor_sig"] = sig
        state.pop("cycle_id", None)
        state.pop("cycle_presenter", None)
        return True

    anchor = str(secure.get("jc_anchor_presenter", "") or "").strip()
    if anchor and anchor in sorted_members:
        rest = [x for x in sorted_members if x != anchor]
        rotated = rest + [anchor]
        presenter_done = anchor
    else:
        first = sorted_members[0]
        rotated = sorted_members[1:] + [first] if len(sorted_members) > 1 else sorted_members[:]
        presenter_done = first

    state["members_queue"] = rotated
    state["send_step"] = 1
    state["last_presenter"] = presenter_done
    state["bootstrapped_v1"] = True
    state["anchor_sig"] = sig
    state.pop("cycle_id", None)
    state.pop("cycle_presenter", None)
    return True


def _base_wednesday_for_run(run_jst: datetime) -> datetime:
    """
    给定一次“周二 12:00 JST 触发点”，计算该次发送对应的“当周周三”（自动发送日的第二天）。
    """
    base = run_jst.replace(hour=0, minute=0, second=0, microsecond=0)
    # weekday(): Mon=0 Tue=1 Wed=2
    days_until_wed = (2 - base.weekday() + 7) % 7
    if days_until_wed == 0:
        return base
    return base + timedelta(days=days_until_wed)

def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {
            "skip_weeks_remaining": 0,
            "members_queue": [],
            "send_step": 0,
            "last_presenter": None,
        }
    try:
        with STATE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f) or {
                "skip_weeks_remaining": 0,
                "members_queue": [],
                "send_step": 0,
                "last_presenter": None,
            }
    except Exception:  # noqa: BLE001
        return {
            "skip_weeks_remaining": 0,
            "members_queue": [],
            "send_step": 0,
            "last_presenter": None,
        }


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
    - 发送周：由 start_wed 起每 7 天一次（按对应周三判断）
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
        # 每周二都可能发送；轮值是否轮换由 state 决定（此处仅用于“对应周三”展示）
        should_send, _, week_wed = _compute_week(run_jst)
        if remaining > 0:
            remaining -= 1
        elif should_send:
            return {
                "next_run_jst": run_jst.isoformat(),
                "event_wed_jst": week_wed.isoformat(),
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


def _get_send_step(state: dict) -> int:
    try:
        return max(0, int(state.get("send_step", 0) or 0))
    except Exception:  # noqa: BLE001
        return 0


def _is_rotate_week(state: dict) -> bool:
    # 规则：轮换一次-保持一次循环；send_step 只在“实际发送成功后”递增
    # send_step=0 -> 轮换；=1 -> 保持；=2 -> 轮换...
    return (_get_send_step(state) % 2) == 0


def _pick_presenter_for_week(state: dict) -> str:
    queue = _ensure_queue(state)
    if not queue:
        return ""
    if _is_rotate_week(state):
        return queue[0]
    # 保持周：轮值人为“上一周轮换后的第一位”，即当前队首
    return queue[0]


def _after_send_update_state(state: dict, presenter: str) -> None:
    queue = _ensure_queue(state)
    rotate = _is_rotate_week(state)
    if rotate and queue and queue[0] == presenter:
        queue.append(queue.pop(0))
    state["members_queue"] = queue
    state["last_presenter"] = presenter
    state["send_step"] = _get_send_step(state) + 1


def build_mail(
    now_jst: datetime, state: dict
) -> Tuple[bool, str, str, List[str], Optional[datetime], str, int]:
    """
    返回 (should_send, subject, body, recipients, event_wed, presenter, week_id)
    """
    run_jst = _effective_run_time_jst(now_jst)
    base_wed = _base_wednesday_for_run(run_jst)
    start_wed = _load_start_wednesday().replace(hour=0, minute=0, second=0, microsecond=0)
    if base_wed < start_wed:
        return False, "", "", _load_recipients(), None, "", 0

    diff_days = (base_wed - start_wed).days
    week_id = diff_days // 7
    recipients = _load_recipients()
    if not recipients:
        return False, "", "", recipients, None, "", week_id

    presenter = _pick_presenter_for_week(state)
    # 发表日期规则：
    # - 轮换周：当周周三（自动发送日次日）
    # - 保持周：下一周周三（自动发送日第 8 天）
    event_wed = base_wed if _is_rotate_week(state) else (base_wed + timedelta(days=7))
    month = event_wed.month
    day = event_wed.day

    secure_subject = str(_load_secure_config().get("jc_subject", "") or "")
    subject = secure_subject or f"文献分享（Journal Club） - {presenter}（{month}月{day}日）"

    # 正文模板可以通过环境变量 JC_TEMPLATE 覆盖，这样实际内容只存在于机密环境中
    secure_template = _load_secure_config().get("jc_template", "")
    template = secure_template or (
        "文献分享（Journal Club）\n\n"
        "每周由成员分享领域内最新的研究论文（轮值：轮换一次-保持一次）。\n\n"
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

    return True, subject, template, recipients, event_wed, presenter, week_id


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

    # 首次初始化：根据锚点周三预推进一次轮换，避免第一次触发还使用“锚点当天的第一位”
    if args.send_only:
        state = dict(state)
        _bootstrap_state_if_needed(state)
    else:
        if _bootstrap_state_if_needed(state):
            _save_state(state)
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

    should_send, subject, body, recipients, week_wed, presenter, _ = build_mail(now_jst, state)
    if not should_send:
        return

    send_email_via_smtp(subject, body, recipients)
    if week_wed is not None and presenter:
        _after_send_update_state(state, presenter)
    _save_state(state)


if __name__ == "__main__":
    main()

