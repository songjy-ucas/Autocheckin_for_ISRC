#!/usr/bin/env python3
"""ISRC LLM API command-line daily check-in client.

Uses a Personal Access Token (PAT) and the public New API check-in endpoint.
No browser, third-party package, username, password, or OAuth automation is used.
"""

from __future__ import annotations

import argparse
import base64
import csv
import ctypes
import getpass
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://llmapi.isrc.ac.cn"
TOKEN_ENV_NAME = "ISRC_ACCESS_TOKEN"
USER_ID_ENV_NAME = "ISRC_USER_ID"
CREDENTIAL_FILE = Path(__file__).with_name(".credential.bin")
USER_AGENT = "ISRC-AutoCheckin-CLI/1.0"
TASK_NAME = "ISRC LLMAPI Daily Checkin"
LOG_FILE = Path(__file__).with_name("checkin.log")
TASK_XML_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


@dataclass
class Result:
    status: str
    message: str
    reward: int | float | None = None
    checked_in: bool | None = None
    http_status: int | None = None

    @property
    def exit_code(self) -> int:
        return {
            "success": 0,
            "already": 0,
            "status": 0,
            "login_required": 2,
            "disabled": 4,
        }.get(self.status, 3)


@dataclass
class HttpResponse:
    status: int
    payload: dict[str, Any] | None
    text: str


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _bytes_to_blob(value: bytes) -> tuple[DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(value)
    blob = DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def protect_for_current_windows_user(secret: str) -> bytes:
    """Encrypt a secret with Windows DPAPI for the current Windows user."""
    if os.name != "nt":
        raise RuntimeError("加密保存仅支持 Windows")

    input_blob, input_buffer = _bytes_to_blob(secret.encode("utf-8"))
    entropy_blob, entropy_buffer = _bytes_to_blob(b"ISRC-AutoCheckin-v1")
    output_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "ISRC LLM API PAT",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(output_blob),
    )
    _ = (input_buffer, entropy_buffer)
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def unprotect_for_current_windows_user(ciphertext: bytes) -> str:
    """Decrypt a DPAPI value created for the current Windows user."""
    if os.name != "nt":
        raise RuntimeError("加密凭据仅支持 Windows")

    input_blob, input_buffer = _bytes_to_blob(ciphertext)
    entropy_blob, entropy_buffer = _bytes_to_blob(b"ISRC-AutoCheckin-v1")
    output_blob = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    success = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    )
    _ = (input_buffer, entropy_buffer)
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output_blob.pbData)


def save_encrypted_token(token: str, path: Path = CREDENTIAL_FILE) -> None:
    token = normalize_token(token)
    if not token:
        raise ValueError("访问令牌不能为空")
    encrypted = protect_for_current_windows_user(token)
    path.write_bytes(base64.b64encode(encrypted))


def load_encrypted_token(path: Path = CREDENTIAL_FILE) -> str | None:
    if not path.exists():
        return None
    try:
        encrypted = base64.b64decode(path.read_bytes(), validate=True)
        return normalize_token(unprotect_for_current_windows_user(encrypted))
    except Exception as error:
        raise RuntimeError(f"无法读取加密凭据：{error}") from error


def normalize_token(token: str | None) -> str:
    value = (token or "").strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value


def parse_auto_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value or "")
    if not match:
        raise ValueError("时间格式应为 HH:MM，例如 08:10 或 12:00")
    hour, minute = (int(part) for part in match.groups())
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("时间必须在 00:00 至 23:59 之间")
    return hour, minute


def current_windows_user_sid() -> str:
    if os.name != "nt":
        raise RuntimeError("自动计划功能仅支持 Windows")
    completed = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "无法获取当前 Windows 用户 SID")
    rows = list(csv.reader(io.StringIO(completed.stdout)))
    if not rows or len(rows[0]) < 2 or not rows[0][1].startswith("S-"):
        raise RuntimeError("无法解析当前 Windows 用户 SID")
    return rows[0][1]


def scheduled_python_executable() -> Path:
    executable = Path(sys.executable).resolve()
    pythonw = executable.with_name("pythonw.exe")
    return pythonw if pythonw.exists() else executable


def build_task_xml(
    schedule_time: str,
    *,
    user_sid: str,
    python_executable: Path | None = None,
    script_path: Path | None = None,
    log_file: Path | None = None,
    user_id: str | None = None,
    now: datetime | None = None,
) -> bytes:
    hour, minute = parse_auto_time(schedule_time)
    now = now or datetime.now()
    python_executable = python_executable or scheduled_python_executable()
    script_path = (script_path or Path(__file__)).resolve()
    log_file = (log_file or LOG_FILE).resolve()

    ET.register_namespace("", TASK_XML_NS)
    task = ET.Element(f"{{{TASK_XML_NS}}}Task", {"version": "1.4"})
    registration = ET.SubElement(task, f"{{{TASK_XML_NS}}}RegistrationInfo")
    ET.SubElement(registration, f"{{{TASK_XML_NS}}}Description").text = (
        "Daily check-in for llmapi.isrc.ac.cn. Created by checkin.py."
    )

    triggers = ET.SubElement(task, f"{{{TASK_XML_NS}}}Triggers")
    trigger = ET.SubElement(triggers, f"{{{TASK_XML_NS}}}CalendarTrigger")
    ET.SubElement(trigger, f"{{{TASK_XML_NS}}}StartBoundary").text = (
        f"{now:%Y-%m-%d}T{hour:02d}:{minute:02d}:00"
    )
    ET.SubElement(trigger, f"{{{TASK_XML_NS}}}Enabled").text = "true"
    schedule = ET.SubElement(trigger, f"{{{TASK_XML_NS}}}ScheduleByDay")
    ET.SubElement(schedule, f"{{{TASK_XML_NS}}}DaysInterval").text = "1"

    principals = ET.SubElement(task, f"{{{TASK_XML_NS}}}Principals")
    principal = ET.SubElement(principals, f"{{{TASK_XML_NS}}}Principal", {"id": "Author"})
    ET.SubElement(principal, f"{{{TASK_XML_NS}}}UserId").text = user_sid
    ET.SubElement(principal, f"{{{TASK_XML_NS}}}LogonType").text = "InteractiveToken"
    ET.SubElement(principal, f"{{{TASK_XML_NS}}}RunLevel").text = "LeastPrivilege"

    settings = ET.SubElement(task, f"{{{TASK_XML_NS}}}Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("AllowHardTerminate", "true"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("AllowStartOnDemand", "true"),
        ("Enabled", "true"),
        ("Hidden", "false"),
        ("RunOnlyIfIdle", "false"),
        ("WakeToRun", "false"),
        ("ExecutionTimeLimit", "PT5M"),
        ("Priority", "7"),
    ):
        ET.SubElement(settings, f"{{{TASK_XML_NS}}}{name}").text = value

    actions = ET.SubElement(task, f"{{{TASK_XML_NS}}}Actions", {"Context": "Author"})
    execute = ET.SubElement(actions, f"{{{TASK_XML_NS}}}Exec")
    ET.SubElement(execute, f"{{{TASK_XML_NS}}}Command").text = str(python_executable)
    arguments = [str(script_path), "--quiet", "--log-file", str(log_file)]
    if user_id:
        arguments.extend(["--user-id", str(user_id)])
    ET.SubElement(execute, f"{{{TASK_XML_NS}}}Arguments").text = subprocess.list2cmdline(arguments)
    ET.SubElement(execute, f"{{{TASK_XML_NS}}}WorkingDirectory").text = str(script_path.parent)

    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def install_scheduled_task(schedule_time: str, *, user_id: str | None = None) -> str:
    hour, minute = parse_auto_time(schedule_time)
    task_xml = build_task_xml(
        f"{hour:02d}:{minute:02d}",
        user_sid=current_windows_user_sid(),
        user_id=user_id,
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as temporary:
            temporary.write(task_xml)
            temporary_path = Path(temporary.name)
        completed = subprocess.run(
            [
                "schtasks.exe",
                "/Create",
                "/TN",
                TASK_NAME,
                "/XML",
                str(temporary_path),
                "/F",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "创建计划任务失败")
    return f"{hour:02d}:{minute:02d}"


def remove_scheduled_task() -> bool:
    completed = subprocess.run(
        ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode == 0:
        return True
    combined = f"{completed.stdout}\n{completed.stderr}".lower()
    if "cannot find" in combined or "找不到" in combined or "不存在" in combined:
        return False
    raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "删除计划任务失败")


def query_scheduled_task() -> tuple[bool, str]:
    completed = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        return False, "尚未创建自动签到计划"
    return True, completed.stdout.strip()


def append_result_log(path: Path, result: Result) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        **asdict(result),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def resolve_token(cli_token: str | None, *, allow_prompt: bool = True) -> str:
    token = normalize_token(cli_token)
    if token:
        return token

    token = normalize_token(os.environ.get(TOKEN_ENV_NAME))
    if token:
        return token

    token = load_encrypted_token()
    if token:
        return token

    if allow_prompt and sys.stdin.isatty():
        return normalize_token(getpass.getpass("请输入个人访问令牌（输入内容不会显示）："))

    return ""


def request_json(
    base_url: str,
    path: str,
    token: str,
    *,
    method: str = "GET",
    user_id: str | None = None,
    timeout: float = 20,
) -> HttpResponse:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }
    if user_id:
        headers["New-Api-User"] = str(user_id).strip()

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=b"" if method == "POST" else None,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status = error.code
        text = error.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        return HttpResponse(0, None, str(reason))

    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            payload = None
    except json.JSONDecodeError:
        payload = None
    return HttpResponse(status, payload, text[:500])


def response_message(response: HttpResponse, fallback: str) -> str:
    payload = response.payload or {}
    message = payload.get("message") or payload.get("msg")
    if isinstance(message, str) and message.strip():
        return " ".join(message.split())[:240]
    if response.text.strip():
        return " ".join(response.text.split())[:240]
    return fallback


def is_auth_failure(response: HttpResponse) -> bool:
    if response.status in (401, 403):
        return True
    payload = response.payload or {}
    code = str(payload.get("code", "")).upper()
    message = response_message(response, "").lower()
    return code.startswith("AUTH_") or any(
        marker in message
        for marker in ("unauthorized", "invalid access token", "未登录", "未授权", "令牌失效")
    )


def extract_checked_in(payload: dict[str, Any] | None) -> bool:
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return False
    stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
    return bool(
        stats.get("checked_in_today")
        or data.get("checked_in_today")
        or data.get("checked_in")
    )


def extract_reward(payload: dict[str, Any] | None) -> int | float | None:
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return None
    for key in ("quota_awarded", "quota", "amount", "reward_amount"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def perform_checkin(
    token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    user_id: str | None = None,
    timeout: float = 20,
    status_only: bool = False,
) -> Result:
    month = datetime.now().strftime("%Y-%m")
    status_response = request_json(
        base_url,
        f"/api/user/checkin?{urllib.parse.urlencode({'month': month})}",
        token,
        user_id=user_id,
        timeout=timeout,
    )

    if status_response.status == 0:
        return Result("error", f"网络请求失败：{status_response.text}", http_status=0)
    if is_auth_failure(status_response):
        return Result(
            "login_required",
            "访问令牌无效、已过期或权限不足，请在网站的“安全与访问”中重新生成个人访问令牌",
            http_status=status_response.status,
        )
    if not status_response.payload or status_response.payload.get("success") is False:
        return Result(
            "error",
            f"查询签到状态失败：{response_message(status_response, f'HTTP {status_response.status}')}",
            http_status=status_response.status,
        )

    data = status_response.payload.get("data")
    if isinstance(data, dict) and data.get("enabled") is False:
        return Result("disabled", "网站当前未开启每日签到功能", http_status=status_response.status)

    checked = extract_checked_in(status_response.payload)
    if checked:
        return Result(
            "already",
            "今日已经签到，无需重复领取",
            checked_in=True,
            http_status=status_response.status,
        )
    if status_only:
        return Result(
            "status",
            "今日尚未签到",
            checked_in=False,
            http_status=status_response.status,
        )

    checkin_response = request_json(
        base_url,
        "/api/user/checkin",
        token,
        method="POST",
        user_id=user_id,
        timeout=timeout,
    )
    if checkin_response.status == 0:
        return Result(
            "error",
            "签到请求的结果未知。请先查询状态，不要立即重复提交："
            + checkin_response.text,
            http_status=0,
        )
    if is_auth_failure(checkin_response):
        return Result(
            "login_required",
            "访问令牌无效、已过期或权限不足",
            http_status=checkin_response.status,
        )

    message = response_message(checkin_response, "签到请求已完成")
    if any(marker in message.lower() for marker in ("今日已签到", "已经签到", "already")):
        return Result("already", message, checked_in=True, http_status=checkin_response.status)

    payload = checkin_response.payload or {}
    if 200 <= checkin_response.status < 300 and payload.get("success") is not False:
        return Result(
            "success",
            message,
            reward=extract_reward(payload),
            checked_in=True,
            http_status=checkin_response.status,
        )

    return Result(
        "error",
        f"签到失败：{message}",
        checked_in=False,
        http_status=checkin_response.status,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ISRC LLM API 命令行每日签到")
    parser.add_argument(
        "--token",
        help="个人访问令牌；不推荐使用，因为可能进入终端历史。优先使用隐藏输入或 --save-token",
    )
    parser.add_argument(
        "--user-id",
        default=os.environ.get(USER_ID_ENV_NAME),
        help=f"兼容旧版 New API 的用户 ID，也可通过 {USER_ID_ENV_NAME} 设置",
    )
    parser.add_argument("--status", action="store_true", help="只查询状态，不执行签到")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--quiet", action="store_true", help="成功时不输出，适合计划任务")
    parser.add_argument("--timeout", type=float, default=20, help="请求超时秒数，默认 20")
    parser.add_argument("--save-token", action="store_true", help="隐藏输入令牌并使用 Windows DPAPI 加密保存")
    parser.add_argument("--clear-token", action="store_true", help="删除本程序保存的加密令牌")
    auto_group = parser.add_mutually_exclusive_group()
    auto_group.add_argument(
        "--auto",
        metavar="HH:MM",
        help="创建或更新 Windows 每日计划任务，例如 --auto 12:00",
    )
    auto_group.add_argument("--auto-off", action="store_true", help="删除每日自动签到计划")
    auto_group.add_argument("--auto-status", action="store_true", help="查看自动签到计划状态")
    parser.add_argument("--log-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=argparse.SUPPRESS,
    )
    return parser


def print_result(result: Result, *, json_output: bool, quiet: bool) -> None:
    if quiet and result.exit_code == 0:
        return
    if json_output:
        print(json.dumps(asdict(result), ensure_ascii=False))
        return

    icon = {
        "success": "✅",
        "already": "✅",
        "status": "ℹ️",
        "login_required": "🔑",
        "disabled": "⏸️",
        "error": "❌",
    }.get(result.status, "ℹ️")
    reward_text = f"；奖励额度：{result.reward}" if result.reward is not None else ""
    print(f"{icon} {result.message}{reward_text}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.auto_off:
        try:
            removed = remove_scheduled_task()
        except Exception as error:
            print(f"删除自动计划失败：{error}", file=sys.stderr)
            return 3
        print("已删除每日自动签到计划" if removed else "没有已创建的自动签到计划")
        return 0

    if args.auto_status:
        try:
            exists, details = query_scheduled_task()
        except Exception as error:
            print(f"查询自动计划失败：{error}", file=sys.stderr)
            return 3
        print(details)
        if exists:
            print(f"\n执行日志：{LOG_FILE}")
        return 0

    if args.clear_token:
        if CREDENTIAL_FILE.exists():
            CREDENTIAL_FILE.unlink()
            print(f"已删除加密凭据：{CREDENTIAL_FILE}")
        else:
            print("没有已保存的加密凭据")
        return 0

    if args.save_token:
        token = normalize_token(getpass.getpass("请输入要加密保存的个人访问令牌："))
        if not token:
            print("错误：令牌不能为空", file=sys.stderr)
            return 2
        try:
            save_encrypted_token(token)
        except Exception as error:
            print(f"保存失败：{error}", file=sys.stderr)
            return 3
        print(f"令牌已用 Windows DPAPI 加密保存到：{CREDENTIAL_FILE}")
        print("该文件只能由当前 Windows 用户在本机解密。")
        return 0

    try:
        token = resolve_token(args.token)
    except Exception as error:
        if args.log_file:
            try:
                append_result_log(args.log_file, Result("login_required", f"读取令牌失败：{error}"))
            except Exception:
                pass
        print(f"读取令牌失败：{error}", file=sys.stderr)
        return 2
    if not token:
        if args.log_file:
            try:
                append_result_log(args.log_file, Result("login_required", "未提供个人访问令牌"))
            except Exception:
                pass
        print(
            "错误：未提供个人访问令牌。请直接运行后隐藏输入，或先运行 python checkin.py --save-token。",
            file=sys.stderr,
        )
        return 2

    if args.auto:
        validation = perform_checkin(
            token,
            base_url=args.base_url,
            user_id=args.user_id,
            timeout=args.timeout,
            status_only=True,
        )
        if validation.exit_code != 0:
            print_result(validation, json_output=False, quiet=False)
            print("未创建计划任务：请先解决上面的令牌或网络问题。", file=sys.stderr)
            return validation.exit_code
        try:
            # The scheduled task cannot use a one-off prompt or process-local
            # environment variable, so persist the exact token just validated.
            save_encrypted_token(token)
        except Exception as error:
            print(f"保存加密令牌失败：{error}", file=sys.stderr)
            return 3
        try:
            normalized_time = install_scheduled_task(args.auto, user_id=args.user_id)
        except Exception as error:
            print(f"创建自动计划失败：{error}", file=sys.stderr)
            return 3
        print(f"✅ 已创建每日 {normalized_time} 的自动签到计划")
        print("脚本不会常驻后台；Windows 到点时才启动它，完成后立即退出。")
        print("电脑关机时不会被唤醒；错过时间后会在下次可运行时补执行。")
        print(f"执行日志：{LOG_FILE}")
        return 0

    result = perform_checkin(
        token,
        base_url=args.base_url,
        user_id=args.user_id,
        timeout=args.timeout,
        status_only=args.status,
    )
    if args.log_file:
        try:
            append_result_log(args.log_file, result)
        except Exception as error:
            if not args.quiet:
                print(f"警告：无法写入执行日志：{error}", file=sys.stderr)
    print_result(result, json_output=args.json, quiet=args.quiet)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
