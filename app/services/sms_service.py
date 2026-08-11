"""
短信通知服务
支持配置短信接口，任务失败时发送通知
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

from app.extensions import db
from app.models.system_config import SystemConfig

logger = logging.getLogger(__name__)

# ============================================================
# 配置键常量
# ============================================================
SMS_ENABLED_KEY = "sms_enabled"           # 全局开关: "true"/"false"
SMS_API_URL_KEY = "sms_api_url"           # 短信接口 URL
SMS_API_METHOD_KEY = "sms_api_method"     # 请求方法: "POST"/"GET"
SMS_API_HEADERS_KEY = "sms_api_headers"   # 请求头 JSON
SMS_API_BODY_KEY = "sms_api_body"         # 请求体模板 JSON（支持占位符）
SMS_API_PHONES_KEY = "sms_api_phones"     # 接收手机号（逗号分隔）
SMS_SIGN_KEY = "sms_sign"                 # 短信签名
SMS_CONTENT_MAX_LEN_KEY = "sms_content_max_len"  # 短信内容截取长度

# ============================================================
# 短信记录（内存存储）
# ============================================================
_sms_history: List[Dict[str, Any]] = []
_SMS_MAX_RECORDS = 500
_SMS_RETENTION_DAYS = 7
_sms_lock = threading.Lock()


def get_config_value(key: str, default: str = "") -> str:
    """获取系统配置值"""
    try:
        cfg = SystemConfig.query.filter_by(config_key=key).first()
        return cfg.config_value if cfg and cfg.config_value else default
    except Exception:
        return default


def set_config_value(key: str, value: str, description: str = "") -> None:
    """设置系统配置值"""
    try:
        cfg = SystemConfig.query.filter_by(config_key=key).first()
        if cfg:
            cfg.config_value = value
        else:
            cfg = SystemConfig(config_key=key, config_value=value, description=description)
            db.session.add(cfg)
        db.session.commit()
    except Exception as e:
        logger.error(f"[SMS] 保存配置 {key} 失败: {e}")
        db.session.rollback()


def is_sms_enabled() -> bool:
    """检查全局短信开关是否开启"""
    return get_config_value(SMS_ENABLED_KEY, "false").lower() == "true"


def should_notify_task(task) -> bool:
    """
    判断任务是否需要短信通知
    优先级：任务级 sms_notify > 全局开关
    """
    # 任务级配置优先
    if task.sms_notify is True:
        return True
    if task.sms_notify is False:
        return False
    # 跟随全局配置
    return is_sms_enabled()


def send_failure_sms(task_name: str, error_msg: str = "", retry_info: str = "") -> bool:
    """
    发送任务失败短信通知

    :param task_name: 任务名称
    :param error_msg: 错误信息（截取前100字符）
    :param retry_info: 重试信息（如 "已重试3次"）
    :return: True=发送成功
    """
    try:
        if not is_sms_enabled():
            logger.debug(f"[SMS] 全局短信开关关闭，跳过通知: {task_name}")
            return False

        api_url = get_config_value(SMS_API_URL_KEY)
        if not api_url:
            logger.warning("[SMS] 短信接口 URL 未配置")
            return False

        method = get_config_value(SMS_API_METHOD_KEY, "POST").upper()
        phones = get_config_value(SMS_API_PHONES_KEY)
        sign = get_config_value(SMS_SIGN_KEY, "任务管理系统")

        if not phones:
            logger.warning("[SMS] 接收手机号未配置")
            return False

        # 清洗错误信息：去除换行、压缩空白、截断
        clean_error = (error_msg or "").replace("\n", " ").replace("\r", "")
        import re as _re
        clean_error = _re.sub(r'\s+', ' ', clean_error).strip()

        content_max_len = int(get_config_value(SMS_CONTENT_MAX_LEN_KEY, "200"))
        if content_max_len < 10:
            content_max_len = 10

        # 构建短信内容
        content = f"【{sign}】任务失败提醒：{task_name}"
        if retry_info:
            content += f"，{retry_info}"
        if clean_error:
            content += f"，错误：{clean_error}"
        if len(content) > content_max_len:
            content = content[:content_max_len - 3] + "..."

        # 解析请求头
        headers_str = get_config_value(SMS_API_HEADERS_KEY, "{}")
        try:
            headers = json.loads(headers_str) if headers_str else {}
        except json.JSONDecodeError:
            headers = {}
        # 确保有 Content-Type
        if "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

        # 解析请求体模板并替换占位符
        body_str = get_config_value(SMS_API_BODY_KEY, "{}")
        try:
            body = json.loads(body_str) if body_str else {}
        except json.JSONDecodeError:
            body = {}

        # 占位符替换（支持嵌套字典）
        def replace_placeholders(obj):
            if isinstance(obj, str):
                return (
                    obj.replace("{phones}", phones)
                    .replace("{content}", content)
                    .replace("{task_name}", task_name)
                    .replace("{sign}", sign)
                    .replace("{error}", clean_error)
                )
            elif isinstance(obj, dict):
                return {k: replace_placeholders(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [replace_placeholders(item) for item in obj]
            return obj

        body = replace_placeholders(body)
        # 如果 body 为空，使用默认格式
        if not body:
            body = {
                "phones": phones,
                "content": content,
                "sign": sign,
            }

        # 发送请求
        if method == "GET":
            resp = requests.get(api_url, params=body, headers=headers, timeout=10)
        else:
            resp = requests.post(api_url, json=body, headers=headers, timeout=10)

        if resp.status_code == 200:
            logger.info(f"[SMS] 短信发送成功: {task_name} -> {phones}")
            _add_sms_record(task_name, phones, content, "success")
            return True
        else:
            logger.error(
                f"[SMS] 短信发送失败: HTTP {resp.status_code}, "
                f"body={resp.text[:200]}"
            )
            _add_sms_record(task_name, phones, content, "failed")
            return False

    except requests.RequestException as e:
        logger.error(f"[SMS] 短信发送网络异常: {e}")
        _add_sms_record(task_name, phones or "", content or "", "error")
        return False
    except Exception as e:
        logger.error(f"[SMS] 短信发送异常: {e}", exc_info=True)
        return False


def get_all_sms_config() -> dict:
    """获取所有短信相关配置"""
    return {
        SMS_ENABLED_KEY: get_config_value(SMS_ENABLED_KEY, "false"),
        SMS_API_URL_KEY: get_config_value(SMS_API_URL_KEY),
        SMS_API_METHOD_KEY: get_config_value(SMS_API_METHOD_KEY, "POST"),
        SMS_API_HEADERS_KEY: get_config_value(SMS_API_HEADERS_KEY, "{}"),
        SMS_API_BODY_KEY: get_config_value(SMS_API_BODY_KEY, "{}"),
        SMS_API_PHONES_KEY: get_config_value(SMS_API_PHONES_KEY),
        SMS_SIGN_KEY: get_config_value(SMS_SIGN_KEY, "任务管理系统"),
        SMS_CONTENT_MAX_LEN_KEY: get_config_value(SMS_CONTENT_MAX_LEN_KEY, "200"),
    }


def _add_sms_record(
    task_name: str, phones: str, content: str, status: str
) -> None:
    """添加一条短信记录（线程安全）"""
    try:
        with _sms_lock:
            _cleanup_expired_records()
            record = {
                "id": len(_sms_history) + 1 if _sms_history else 1,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "body": {
                    "phones": phones,
                    "content": content,
                    "task_name": task_name,
                },
                "status": status,
            }
            _sms_history.insert(0, record)
            while len(_sms_history) > _SMS_MAX_RECORDS:
                _sms_history.pop()
    except Exception as e:
        logger.error(f"[SMS] 记录短信失败: {e}")


def _cleanup_expired_records() -> None:
    """清理超过保留天数的记录（调用方需持有锁）"""
    global _sms_history
    if not _sms_history:
        return
    cutoff = (datetime.now() - timedelta(days=_SMS_RETENTION_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    _sms_history[:] = [r for r in _sms_history if r.get("time", "") >= cutoff]


def get_sms_records(
    page: int = 1,
    per_page: int = 15,
    keyword: str = "",
) -> Dict[str, Any]:
    """获取短信记录（分页+搜索+自动清理过期）"""
    with _sms_lock:
        _cleanup_expired_records()
        records = _sms_history

    if keyword:
        kw = keyword.lower()
        records = [
            r
            for r in records
            if kw in json.dumps(r.get("body", {}), ensure_ascii=False).lower()
            or kw in r.get("time", "").lower()
            or kw in r.get("status", "").lower()
        ]

    total = len(records)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start = (page - 1) * per_page
    items = records[start : start + per_page]

    return {
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "items": items,
    }


def clear_sms_records() -> None:
    """清空所有短信记录"""
    with _sms_lock:
        _sms_history.clear()


def get_all_sms_records() -> List[Dict[str, Any]]:
    """获取全部短信记录（用于导出）"""
    with _sms_lock:
        return list(_sms_history)