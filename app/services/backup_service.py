"""
备份服务层
核心功能：创建备份、恢复备份、路径安全校验
使用 Python 标准库 zipfile + shutil，无需额外依赖
"""
import os
import json
import shutil
import zipfile
import logging
from datetime import datetime
from typing import Tuple, List

from app.extensions import db
from app.models.backup import BackupTarget, BackupRecord

logger = logging.getLogger(__name__)

# 默认排除的目录/文件名（用户可在备份管理页覆盖）
DEFAULT_EXCLUDE_PATTERNS = ['__pycache__', '.git', 'node_modules', '.env', '.idea', '.vscode']

# 单次备份最大总大小（500MB），防止误操作备份超大目录
MAX_BACKUP_TOTAL_SIZE = 500 * 1024 * 1024

# 允许访问的根目录列表（与 file_editor 保持一致）
ALLOWED_ROOT_DIRS = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')),
    os.path.abspath(os.path.expanduser('~')),
    'D:\\',
    'C:\\',
    'E:\\',
    'F:\\',
]


def get_exclude_patterns():
    """
    从数据库读取用户配置的排除规则，未配置则返回默认值
    排除规则存储在 system_config 表中，key = backup_exclude_patterns
    值为 JSON 数组，如 ["__pycache__", ".git", "*.tmp", "logs/"]
    """
    try:
        from app.models.system_config import SystemConfig
        config = SystemConfig.query.filter_by(config_key='backup_exclude_patterns').first()
        if config and config.config_value:
            import json as _json
            patterns = _json.loads(config.config_value)
            if isinstance(patterns, list):
                return [str(p).strip() for p in patterns if str(p).strip()]
    except Exception as e:
        logger.warning(f"[Backup] 读取排除规则配置失败，使用默认值: {e}")

    return list(DEFAULT_EXCLUDE_PATTERNS)


def validate_source_path(source_path: str) -> Tuple[bool, str]:
    """
    校验备份源路径安全性
    :return: (是否安全, 规范化后的绝对路径或错误信息)
    """
    if not source_path:
        return False, "源路径不能为空"

    abs_path = os.path.normcase(os.path.abspath(source_path))

    # 检查路径是否存在
    if not os.path.exists(abs_path):
        return False, f"路径不存在: {source_path}"

    # 检查是否在允许的根目录内
    in_allowed = False
    for root in ALLOWED_ROOT_DIRS:
        root_norm = os.path.normcase(root)
        if not root_norm.endswith(os.sep):
            root_norm += os.sep
        if abs_path == root_norm.rstrip(os.sep) or abs_path.startswith(root_norm):
            in_allowed = True
            break

    if not in_allowed:
        return False, f"路径不在允许的根目录范围内: {source_path}"

    # 检查危险路径（使用用户配置的排除规则，按路径分段精确匹配目录名/文件名，避免子串误伤如 login 被 log 命中）
    exclude_patterns = get_exclude_patterns()
    parts = [p for p in abs_path.replace('\\', '/').split('/') if p]
    for pattern in exclude_patterns:
        pat_lower = pattern.lower()
        for part in parts:
            if part.lower() == pat_lower:
                return False, f"命中排除规则 {pattern}，已禁止备份: {source_path}"

    return True, abs_path


def validate_target_path(target_path: str) -> Tuple[bool, str]:
    """
    校验备份目标目录安全性，如果不存在则自动创建
    :return: (是否有效, 规范化后的绝对路径或错误信息)
    """
    if not target_path:
        return False, "目标路径不能为空"

    abs_path = os.path.normcase(os.path.abspath(target_path))

    # 不允许备份到源路径自身或其子目录（防止递归备份）
    # 这个检查在调用处做，因为源和目标是动态的

    try:
        os.makedirs(abs_path, exist_ok=True)
    except Exception as e:
        return False, f"无法创建备份目录 {target_path}: {e}"

    # 测试写权限
    test_file = os.path.join(abs_path, '.backup_write_test')
    try:
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
    except Exception as e:
        return False, f"备份目录无写权限: {target_path}: {e}"

    return True, abs_path


def _collect_files(source_path: str, exclude_patterns=None):
    """
    收集源路径下所有文件（排除用户配置的目录/文件）
    :param exclude_patterns: 排除规则列表，None 则自动读取
    :return: (文件相对路径列表, 总大小bytes)
    """
    if exclude_patterns is None:
        exclude_patterns = get_exclude_patterns()

    files = []
    total_size = 0

    if os.path.isfile(source_path):
        # 单文件
        files.append(os.path.basename(source_path))
        total_size = os.path.getsize(source_path)
    else:
        # 目录递归
        for root, dirs, filenames in os.walk(source_path):
            # 过滤排除目录
            dirs[:] = [d for d in dirs if d not in exclude_patterns]
            for fname in filenames:
                if fname in exclude_patterns:
                    continue
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, source_path)
                try:
                    fsize = os.path.getsize(full_path)
                    total_size += fsize
                except Exception:
                    fsize = 0
                files.append(rel_path)

    return files, total_size


def create_backup(source_path: str, source_type: str, source_name: str,
                  target_id: int, record_name: str,
                  created_by: str = None) -> dict:
    """
    创建备份
    :param source_path: 备份源路径
    :param source_type: project / folder / file
    :param source_name: 源名称（用于备份文件名）
    :param target_id: 备份目标目录ID
    :param record_name: 备份名称
    :param created_by: 创建人
    :return: dict 备份结果
    """
    # 1. 校验源路径
    ok, result = validate_source_path(source_path)
    if not ok:
        return {"success": False, "msg": result}

    abs_source = result

    # 2. 获取备份目标目录
    target = BackupTarget.query.get(target_id)
    if not target:
        return {"success": False, "msg": "备份目标目录不存在"}

    # 3. 校验目标路径
    ok, result = validate_target_path(target.target_path)
    if not ok:
        return {"success": False, "msg": result}

    abs_target = result

    # 4. 防止备份到源路径自身或其子目录
    if abs_source == abs_target or abs_target.startswith(abs_source + os.sep):
        return {"success": False, "msg": "备份目标目录不能与源路径相同或在其子目录内"}

    # 5. 收集文件并检查大小
    files, total_size = _collect_files(abs_source)
    if not files:
        return {"success": False, "msg": "源路径下没有可备份的文件"}

    if total_size > MAX_BACKUP_TOTAL_SIZE:
        size_mb = total_size / (1024 * 1024)
        return {
            "success": False,
            "msg": f"源路径总大小 {size_mb:.1f}MB 超过限制 {MAX_BACKUP_TOTAL_SIZE / (1024 * 1024):.0f}MB"
        }

    # 6. 生成备份文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in ('_', '-', '.') else '_' for c in source_name)
    if not record_name:
        record_name = f"{safe_name}_{timestamp}"
    zip_filename = f"{safe_name}_{timestamp}.zip"
    zip_path = os.path.join(abs_target, zip_filename)

    # 7. 打包压缩
    skipped_files = []
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            # 添加元数据文件
            meta = {
                "record_name": record_name,
                "source_type": source_type,
                "source_path": abs_source,
                "source_name": source_name,
                "created_at": timestamp,
                "created_by": created_by,
                "file_count": len(files),
                "total_size": total_size,
            }
            zf.writestr("__backup_meta__.json", json.dumps(meta, ensure_ascii=False, indent=2))

            if os.path.isfile(abs_source):
                # 单文件备份
                try:
                    zf.write(abs_source, os.path.basename(abs_source))
                except Exception as e:
                    return {"success": False, "msg": f"创建备份失败，无法读取文件 {abs_source}: {e}"}
            else:
                # 目录备份：单个文件读不了则跳过，避免整个备份因一个被占用的文件而失败
                for rel_path in files:
                    full_path = os.path.join(abs_source, rel_path)
                    try:
                        zf.write(full_path, rel_path)
                    except Exception as e:
                        logger.warning(f"[Backup] 跳过无法读取的文件 {full_path}: {e}")
                        skipped_files.append(full_path)
                if files and len(skipped_files) == len(files):
                    return {"success": False, "msg": f"创建备份失败：所有文件均无法读取（可能被其他进程占用或无读取权限），例如 {skipped_files[0]}"}
    except Exception as e:
        # 清理不完整的zip
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        logger.error(f"[Backup] 创建备份失败: {e}")
        return {"success": False, "msg": f"创建备份失败: {e}"}

    # 8. 获取最终文件大小
    backup_size = os.path.getsize(zip_path)

    # 9. 写入数据库记录
    try:
        record = BackupRecord(
            record_name=record_name,
            source_type=source_type,
            source_path=abs_source,
            source_name=source_name,
            target_id=target_id,
            target_path=abs_target,
            backup_file_name=zip_filename,
            backup_file_path=zip_path,
            backup_size=backup_size,
            file_count=len(files) - len(skipped_files),
            status="success",
            created_by=created_by,
        )
        db.session.add(record)
        db.session.commit()
        logger.info(f"[Backup] 备份成功: {record_name} -> {zip_path}")
        skip_msg = f"（已跳过 {len(skipped_files)} 个无法读取的文件）" if skipped_files else ""
        return {
            "success": True,
            "msg": "备份成功" + skip_msg,
            "data": {
                "id": record.id,
                "record_name": record_name,
                "backup_file": zip_filename,
                "backup_size": backup_size,
                "file_count": len(files) - len(skipped_files),
                "skipped_count": len(skipped_files),
                "skipped_files": [os.path.basename(f) for f in skipped_files],
            }
        }
    except Exception as e:
        db.session.rollback()
        logger.error(f"[Backup] 写入数据库记录失败: {e}")
        return {"success": False, "msg": f"写入备份记录失败: {e}"}


def restore_backup(record_id: int) -> dict:
    """
    恢复备份
    恢复前会自动创建一个"恢复前快照"，防止恢复出错
    :param record_id: 备份记录ID
    :return: dict 恢复结果
    """
    record = BackupRecord.query.get(record_id)
    if not record:
        return {"success": False, "msg": "备份记录不存在"}

    if record.status != "success":
        return {"success": False, "msg": "该备份状态异常，无法恢复"}

    zip_path = record.backup_file_path
    source_path = record.source_path

    # 检查备份文件是否存在
    if not os.path.exists(zip_path):
        return {"success": False, "msg": f"备份文件不存在: {zip_path}"}

    # 检查源路径是否仍然存在
    is_file_backup = record.source_type == 'file'
    if not os.path.exists(source_path):
        if is_file_backup:
            # 单文件备份：源文件可能被删除，不创建目录（恢复时直接写入文件）
            # 确保父目录存在
            parent_dir = os.path.dirname(source_path)
            if not os.path.isdir(parent_dir):
                try:
                    os.makedirs(parent_dir, exist_ok=True)
                except Exception as e:
                    return {"success": False, "msg": f"无法创建父目录 {parent_dir}: {e}"}
        else:
            # 目录备份：源目录不存在则创建
            try:
                os.makedirs(source_path, exist_ok=True)
            except Exception as e:
                return {"success": False, "msg": f"源路径不存在且无法创建: {source_path}: {e}"}

    # 恢复前自动创建快照（仅对目录做，单文件不做快照）
    # 若要恢复的记录本身已是快照(pre_restore_snapshot)，不再嵌套建快照——它本身就是安全网
    snapshot_created = False
    if os.path.isdir(source_path) and record.source_type != 'pre_restore_snapshot':
        try:
            snapshot_time = datetime.now().strftime("%Y%m%d_%H%M%S")
            source_name = record.source_name
            safe_name = "".join(c if c.isalnum() or c in ('_', '-', '.') else '_' for c in source_name)
            snapshot_name = f"恢复前快照_{safe_name}_{snapshot_time}"
            snapshot_zip = os.path.join(record.target_path, f"{safe_name}_pre_restore_{snapshot_time}.zip")

            with zipfile.ZipFile(snapshot_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                meta = {
                    "record_name": snapshot_name,
                    "source_type": "pre_restore_snapshot",
                    "source_path": source_path,
                    "source_name": source_name,
                    "created_at": snapshot_time,
                    "note": "恢复前自动快照",
                }
                zf.writestr("__backup_meta__.json", json.dumps(meta, ensure_ascii=False, indent=2))

                # 快照只备份与备份同范围的内容（沿用默认排除规则）。
                # 因为恢复已改为合并模式，不会触碰 .git/node_modules 等排除目录，
                # 这些目录无需进快照、回退时也不会丢失；完整打包反而体积过大。
                files, _ = _collect_files(source_path)
                for rel_path in files:
                    full_path = os.path.join(source_path, rel_path)
                    zf.write(full_path, rel_path)

            snapshot_size = os.path.getsize(snapshot_zip)

            # 写入快照记录
            snapshot_record = BackupRecord(
                record_name=snapshot_name,
                source_type="pre_restore_snapshot",
                source_path=source_path,
                source_name=source_name,
                target_id=record.target_id,
                target_path=record.target_path,
                backup_file_name=os.path.basename(snapshot_zip),
                backup_file_path=snapshot_zip,
                backup_size=snapshot_size,
                file_count=len(files),
                status="success",
                created_by=record.created_by,
            )
            db.session.add(snapshot_record)
            db.session.commit()  # 先提交快照记录，防止后续恢复失败时回滚导致孤立文件
            snapshot_created = True
            logger.info(f"[Backup] 恢复前快照已创建: {snapshot_zip}")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"[Backup] 创建恢复前快照失败（继续恢复）: {e}")

    # 执行恢复
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # 获取压缩包内的文件列表（排除元数据文件）
            names = [n for n in zf.namelist() if n != "__backup_meta__.json"]

            if is_file_backup:
                # 单文件恢复：从 zip 中找到文件内容写入 source_path
                for name in names:
                    # 确保源路径的父目录存在
                    parent_dir = os.path.dirname(source_path)
                    if parent_dir and not os.path.isdir(parent_dir):
                        os.makedirs(parent_dir, exist_ok=True)
                    with zf.open(name) as src, open(source_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
            else:
                # 目录恢复（合并模式）：只覆盖备份内有的文件，保留目录中已有的其他内容
                # 这样 .git / node_modules / __pycache__ 等排除项不会被误删
                # 注意：此模式不会删除备份中不存在的旧文件（可能留下残留文件，属预期行为）
                zf.extractall(source_path)

                # 删除元数据文件
                meta_file = os.path.join(source_path, "__backup_meta__.json")
                if os.path.exists(meta_file):
                    os.remove(meta_file)

        # 更新恢复记录
        record.restored_at = datetime.now()
        record.restore_count = (record.restore_count or 0) + 1
        db.session.commit()

        logger.info(f"[Backup] 恢复成功: {record.record_name} -> {source_path}")
        msg = "恢复成功"
        if snapshot_created:
            msg += "（已自动创建恢复前快照）"
        return {"success": True, "msg": msg}

    except Exception as e:
        db.session.rollback()
        logger.error(f"[Backup] 恢复失败: {e}")
        return {"success": False, "msg": f"恢复失败: {e}"}


def delete_backup(record_id: int) -> dict:
    """
    删除备份记录（同时删除备份文件）
    """
    record = BackupRecord.query.get(record_id)
    if not record:
        return {"success": False, "msg": "备份记录不存在"}

    # 删除备份文件
    zip_path = record.backup_file_path
    if os.path.exists(zip_path):
        try:
            os.remove(zip_path)
        except Exception as e:
            logger.warning(f"[Backup] 删除备份文件失败: {zip_path}: {e}")

    db.session.delete(record)
    db.session.commit()
    logger.info(f"[Backup] 备份记录已删除: {record.record_name}")
    return {"success": True, "msg": "删除成功"}


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if not size_bytes:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB']
    idx = 0
    size = float(size_bytes)
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024
        idx += 1
    return f"{size:.1f} {units[idx]}"
