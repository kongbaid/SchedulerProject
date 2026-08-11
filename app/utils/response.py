"""
统一 JSON 响应格式模块
所有 AJAX 接口统一返回格式：{code, msg, data}
"""
from typing import Any, Optional
from flask import jsonify


def success_response(
    msg: str = "操作成功",
    data: Optional[Any] = None,
    code: int = 200
) -> Any:
    """
    构建成功响应
    :param msg:  提示信息
    :param data: 返回数据
    :param code: HTTP 状态码
    :return: Flask JSON 响应对象
    """
    return jsonify({"code": code, "msg": msg, "data": {} if data is None else data}), code


def error_response(
    msg: str = "操作失败",
    code: int = 500,
    data: Optional[Any] = None
) -> Any:
    """
    构建错误响应
    :param msg:  错误信息
    :param code: 错误码（400=参数错误, 401=未登录, 500=服务器异常）
    :param data: 附加数据
    :return: Flask JSON 响应对象
    """
    return jsonify({"code": code, "msg": msg, "data": {} if data is None else data}), code
