/**
 * 备份管理模块 JS
 * 功能: 备份目标目录CRUD + 备份记录查询/恢复/删除 + 创建备份
 */

// ============================================================
// 全局状态
// ============================================================
var backupTargets = [];
var currentRecordPage = 1;
var currentRestoreId = null;
// 存储当前备份对话框的源信息（供 doBackup 使用）
var currentBackupSourcePath = '';
var currentBackupSourceType = 'folder';
var currentBackupSourceName = '';

// ============================================================
// 页面初始化
// ============================================================
$(document).ready(function() {
    // 备份目录列表（两种页面都需要：备份管理页用于展示，文件编辑器用于弹框选择）
    loadTargets();
    // 备份记录列表仅在备份管理页加载
    if ($('#recordsBody').length) {
        loadRecords();
        loadExcludePatterns();
    }
    bindEvents();
});

function bindEvents() {
    // 备份目标目录
    $('#addTargetBtn').click(function() { showTargetModal(); });
    $('#saveTargetBtn').click(function() { saveTarget(); });
    $('#browseTargetPathBtn').click(function() { bkShowFolderBrowser(); });

    // 排除规则
    $('#addExcludePatternBtn').click(function() { addExcludePattern(); });
    $('#newExcludePattern').on('keypress', function(e) {
        if (e.which === 13) { addExcludePattern(); }
    });

    // 搜索
    $('#searchRecordsBtn').click(function() { currentRecordPage = 1; loadRecords(); });
    $('#recordKeyword').on('keypress', function(e) {
        if (e.which === 13) { currentRecordPage = 1; loadRecords(); }
    });

    // 创建备份
    $('#confirmBackupBtn').click(function() { doBackup(); });

    // 恢复
    $('#confirmRestoreBtn').click(function() { doRestore(); });
}

// ============================================================
// 备份目标目录管理
// ============================================================
function loadTargets() {
    $.ajax({
        url: '/backup/api/targets',
        method: 'GET',
        success: function(resp) {
            backupTargets = resp.data || [];
            // 备份管理页才有目录列表容器
            if ($('#targetsBody').length) {
                renderTargets();
            }
            // 备份管理页才有记录筛选下拉
            if ($('#recordTargetId').length) {
                var $sel = $('#recordTargetId');
                $sel.find('option:not(:first)').remove();
                backupTargets.forEach(function(t) {
                    $sel.append('<option value="' + t.id + '">' + escapeHtml(t.target_name) + '</option>');
                });
            }
            // 两种页面都有创建备份弹框的目录选择
            if ($('#backupTargetSelect').length) {
                var $sel2 = $('#backupTargetSelect');
                $sel2.empty();
                backupTargets.forEach(function(t) {
                    var label = t.target_name + (t.is_default ? ' (默认)' : '');
                    $sel2.append('<option value="' + t.id + '"' + (t.is_default ? ' selected' : '') + '>' + escapeHtml(label) + '</option>');
                });
            }
        },
        error: function() {
            // 文件编辑器页面可能没有权限或接口未加载完成，静默处理
            if ($('#targetsBody').length) {
                showToast('加载备份目录失败', 'danger');
            }
        }
    });
}

function renderTargets() {
    var $body = $('#targetsBody');
    if (backupTargets.length === 0) {
        $body.html(
            '<div class="empty-state">' +
            '<i class="bi bi-hdd" style="font-size: 24px;"></i>' +
            '<p class="mt-2">暂无备份目录，请点击"新增目录"添加</p>' +
            '</div>'
        );
        return;
    }

    var html = '';
    backupTargets.forEach(function(t) {
        html += '<div class="target-row">' +
            '<div style="flex:1;">' +
                '<span class="fw-bold">' + escapeHtml(t.target_name) + '</span>' +
                (t.is_default ? ' <span class="badge badge-default">默认</span>' : '') +
                '<br><small class="text-muted">' + escapeHtml(t.target_path) + '</small>' +
                (t.description ? '<br><small class="text-muted">' + escapeHtml(t.description) + '</small>' : '') +
            '</div>' +
            '<button class="btn btn-outline-secondary btn-sm" onclick="showTargetModal(' + t.id + ')">' +
                '<i class="bi bi-pencil"></i>' +
            '</button>' +
            '<button class="btn btn-outline-danger btn-sm" onclick="deleteTarget(' + t.id + ')">' +
                '<i class="bi bi-trash"></i>' +
            '</button>' +
        '</div>';
    });
    $body.html(html);
}

function showTargetModal(id) {
    $('#targetId').val('');
    $('#targetName').val('');
    $('#targetPath').val('');
    $('#targetDesc').val('');
    $('#targetDefault').prop('checked', false);

    if (id) {
        // 编辑模式
        var t = backupTargets.find(function(x) { return x.id === id; });
        if (!t) return;
        $('#targetId').val(t.id);
        $('#targetName').val(t.target_name);
        $('#targetPath').val(t.target_path);
        $('#targetDesc').val(t.description || '');
        $('#targetDefault').prop('checked', !!t.is_default);
        $('#targetModalTitle').html('<i class="bi bi-hdd me-2"></i>编辑备份目录');
    } else {
        $('#targetModalTitle').html('<i class="bi bi-hdd me-2"></i>新增备份目录');
    }

    var el = document.getElementById('targetModal');
    bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el);
    bootstrap.Modal.getInstance(el).show();
}

function saveTarget() {
    var id = $('#targetId').val();
    var data = {
        target_name: $('#targetName').val().trim(),
        target_path: $('#targetPath').val().trim(),
        description: $('#targetDesc').val().trim(),
        is_default: $('#targetDefault').is(':checked')
    };

    if (!data.target_path) { showToast('请先点击浏览按钮选择备份路径', 'warning'); return; }
    if (!data.target_name) { showToast('目录别名生成失败，请重新选择路径', 'warning'); return; }

    var method = id ? 'PUT' : 'POST';
    var url = id ? '/backup/api/target/' + id : '/backup/api/target';

    $.ajax({
        url: url,
        method: method,
        contentType: 'application/json',
        data: JSON.stringify(data),
        success: function(resp) {
            bootstrap.Modal.getInstance(document.getElementById('targetModal')).hide();
            showToast(resp.msg, 'success');
            loadTargets();
        },
        error: function(xhr) {
            showToast((xhr.responseJSON || {}).msg || '操作失败', 'danger');
        }
    });
}

function deleteTarget(id) {
    if (!confirm('确定删除此备份目录配置吗？（仅删除配置，不影响已备份的文件）')) return;
    $.ajax({
        url: '/backup/api/target/' + id,
        method: 'DELETE',
        success: function(resp) {
            showToast(resp.msg, 'success');
            loadTargets();
        },
        error: function(xhr) {
            showToast((xhr.responseJSON || {}).msg || '删除失败', 'danger');
        }
    });
}

// ============================================================
// 备份记录管理
// ============================================================
function loadRecords() {
    var params = {
        page: currentRecordPage,
        per_page: 15,
        keyword: $('#recordKeyword').val(),
        source_type: $('#recordSourceType').val(),
        target_id: $('#recordTargetId').val()
    };

    $.ajax({
        url: '/backup/api/records',
        method: 'GET',
        data: params,
        success: function(resp) {
            renderRecords(resp.data);
        },
        error: function() {
            showToast('加载备份记录失败', 'danger');
        }
    });
}

function renderRecords(data) {
    var $body = $('#recordsBody');
    var records = data.records || [];

    if (records.length === 0) {
        $body.html(
            '<div class="empty-state">' +
            '<i class="bi bi-archive" style="font-size: 24px;"></i>' +
            '<p class="mt-2">暂无备份记录</p>' +
            '</div>'
        );
        $('#recordPageInfo').text('');
        $('#recordPageControls').html('');
        return;
    }

    var html = '';
    records.forEach(function(r) {
        var typeBadge = '<span class="source-type-badge source-type-' + r.source_type + '">' +
            getTypeLabel(r.source_type) + '</span>';

        html += '<div class="record-row">' +
            '<div style="flex:1; min-width:0;">' +
                '<div class="d-flex align-items-center gap-2">' +
                    '<i class="bi bi-file-earmark-zip text-primary"></i>' +
                    '<span class="fw-bold text-truncate">' + escapeHtml(r.record_name) + '</span>' +
                    typeBadge +
                '</div>' +
                '<div class="text-muted small text-truncate">' +
                    '<i class="bi bi-folder2 me-1"></i>' + escapeHtml(r.source_path) +
                '</div>' +
                '<div class="text-muted small">' +
                    r.backup_size_display + ' · ' + r.file_count + '个文件' +
                    ' · ' + (r.created_at || '') +
                    (r.created_by ? ' · ' + escapeHtml(r.created_by) : '') +
                    (r.restored_at ? ' · <span class="text-warning">已恢复' + r.restore_count + '次</span>' : '') +
                '</div>' +
            '</div>' +
            '<button class="btn btn-outline-info btn-sm" title="详情" onclick="showRecordDetail(' + r.id + ')">' +
                '<i class="bi bi-eye"></i>' +
            '</button>' +
            '<button class="btn btn-outline-warning btn-sm" title="恢复" onclick="showRestoreConfirm(' + r.id + ')">' +
                '<i class="bi bi-arrow-counterclockwise"></i>' +
            '</button>' +
            '<button class="btn btn-outline-danger btn-sm" title="删除" onclick="deleteRecord(' + r.id + ')">' +
                '<i class="bi bi-trash"></i>' +
            '</button>' +
        '</div>';
    });
    $body.html(html);

    // 分页
    var info = '共 ' + data.total + ' 条，第 ' + data.current_page + '/' + data.pages + ' 页';
    $('#recordPageInfo').text(info);

    var controls = '';
    if (data.pages > 1) {
        controls += '<nav><ul class="pagination pagination-sm mb-0">';
        if (data.current_page > 1) {
            controls += '<li class="page-item"><a class="page-link" href="javascript:gotoPage(' + (data.current_page - 1) + ')">&laquo;</a></li>';
        }
        var start = Math.max(1, data.current_page - 2);
        var end = Math.min(data.pages, data.current_page + 2);
        for (var i = start; i <= end; i++) {
            controls += '<li class="page-item ' + (i === data.current_page ? 'active' : '') + '"><a class="page-link" href="javascript:gotoPage(' + i + ')">' + i + '</a></li>';
        }
        if (data.current_page < data.pages) {
            controls += '<li class="page-item"><a class="page-link" href="javascript:gotoPage(' + (data.current_page + 1) + ')">&raquo;</a></li>';
        }
        controls += '</ul></nav>';
    }
    $('#recordPageControls').html(controls);
}

function gotoPage(page) {
    currentRecordPage = page;
    loadRecords();
}

function getTypeLabel(type) {
    var labels = {
        'project': '项目',
        'folder': '文件夹',
        'file': '文件',
        'pre_restore_snapshot': '恢复前快照'
    };
    return labels[type] || type;
}

function showRecordDetail(id) {
    $.ajax({
        url: '/backup/api/record/' + id,
        method: 'GET',
        success: function(resp) {
            var r = resp.data;
            var fileExists = r.file_exists ?
                '<span class="file-exists-yes"><i class="bi bi-check-circle"></i> 存在</span>' :
                '<span class="file-exists-no"><i class="bi bi-x-circle"></i> 缺失</span>';

            var html = '<table class="table table-sm mb-0" style="table-layout:fixed; width:100%;">' +
                '<colgroup><col style="width:110px; min-width:110px;"><col></colgroup>' +
                '<tr><td class="text-muted text-nowrap">备份名称</td><td>' + escapeHtml(r.record_name) + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">来源类型</td><td>' + getTypeLabel(r.source_type) + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">源路径</td><td><code class="d-block" style="word-break:break-all;">' + escapeHtml(r.source_path) + '</code></td></tr>' +
                '<tr><td class="text-muted text-nowrap">源名称</td><td>' + escapeHtml(r.source_name) + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">备份目录</td><td><code class="d-block" style="word-break:break-all;">' + escapeHtml(r.target_path) + '</code></td></tr>' +
                '<tr><td class="text-muted text-nowrap">备份文件</td><td>' + escapeHtml(r.backup_file_name) + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">文件大小</td><td>' + r.backup_size_display + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">文件数量</td><td>' + r.file_count + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">备份文件状态</td><td>' + fileExists + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">创建人</td><td>' + escapeHtml(r.created_by || '-') + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">创建时间</td><td>' + (r.created_at || '-') + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">恢复次数</td><td>' + r.restore_count + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">最近恢复</td><td>' + (r.restored_at || '-') + '</td></tr>' +
                '</table>';

            $('#recordDetailBody').html(html);
            var el = document.getElementById('recordDetailModal');
            bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el);
            bootstrap.Modal.getInstance(el).show();
        },
        error: function() {
            showToast('加载详情失败', 'danger');
        }
    });
}

function showRestoreConfirm(id) {
    $.ajax({
        url: '/backup/api/record/' + id,
        method: 'GET',
        success: function(resp) {
            var r = resp.data;
            currentRestoreId = id;
            var html =
                '<div class="alert alert-warning">' +
                    '<i class="bi bi-exclamation-triangle me-1"></i>' +
                    '<strong>恢复操作将覆盖目标目录下的所有文件！</strong>' +
                '</div>' +
                '<table class="table table-sm mb-0" style="table-layout:fixed; width:100%;">' +
                '<colgroup><col style="width:90px; min-width:90px;"><col></colgroup>' +
                '<tr><td class="text-muted text-nowrap">备份名称</td><td>' + escapeHtml(r.record_name) + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">恢复到</td><td><code class="d-block" style="word-break:break-all;">' + escapeHtml(r.source_path) + '</code></td></tr>' +
                '<tr><td class="text-muted text-nowrap">备份时间</td><td>' + (r.created_at || '-') + '</td></tr>' +
                '<tr><td class="text-muted text-nowrap">文件数量</td><td>' + r.file_count + ' 个文件</td></tr>' +
                '<tr><td class="text-muted text-nowrap">备份大小</td><td>' + r.backup_size_display + '</td></tr>' +
                '</table>' +
                '<div class="alert alert-info">' +
                    '<i class="bi bi-info-circle me-1"></i>恢复前系统会自动创建一个快照备份，可随时回退。' +
                '</div>';
            $('#restoreConfirmBody').html(html);
            var el = document.getElementById('restoreConfirmModal');
            bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el);
            bootstrap.Modal.getInstance(el).show();
        },
        error: function() {
            showToast('加载详情失败', 'danger');
        }
    });
}

function doRestore() {
    if (!currentRestoreId) return;
    var $btn = $('#confirmRestoreBtn');
    $btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span> 恢复中...');

    $.ajax({
        url: '/backup/api/record/' + currentRestoreId + '/restore',
        method: 'POST',
        success: function(resp) {
            bootstrap.Modal.getInstance(document.getElementById('restoreConfirmModal')).hide();
            showToast(resp.msg, 'success');
            loadRecords();
        },
        error: function(xhr) {
            showToast((xhr.responseJSON || {}).msg || '恢复失败', 'danger');
        },
        complete: function() {
            $btn.prop('disabled', false).html('<i class="bi bi-arrow-counterclockwise me-1"></i>确认恢复');
        }
    });
}

function deleteRecord(id) {
    if (!confirm('确定删除此备份记录吗？（备份文件也将被删除）')) return;
    $.ajax({
        url: '/backup/api/record/' + id,
        method: 'DELETE',
        success: function(resp) {
            showToast(resp.msg, 'success');
            loadRecords();
        },
        error: function(xhr) {
            showToast((xhr.responseJSON || {}).msg || '删除失败', 'danger');
        }
    });
}

// ============================================================
// 创建备份（从文件编辑器调用）
// ============================================================

/**
 * 打开创建备份弹框
 * @param sourcePath 备份源路径
 * @param sourceType project / folder / file
 * @param sourceName 源名称
 */
function openBackupDialog(sourcePath, sourceType, sourceName) {
    // 保存源信息供 doBackup 使用
    currentBackupSourcePath = sourcePath;
    currentBackupSourceType = sourceType;
    currentBackupSourceName = sourceName;

    // 先校验源路径并获取预估信息
    $.ajax({
        url: '/backup/api/validate-source',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ source_path: sourcePath }),
        success: function(resp) {
            var d = resp.data;
            $('#backupSourcePath').val(d.source_path);
            $('#backupSourceType').val(getTypeLabel(d.source_type));
            $('#backupFileCount').val(d.file_count + ' 个');
            $('#backupTotalSize').val(d.total_size_display);
            $('#backupRecordName').val('');

            // 填充备份目录选择
            var $sel = $('#backupTargetSelect');
            $sel.empty();
            if (backupTargets.length === 0) {
                $sel.append('<option value="">请先在备份管理中添加目录</option>');
            } else {
                backupTargets.forEach(function(t) {
                    var label = t.target_name + (t.is_default ? ' (默认)' : '');
                    $sel.append('<option value="' + t.id + '"' + (t.is_default ? ' selected' : '') + '>' +
                        escapeHtml(label) + '</option>');
                });
            }

            var el = document.getElementById('backupModal');
            bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el);
            bootstrap.Modal.getInstance(el).show();
        },
        error: function(xhr) {
            showToast((xhr.responseJSON || {}).msg || '路径校验失败', 'danger');
        }
    });
}

function doBackup() {
    var targetId = $('#backupTargetSelect').val();
    if (!targetId) {
        showToast('请先在备份管理中添加备份目录', 'warning');
        return;
    }

    var sourcePath = $('#backupSourcePath').val();
    var data = {
        source_path: sourcePath,
        source_type: currentBackupSourceType,
        source_name: currentBackupSourceName,
        target_id: parseInt(targetId),
        record_name: $('#backupRecordName').val().trim()
    };

    var $btn = $('#confirmBackupBtn');
    $btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm"></span> 备份中...');

    $.ajax({
        url: '/backup/api/create',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(data),
        success: function(resp) {
            bootstrap.Modal.getInstance(document.getElementById('backupModal')).hide();
            showToast(resp.msg + ' (' + (resp.data ? resp.data.file_count + '个文件' : '') + ')', 'success');
        },
        error: function(xhr) {
            showToast((xhr.responseJSON || {}).msg || '备份失败', 'danger');
        },
        complete: function() {
            $btn.prop('disabled', false).html('<i class="bi bi-archive me-1"></i>开始备份');
        }
    });
}

// ============================================================
// 文件夹浏览器（选择备份目录路径）
// ============================================================

var bkFolderBrowserCallback = null;

function bkShowFolderBrowser(initialPath) {
    var el = document.getElementById('folderBrowserModal');
    var modal = bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el);
    modal.show();
    bkLoadFolders(initialPath || '');
}

function bkLoadFolders(path) {
    var $folderList = $('#bkFolderList');
    var $quickPaths = $('#bkQuickPaths');
    var $currentPath = $('#bkCurrentPath');

    $folderList.html('<div class="text-center text-muted py-4"><div class="spinner-border"></div><div class="mt-2">加载中...</div></div>');

    $.ajax({
        url: '/file-editor/api/browse/folders',
        method: 'GET',
        data: { path: path || '' },
        success: function(res) {
            if (res.code === 200) {
                if (res.data.type === 'quick_paths') {
                    var html = '';
                    $.each(res.data.paths, function(key, value) {
                        var labels = {
                            'current_dir': '📁 当前目录',
                            'home_dir': '🏠 用户目录',
                            'desktop': '🖥️ 桌面',
                            'documents': '📄 文档',
                            'downloads': '⬇️ 下载',
                            'c_drive': '💾 C盘',
                            'd_drive': '💾 D盘',
                            'e_drive': '💾 E盘'
                        };
                        html += '<button class="btn btn-sm btn-outline-primary bk-folder-quick" data-path="' + escapeHtml(value) + '">' + (labels[key] || key) + '</button>';
                    });
                    $quickPaths.html(html);
                    $currentPath.val('请选择目录');
                    $folderList.html('<div class="text-center text-muted py-4">请点击上方快捷路径开始浏览</div>');
                } else {
                    $currentPath.val(res.data.current_path);
                    $('#bkGoUpBtn').prop('disabled', !res.data.parent_path);

                    var html = '';
                    if (res.data.parent_path) {
                        html += '<button class="list-group-item list-group-item-action bk-folder-nav" data-path="' + escapeHtml(res.data.parent_path) + '">';
                        html += '<i class="bi bi-arrow-up-circle text-primary me-2"></i><strong>.. 上级文件夹</strong>';
                        html += '</button>';
                    }
                    if (res.data.folders.length === 0) {
                        html += '<div class="list-group-item text-muted text-center">此文件夹为空</div>';
                    } else {
                        res.data.folders.forEach(function(folder) {
                            html += '<button class="list-group-item list-group-item-action bk-folder-nav" data-path="' + escapeHtml(folder.path) + '">';
                            html += '<i class="bi bi-folder text-warning me-2"></i>' + escapeHtml(folder.name);
                            html += '</button>';
                        });
                    }
                    $folderList.html(html);
                }
            } else {
                $folderList.html('<div class="list-group-item text-danger">' + escapeHtml(res.msg) + '</div>');
            }
        },
        error: function(xhr) {
            var msg = (xhr.responseJSON || {}).msg || '加载失败';
            $folderList.html('<div class="list-group-item text-danger">' + escapeHtml(msg) + '</div>');
        }
    });
}

function bkGoUpFolder() {
    var currentPath = $('#bkCurrentPath').val();
    if (currentPath && currentPath !== '请选择目录') {
        var lastSlash = currentPath.lastIndexOf('\\');
        if (lastSlash === -1) lastSlash = currentPath.lastIndexOf('/');
        var parentPath = currentPath.substring(0, lastSlash);
        if (parentPath) {
            bkLoadFolders(parentPath);
        }
    }
}

function bkConfirmFolderSelection() {
    var selectedPath = $('#bkCurrentPath').val();
    if (!selectedPath || selectedPath === '请选择目录') {
        showToast('请先选择一个目录', 'warning');
        return;
    }

    // 自动生成目录别名: 文件夹名_盘符路径
    var alias = generateTargetAlias(selectedPath);
    $('#targetPath').val(selectedPath);
    $('#targetName').val(alias);

    bootstrap.Modal.getInstance(document.getElementById('folderBrowserModal')).hide();
    showToast('已选择: ' + selectedPath, 'success');
}

/**
 * 根据所选路径自动生成目录别名
 * 例如: D:\backups → backups_D
 *      E:\project\snapshots → snapshots_E_project
 */
function generateTargetAlias(path) {
    // 标准化路径分隔符
    var parts = path.replace(/\//g, '\\').split('\\').filter(function(p) { return p.length > 0; });
    if (parts.length === 0) return 'backup_dir';

    // 最后一级文件夹名
    var folderName = parts[parts.length - 1];

    // 拼接上级路径信息（最多取2级，避免太长）
    var pathParts = parts.slice(0, -1).slice(-2); // 取倒数2级父目录
    if (pathParts.length > 0) {
        return folderName + '_' + pathParts.join('_');
    }
    // 如果没有上级目录（如选的是盘根 D:\），用盘符
    var driveMatch = path.match(/^([A-Z]):/i);
    if (driveMatch) {
        return folderName + '_' + driveMatch[1].toUpperCase();
    }
    return folderName;
}

// 文件夹浏览器导航事件委托
$(document).on('click', '.bk-folder-nav', function() {
    bkLoadFolders($(this).data('path'));
});

$(document).on('click', '.bk-folder-quick', function() {
    bkLoadFolders($(this).data('path'));
});

// ============================================================
// 备份排除规则管理
// ============================================================

var excludePatterns = [];
var defaultExcludePatterns = [];

function loadExcludePatterns() {
    $.ajax({
        url: '/backup/api/exclude-patterns',
        method: 'GET',
        success: function(resp) {
            excludePatterns = (resp.data && resp.data.patterns) || [];
            defaultExcludePatterns = (resp.data && resp.data.defaults) || [];
            renderExcludePatterns();
        },
        error: function() {
            $('#excludePatternsList').html('<span class="text-muted">加载失败</span>');
        }
    });
}

function renderExcludePatterns() {
    var $list = $('#excludePatternsList');
    if (excludePatterns.length === 0) {
        $list.html('<span class="text-muted">无排除规则（备份所有文件）</span>');
        return;
    }
    var html = '';
    excludePatterns.forEach(function(p, i) {
        var isDefault = defaultExcludePatterns.indexOf(p) >= 0;
        html += '<span class="badge bg-' + (isDefault ? 'secondary' : 'primary') + ' d-inline-flex align-items-center gap-1" style="font-size:13px; padding:5px 10px;">';
        html += '<i class="bi bi-slash-circle"></i> ';
        html += escapeHtml(p);
        html += ' <i class="bi bi-x-lg ms-1" style="cursor:pointer;" onclick="removeExcludePattern(' + i + ')" title="移除"></i>';
        html += '</span>';
    });
    $list.html(html);
}

function addExcludePattern() {
    var $input = $('#newExcludePattern');
    var val = $input.val().trim();
    if (!val) {
        showToast('请输入文件夹名或文件名', 'warning');
        return;
    }
    if (excludePatterns.indexOf(val) >= 0) {
        showToast('该规则已存在', 'warning');
        return;
    }
    excludePatterns.push(val);
    $input.val('');
    saveExcludePatterns();
}

function removeExcludePattern(index) {
    excludePatterns.splice(index, 1);
    saveExcludePatterns();
}

function resetExcludePatterns() {
    if (!confirm('确定恢复为默认排除规则？当前自定义规则将被覆盖。')) return;
    excludePatterns = defaultExcludePatterns.slice();
    saveExcludePatterns();
}

function saveExcludePatterns() {
    $.ajax({
        url: '/backup/api/exclude-patterns',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ patterns: excludePatterns }),
        success: function(resp) {
            excludePatterns = (resp.data && resp.data.patterns) || excludePatterns;
            renderExcludePatterns();
            showToast('排除规则已保存', 'success');
        },
        error: function(xhr) {
            showToast((xhr.responseJSON || {}).msg || '保存失败', 'danger');
            loadExcludePatterns();
        }
    });
}
