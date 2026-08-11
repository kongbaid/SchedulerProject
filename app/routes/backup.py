"""
备份模块路由
- 页面路由: /backup/
- API路由: /backup/api/...
"""
import os
import logging
from datetime import datetime

from flask import Blueprint, request, render_template
from flask_login import login_required, current_user

from app.utils.response import success_response, error_response
from app.models.backup import BackupTarget, BackupRecord
from app.models.user import ROLE_ADMIN
from app.extensions import db
from app.services.backup_service import (
    create_backup, restore_backup, delete_backup,
    validate_source_path, validate_target_path, format_size,
)

logger = logging.getLogger(__name__)

backup_bp = Blueprint('backup', __name__, url_prefix='/backup')


def _admin_required(f):
    """装饰器：仅允许超级管理员访问"""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if current_user.role != ROLE_ADMIN:
            return error_response("仅超级管理员可访问此功能", code=403)
        return f(*args, **kwargs)
    return decorated


# ============================================================
# 页面路由
# ============================================================

@backup_bp.route('/')
@login_required
@_admin_required
def backup_page():
    """备份管理页面"""
    return render_template('backup/index.html')


# ============================================================
# 备份目标目录管理 API
# ============================================================

@backup_bp.route('/api/targets', methods=['GET'])
@login_required
@_admin_required
def list_targets():
    """获取所有备份目录"""
    targets = BackupTarget.query.order_by(BackupTarget.id.desc()).all()
    return success_response("查询成功", [t.to_dict() for t in targets])


@backup_bp.route('/api/target', methods=['POST'])
@login_required
@_admin_required
def add_target():
    """新增备份目录"""
    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    target_path = (data.get('target_path') or '').strip()
    description = (data.get('description') or '').strip()
    is_default = 1 if data.get('is_default') else 0

    if not target_name:
        return error_response("目录别名不能为空", code=400)
    if not target_path:
        return error_response("备份路径不能为空", code=400)

    # 校验路径并自动创建目录
    ok, result = validate_target_path(target_path)
    if not ok:
        return error_response(result, code=400)

    # 检查重复
    existing = BackupTarget.query.filter_by(target_path=result).first()
    if existing:
        return error_response("该备份路径已存在", code=400)

    # 如果设为默认，先取消其他默认
    if is_default:
        BackupTarget.query.filter_by(is_default=1).update({'is_default': 0})

    target = BackupTarget(
        target_name=target_name,
        target_path=result,
        description=description or None,
        is_default=is_default,
        created_by=current_user.username,
    )
    db.session.add(target)
    db.session.commit()
    logger.info(f"[Backup] 新增备份目录: {target_name} -> {result}")
    return success_response("新增成功", target.to_dict())


@backup_bp.route('/api/target/<int:tid>', methods=['PUT'])
@login_required
@_admin_required
def update_target(tid):
    """编辑备份目录"""
    target = BackupTarget.query.get(tid)
    if not target:
        return error_response("备份目录不存在", code=404)

    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    target_path = (data.get('target_path') or '').strip()
    description = (data.get('description') or '').strip()
    is_default = 1 if data.get('is_default') else 0

    if target_name:
        target.target_name = target_name

    if target_path and target_path != target.target_path:
        ok, result = validate_target_path(target_path)
        if not ok:
            return error_response(result, code=400)
        # 检查重复
        existing = BackupTarget.query.filter_by(target_path=result).first()
        if existing and existing.id != tid:
            return error_response("该备份路径已存在", code=400)
        target.target_path = result

    target.description = description or None
    target.updated_at = datetime.now()

    if is_default:
        BackupTarget.query.filter_by(is_default=1).update({'is_default': 0})
        target.is_default = 1
    else:
        target.is_default = 0

    db.session.commit()
    logger.info(f"[Backup] 编辑备份目录: {target.target_name}")
    return success_response("修改成功", target.to_dict())


@backup_bp.route('/api/target/<int:tid>', methods=['DELETE'])
@login_required
@_admin_required
def delete_target(tid):
    """删除备份目录（仅删除配置，不删除已备份的文件）"""
    target = BackupTarget.query.get(tid)
    if not target:
        return error_response("备份目录不存在", code=404)

    # 检查是否有关联的备份记录
    record_count = BackupRecord.query.filter_by(target_id=tid).count()
    if record_count > 0:
        return error_response(
            f"该目录下有 {record_count} 条备份记录，请先删除备份记录",
            code=400
        )

    db.session.delete(target)
    db.session.commit()
    logger.info(f"[Backup] 删除备份目录: {target.target_name}")
    return success_response("删除成功")


# ============================================================
# 备份操作 API
# ============================================================

@backup_bp.route('/api/create', methods=['POST'])
@login_required
@_admin_required
def create_backup_api():
    """创建备份"""
    data = request.get_json(silent=True) or {}
    source_path = (data.get('source_path') or '').strip()
    source_type = (data.get('source_type') or 'folder').strip()
    source_name = (data.get('source_name') or '').strip()
    target_id = data.get('target_id')
    record_name = (data.get('record_name') or '').strip()

    if not source_path:
        return error_response("源路径不能为空", code=400)
    if not source_name:
        source_name = os.path.basename(source_path) or 'unnamed'
    if not target_id:
        # 使用默认目标目录
        target = BackupTarget.query.filter_by(is_default=1).first()
        if not target:
            target = BackupTarget.query.first()
        if not target:
            return error_response("请先添加备份目标目录", code=400)
        target_id = target.id
    else:
        target_id = int(target_id)

    result = create_backup(
        source_path=source_path,
        source_type=source_type,
        source_name=source_name,
        target_id=target_id,
        record_name=record_name,
        created_by=current_user.username,
    )

    if result['success']:
        return success_response(result['msg'], result.get('data'))
    else:
        return error_response(result['msg'], code=400)


@backup_bp.route('/api/records', methods=['GET'])
@login_required
@_admin_required
def list_records():
    """备份记录列表（分页 + 搜索 + 筛选）"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 15, type=int)
    keyword = (request.args.get('keyword') or '').strip()
    source_type = (request.args.get('source_type') or '').strip()
    target_id = request.args.get('target_id', type=int)

    query = BackupRecord.query

    if keyword:
        query = query.filter(
            db.or_(
                BackupRecord.record_name.contains(keyword),
                BackupRecord.source_path.contains(keyword),
                BackupRecord.source_name.contains(keyword),
            )
        )

    if source_type:
        query = query.filter(BackupRecord.source_type == source_type)

    if target_id:
        query = query.filter(BackupRecord.target_id == target_id)

    query = query.order_by(BackupRecord.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    records = [r.to_dict() for r in pagination.items]
    for rec in records:
        rec['backup_size_display'] = format_size(rec.get('backup_size', 0))

    return success_response("查询成功", {
        'records': records,
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page,
        'per_page': per_page,
    })


@backup_bp.route('/api/record/<int:rid>', methods=['GET'])
@login_required
@_admin_required
def get_record(rid):
    """备份记录详情"""
    record = BackupRecord.query.get(rid)
    if not record:
        return error_response("备份记录不存在", code=404)
    data = record.to_dict()
    data['backup_size_display'] = format_size(data.get('backup_size', 0))
    # 检查备份文件是否存在
    data['file_exists'] = os.path.exists(record.backup_file_path)
    return success_response("查询成功", data)


@backup_bp.route('/api/record/<int:rid>/restore', methods=['POST'])
@login_required
@_admin_required
def restore_record(rid):
    """恢复备份"""
    result = restore_backup(rid)
    if result['success']:
        return success_response(result['msg'])
    else:
        return error_response(result['msg'], code=400)


@backup_bp.route('/api/record/<int:rid>', methods=['DELETE'])
@login_required
@_admin_required
def delete_record(rid):
    """删除备份记录"""
    result = delete_backup(rid)
    if result['success']:
        return success_response(result['msg'])
    else:
        return error_response(result['msg'], code=400)


@backup_bp.route('/api/validate-source', methods=['POST'])
@login_required
@_admin_required
def validate_source():
    """校验源路径并返回预估信息（前端备份弹框预览用）"""
    data = request.get_json(silent=True) or {}
    source_path = (data.get('source_path') or '').strip()

    if not source_path:
        return error_response("源路径不能为空", code=400)

    ok, result = validate_source_path(source_path)
    if not ok:
        return error_response(result, code=400)

    abs_source = result
    from app.services.backup_service import _collect_files
    files, total_size = _collect_files(abs_source)

    source_type = 'file' if os.path.isfile(abs_source) else 'folder'
    source_name = os.path.basename(abs_source) or abs_source

    return success_response("校验通过", {
        'source_path': abs_source,
        'source_type': source_type,
        'source_name': source_name,
        'file_count': len(files),
        'total_size': total_size,
        'total_size_display': format_size(total_size),
    })


# ============================================================
# 备份排除规则 API
# ============================================================

@backup_bp.route('/api/exclude-patterns', methods=['GET'])
@login_required
@_admin_required
def get_exclude_patterns():
    """获取备份排除规则"""
    from app.services.backup_service import get_exclude_patterns as svc_get_patterns
    from app.services.backup_service import DEFAULT_EXCLUDE_PATTERNS
    patterns = svc_get_patterns()
    return success_response("查询成功", {
        'patterns': patterns,
        'defaults': list(DEFAULT_EXCLUDE_PATTERNS),
    })


@backup_bp.route('/api/exclude-patterns', methods=['POST'])
@login_required
@_admin_required
def update_exclude_patterns():
    """更新备份排除规则"""
    import json
    from app.models.system_config import SystemConfig

    data = request.get_json(silent=True) or {}
    patterns = data.get('patterns')
    if not isinstance(patterns, list):
        return error_response("patterns 必须是数组", code=400)

    # 清洗：去空、去重
    cleaned = []
    seen = set()
    for p in patterns:
        p = str(p).strip()
        if p and p not in seen:
            cleaned.append(p)
            seen.add(p)

    if len(cleaned) > 100:
        return error_response("排除规则最多 100 条", code=400)

    # 存入 system_config
    config = SystemConfig.query.filter_by(config_key='backup_exclude_patterns').first()
    if config:
        config.config_value = json.dumps(cleaned, ensure_ascii=False)
    else:
        config = SystemConfig(
            config_key='backup_exclude_patterns',
            config_value=json.dumps(cleaned, ensure_ascii=False),
            description='备份排除规则（JSON数组，匹配目录名/文件名）',
        )
        db.session.add(config)

    db.session.commit()
    logger.info(f"[Backup] 更新排除规则: {cleaned}")
    return success_response("保存成功", {'patterns': cleaned})

