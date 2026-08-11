"""
任务日志路由
处理日志列表、详情查看、下载、分页筛选
"""
import io
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, render_template, request, Response
from flask_login import login_required
from sqlalchemy import func, case, and_

from app.extensions import db
from app.models.task import ScriptTask
from app.models.log import TaskLog
from app.utils.response import success_response, error_response

logger = logging.getLogger(__name__)

log_bp = Blueprint(
    "log", __name__,
    template_folder="../templates/logs",
)


@log_bp.route("/logs")
@login_required
def log_home_page() -> str:
    """日志列表首页"""
    return render_template("logs/home.html")


@log_bp.route("/task/<int:task_id>/logs")
@login_required
def log_list_page(task_id: int) -> Any:
    """日志列表页面"""
    task = db.session.get(ScriptTask, task_id)
    if not task:
        return "任务不存在", 404
    return render_template("logs/list.html", task=task)


@log_bp.route("/api/task/stats")
@login_required
def task_stats_api() -> Any:
    """
    获取所有任务的执行统计信息
    返回每个任务的成功/失败/超时/运行中/已停止次数
    """
    try:
        # 聚合查询：按 task_id 和 status 分组统计
        stats_query = db.session.query(
            TaskLog.task_id,
            TaskLog.status,
            func.count(TaskLog.id).label('count')
        ).group_by(TaskLog.task_id, TaskLog.status).all()

        # 构建统计字典
        stats = {}
        for task_id, status, count in stats_query:
            if task_id not in stats:
                stats[task_id] = {
                    'success': 0, 'failed': 0, 'timeout': 0,
                    'running': 0, 'stopped': 0, 'total': 0
                }
            if status in stats[task_id]:
                stats[task_id][status] = count
            stats[task_id]['total'] += count

        return success_response(data=stats)

    except Exception as e:
        logger.error(f"[Log] 获取任务统计异常: {e}", exc_info=True)
        return error_response("获取统计失败", code=500)


@log_bp.route("/api/task/<int:task_id>/logs")
@login_required
def log_list_api(task_id: int) -> Any:
    """
    获取指定任务的日志列表（分页 + 状态筛选）
    GET 参数：page, per_page, status
    """
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        page: int = request.args.get("page", 1, type=int)
        per_page: int = request.args.get("per_page", 10, type=int)
        status: str = request.args.get("status", "", type=str).strip()

        page = max(1, page)
        per_page = min(max(1, per_page), 100)

        query = TaskLog.query.filter_by(task_id=task_id)

        # 状态筛选
        valid_statuses = {"running", "success", "failed", "timeout", "stopped"}
        if status and status in valid_statuses:
            query = query.filter_by(status=status)

        query = query.order_by(TaskLog.start_time.desc())
        pagination = query.paginate(
            page=page, per_page=per_page, error_out=False
        )

        return success_response(data={
            "items": [log.to_dict() for log in pagination.items],
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages,
            "task_name": task.task_name,
        })

    except Exception as e:
        logger.error(f"[Log] 获取日志列表异常: {e}", exc_info=True)
        return error_response("获取日志列表失败", code=500)


@log_bp.route("/api/log/<int:log_id>")
@login_required
def log_detail(log_id: int) -> Any:
    """获取单条日志详情（含完整日志内容）"""
    try:
        task_log = db.session.get(TaskLog, log_id)
        if not task_log:
            return error_response("日志不存在", code=400)

        return success_response(data={"log": task_log.to_dict()})

    except Exception as e:
        logger.error(f"[Log] 获取日志详情异常: {e}", exc_info=True)
        return error_response("获取日志详情失败", code=500)


@log_bp.route("/api/log/<int:log_id>/download")
@login_required
def log_download(log_id: int) -> Any:
    """
    下载日志内容为 .log 文件
    """
    try:
        task_log = db.session.get(TaskLog, log_id)
        if not task_log:
            return error_response("日志不存在", code=400)

        task = db.session.get(ScriptTask, task_log.task_id)
        task_name = task.task_name if task else "unknown"

        # 构建文件内容
        header = (
            f"{'=' * 60}\n"
            f"任务名称: {task_name}\n"
            f"日志 ID: {task_log.id}\n"
            f"触发类型: {task_log.trigger_type}\n"
            f"执行状态: {task_log.status}\n"
            f"开始时间: {task_log.start_time}\n"
            f"结束时间: {task_log.end_time or 'N/A'}\n"
            f"{'=' * 60}\n\n"
        )
        content = header + (task_log.log_content or "（无日志内容）")

        # 生成文件名（URL编码支持中文）
        timestamp = task_log.start_time.strftime("%Y%m%d_%H%M%S")
        filename = f"{task_name}_{timestamp}.log"
        filename_encoded = urllib.parse.quote(filename)

        # 返回文件下载响应（使用 RFC 5987 编码支持中文文件名）
        return Response(
            content.encode("utf-8"),
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": (
                    f"attachment; "
                    f"filename=\"{filename_encoded}\"; "
                    f"filename*=UTF-8''{filename_encoded}"
                ),
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Length": str(len(content.encode("utf-8"))),
            },
        )

    except Exception as e:
        logger.error(f"[Log] 下载日志异常: {e}", exc_info=True)
        return error_response("下载日志失败", code=500)


# ============================================================
# 任务执行历史统计 API
# ============================================================

@log_bp.route("/api/task/<int:task_id>/stats/detail")
@login_required
def task_detail_stats(task_id: int) -> Any:
    """
    获取单个任务的详细执行统计
    返回：执行次数、成功率、平均耗时、最近执行列表
    """
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        # 基础统计
        total = TaskLog.query.filter_by(task_id=task_id).count()
        success_count = TaskLog.query.filter_by(task_id=task_id, status="success").count()
        failed_count = TaskLog.query.filter_by(task_id=task_id, status="failed").count()
        timeout_count = TaskLog.query.filter_by(task_id=task_id, status="timeout").count()

        # 计算平均耗时（仅统计已完成的）
        avg_duration = db.session.query(
            func.avg(
                func.timestampdiff(
                    db.text("SECOND"),
                    TaskLog.start_time,
                    TaskLog.end_time
                )
            )
        ).filter(
            TaskLog.task_id == task_id,
            TaskLog.end_time.isnot(None),
            TaskLog.status.in_(["success", "failed", "timeout"])
        ).scalar()

        # 计算成功率
        completed = success_count + failed_count + timeout_count
        success_rate = round(success_count / completed * 100, 1) if completed > 0 else 0

        return success_response(data={
            "task_name": task.task_name,
            "total": total,
            "success": success_count,
            "failed": failed_count,
            "timeout": timeout_count,
            "success_rate": success_rate,
            "avg_duration": round(avg_duration, 1) if avg_duration else 0,
        })

    except Exception as e:
        logger.error(f"[Log] 获取任务详细统计异常: {e}", exc_info=True)
        return error_response("获取统计失败", code=500)


@log_bp.route("/api/task/<int:task_id>/stats/report")
@login_required
def task_execution_report(task_id: int) -> Any:
    """
    按日/周/月维度的执行报告
    GET 参数：period=day/week/month, days=30(默认30天)
    """
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        period = request.args.get("period", "day", type=str)
        days = request.args.get("days", 30, type=int)
        days = min(days, 365)

        start_date = datetime.now() - timedelta(days=days)

        # 按日期分组统计
        logs = TaskLog.query.filter(
            TaskLog.task_id == task_id,
            TaskLog.start_time >= start_date
        ).order_by(TaskLog.start_time.asc()).all()

        # 构建报告数据
        report = {}
        for log in logs:
            if period == "day":
                key = log.start_time.strftime("%Y-%m-%d")
            elif period == "week":
                # ISO 周数
                iso = log.start_time.isocalendar()
                key = f"{iso[0]}-W{iso[1]:02d}"
            elif period == "month":
                key = log.start_time.strftime("%Y-%m")
            else:
                key = log.start_time.strftime("%Y-%m-%d")

            if key not in report:
                report[key] = {
                    "period": key,
                    "total": 0, "success": 0, "failed": 0,
                    "timeout": 0, "running": 0, "stopped": 0,
                    "total_duration": 0,
                }

            r = report[key]
            r["total"] += 1
            if log.status in r:
                r[log.status] += 1

            # 累加耗时
            if log.end_time and log.start_time:
                duration = (log.end_time - log.start_time).total_seconds()
                r["total_duration"] += duration

        # 计算平均耗时并转为列表
        result = []
        for key in sorted(report.keys()):
            r = report[key]
            completed = r["success"] + r["failed"] + r["timeout"]
            r["avg_duration"] = round(r["total_duration"] / completed, 1) if completed > 0 else 0
            r["success_rate"] = round(r["success"] / completed * 100, 1) if completed > 0 else 0
            del r["total_duration"]  # 不需要返回
            result.append(r)

        return success_response(data=result)

    except Exception as e:
        logger.error(f"[Log] 获取执行报告异常: {e}", exc_info=True)
        return error_response("获取报告失败", code=500)


@log_bp.route("/api/task/<int:task_id>/stats/duration")
@login_required
def task_duration_distribution(task_id: int) -> Any:
    """
    任务耗时分布图数据
    返回：耗时区间分布（<1s, 1-5s, 5-30s, 30s-1m, 1-5m, 5-30m, >30m）
    """
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        # 获取所有已完成日志的耗时
        logs = TaskLog.query.filter(
            TaskLog.task_id == task_id,
            TaskLog.end_time.isnot(None),
            TaskLog.status.in_(["success", "failed", "timeout"])
        ).all()

        # 耗时区间
        buckets = {
            "<1s": 0,
            "1-5s": 0,
            "5-30s": 0,
            "30s-1m": 0,
            "1-5m": 0,
            "5-30m": 0,
            ">30m": 0,
        }

        durations = []
        for log in logs:
            duration = (log.end_time - log.start_time).total_seconds()
            durations.append(round(duration, 1))

            if duration < 1:
                buckets["<1s"] += 1
            elif duration < 5:
                buckets["1-5s"] += 1
            elif duration < 30:
                buckets["5-30s"] += 1
            elif duration < 60:
                buckets["30s-1m"] += 1
            elif duration < 300:
                buckets["1-5m"] += 1
            elif duration < 1800:
                buckets["5-30m"] += 1
            else:
                buckets[">30m"] += 1

        # 基本统计
        if durations:
            min_d = min(durations)
            max_d = max(durations)
            avg_d = round(sum(durations) / len(durations), 1)
            median_d = sorted(durations)[len(durations) // 2]
        else:
            min_d = max_d = avg_d = median_d = 0

        return success_response(data={
            "distribution": buckets,
            "min_duration": min_d,
            "max_duration": max_d,
            "avg_duration": avg_d,
            "median_duration": median_d,
            "total_records": len(durations),
        })

    except Exception as e:
        logger.error(f"[Log] 获取耗时分布异常: {e}", exc_info=True)
        return error_response("获取耗时分布失败", code=500)


@log_bp.route("/task/<int:task_id>/stats")
@login_required
def task_stats_page(task_id: int) -> Any:
    """任务统计页面"""
    task = db.session.get(ScriptTask, task_id)
    if not task:
        return "任务不存在", 404
    return render_template("stats.html", task=task)
