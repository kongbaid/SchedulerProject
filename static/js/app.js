/**
 * 脚本任务管理系统 - 公共 JS 工具
 * 提供全局工具函数：Toast 提示、分页渲染、HTML 转义等
 */

// ============================================================
// 全局 Toast 消息提示
// ============================================================
function showToast(message, type) {
    type = type || 'success';
    var $toast = $('<div class="toast-msg toast-' + type + '">' +
        '<i class="bi bi-' + (type === 'success' ? 'check-circle' : type === 'danger' ? 'x-circle' : 'exclamation-triangle') +
        ' me-2"></i>' + message + '</div>');
    $('#global-toast').append($toast);
    setTimeout(function() {
        $toast.fadeOut(300, function() { $(this).remove(); });
    }, 3000);
}

// ============================================================
// HTML 转义（防止 XSS）
// ============================================================
function escapeHtml(text) {
    if (!text) return '';
    var map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, function(m) { return map[m]; });
}

// ============================================================
// 通用分页渲染
// ============================================================
/**
 * 渲染分页控件
 * @param {string} containerId - 分页 UL 元素 ID
 * @param {number} currentPage - 当前页码
 * @param {number} totalPages  - 总页数
 * @param {Function} callback  - 翻页回调函数(page)
 */
function renderPagination(containerId, currentPage, totalPages, callback) {
    var $ul = $('#' + containerId);
    $ul.empty();

    if (totalPages <= 1) return;

    // 上一页
    $ul.append(
        '<li class="page-item ' + (currentPage <= 1 ? 'disabled' : '') + '">' +
        '<a class="page-link" href="#" data-page="' + (currentPage - 1) + '">&laquo;</a></li>'
    );

    // 页码（最多显示7个）
    var start = Math.max(1, currentPage - 3);
    var end = Math.min(totalPages, currentPage + 3);

    if (start > 1) {
        $ul.append('<li class="page-item"><a class="page-link" href="#" data-page="1">1</a></li>');
        if (start > 2) {
            $ul.append('<li class="page-item disabled"><span class="page-link">...</span></li>');
        }
    }

    for (var i = start; i <= end; i++) {
        $ul.append(
            '<li class="page-item ' + (i === currentPage ? 'active' : '') + '">' +
            '<a class="page-link" href="#" data-page="' + i + '">' + i + '</a></li>'
        );
    }

    if (end < totalPages) {
        if (end < totalPages - 1) {
            $ul.append('<li class="page-item disabled"><span class="page-link">...</span></li>');
        }
        $ul.append(
            '<li class="page-item"><a class="page-link" href="#" data-page="' + totalPages + '">' + totalPages + '</a></li>'
        );
    }

    // 下一页
    $ul.append(
        '<li class="page-item ' + (currentPage >= totalPages ? 'disabled' : '') + '">' +
        '<a class="page-link" href="#" data-page="' + (currentPage + 1) + '">&raquo;</a></li>'
    );

    // 绑定点击事件
    $ul.find('.page-link').on('click', function(e) {
        e.preventDefault();
        var page = parseInt($(this).data('page'));
        if (page >= 1 && page <= totalPages && page !== currentPage) {
            callback(page);
        }
    });
}

// ============================================================
// 全局 AJAX 配置：处理 401 未登录跳转
// ============================================================
$(document).ajaxError(function(event, xhr) {
    if (xhr.status === 401) {
        showToast('登录已过期，请重新登录', 'warning');
        setTimeout(function() {
            window.location.href = '/login';
        }, 1500);
    }
});


// ============================================================
// 快捷路径标签映射（文件浏览器 / 文件夹浏览器共用）
// ============================================================
var QUICK_PATH_LABELS = {
    'current_dir': '📁 当前目录',
    'home_dir': '🏠 用户目录',
    'desktop': '🖥️ 桌面',
    'documents': '📄 文档',
    'downloads': '⬇️ 下载',
    'program_files': '📦 Program Files',
    'program_files_x86': '📦 Program Files (x86)',
    'local_appdata': '📂 AppData\\Local',
    'c_drive': '💾 C盘',
    'd_drive': '💾 D盘',
    'e_drive': '💾 E盘',
    'usr_bin': '/usr/bin',
    'usr_local_bin': '/usr/local/bin',
    'opt': '/opt',
    'root': '/'
};

function getQuickPathLabel(key) {
    if (QUICK_PATH_LABELS[key]) return QUICK_PATH_LABELS[key];
    if (key.startsWith('python_')) return '🐍 ' + key.replace('python_', '');
    return key;
}


// ============================================================
// 通用文件浏览器（工厂函数）
// 用法：var browser = AppFileBrowser({ modalId:'xxx', fileListId:'xxx', ... })
//       browser.show('', '.py');
// ============================================================
// 浏览器实例注册表，供事件委托查找使用
var _fileBrowserRegistry = {};

function AppFileBrowser(config) {
    var self = {
        _callback: null,
        _types: '',
        name: config.varName,

        show: function(initialPath, fileTypes) {
            self._types = fileTypes || '';
            var el = document.getElementById(config.modalId);
            var modal = bootstrap.Modal.getInstance(el) || new bootstrap.Modal(el);
            modal.show();
            self.load(initialPath || '');
        },

        load: function(path) {
            var $fileList = $('#' + config.fileListId);
            var $quickPaths = $('#' + config.quickPathsId);
            var $currentPath = $('#' + config.currentPathId);
            $fileList.html('<div class="text-center text-muted py-4"><div class="spinner-border"></div><div class="mt-2">加载中...</div></div>');
            var params = { path: path || '' };
            if (self._types) params.types = self._types;
            $.ajax({
                url: '/file-editor/api/browse/files',
                method: 'GET',
                data: params,
                success: function(res) {
                    if (res.code === 200) {
                        if (res.data.type === 'quick_paths') {
                            var html = '';
                            $.each(res.data.paths, function(key, value) {
                                html += '<button class="btn btn-sm btn-outline-info fb-load" data-path="' + escapeHtml(value) + '">';
                                html += getQuickPathLabel(key) + '</button>';
                            });
                            $quickPaths.html(html);
                            $currentPath.val('请选择文件');
                            $fileList.html('<div class="text-center text-muted py-4">请点击上方快捷路径开始浏览</div>');
                        } else {
                            $currentPath.val(res.data.current_path);
                            var html = '<div class="list-group">';
                            if (res.data.parent_path) {
                                html += '<button class="list-group-item list-group-item-action fb-load" data-path="' + escapeHtml(res.data.parent_path) + '">';
                                html += '<i class="bi bi-arrow-up-circle text-primary me-2"></i><strong>.. 上级文件夹</strong></button>';
                            }
                            res.data.folders.forEach(function(folder) {
                                html += '<button class="list-group-item list-group-item-action fb-load" data-path="' + escapeHtml(folder.path) + '">';
                                html += '<i class="bi bi-folder text-warning me-2"></i>' + escapeHtml(folder.name) + '</button>';
                            });
                            if (res.data.files.length === 0 && res.data.folders.length === 0) {
                                html += '<div class="list-group-item text-muted text-center">此文件夹为空</div>';
                            } else {
                                res.data.files.forEach(function(file) {
                                    var sizeStr = (file.size / 1024 / 1024).toFixed(2) + ' MB';
                                    html += '<button class="list-group-item list-group-item-action fb-select" data-path="' + escapeHtml(file.path) + '">';
                                    html += '<i class="bi bi-file-earmark-binary text-success me-2"></i>' + escapeHtml(file.name);
                                    html += '<small class="text-muted ms-2">(' + sizeStr + ')</small></button>';
                                });
                            }
                            html += '</div>';
                            $fileList.html(html);
                        }
                    } else {
                        $fileList.html('<div class="alert alert-danger">' + escapeHtml(res.msg) + '</div>');
                    }
                },
                error: function(xhr) {
                    var msg = (xhr.responseJSON || {}).msg || '加载失败';
                    $fileList.html('<div class="alert alert-danger">' + escapeHtml(msg) + '</div>');
                }
            });
        },

        goUp: function() {
            var currentPath = $('#' + config.currentPathId).val();
            if (currentPath && currentPath !== '请选择文件') {
                var lastSlash = currentPath.lastIndexOf('\\');
                if (lastSlash === -1) lastSlash = currentPath.lastIndexOf('/');
                var parentPath = currentPath.substring(0, lastSlash);
                if (parentPath) self.load(parentPath);
            }
        },

        select: function(filePath) {
            if (self._callback) self._callback(filePath);
            bootstrap.Modal.getInstance(document.getElementById(config.modalId)).hide();
        },

        setCallback: function(fn) { self._callback = fn; }
    };
    // 注册实例（同时记录 modalId，供事件委托查找）
    _fileBrowserRegistry[config.varName] = { instance: self, modalId: config.modalId };
    return self;
}

// 根据按钮所在 modal 查找对应的浏览器实例
function _findBrowserByBtn($btn) {
    var modalId = $btn.closest('.modal').attr('id');
    for (var name in _fileBrowserRegistry) {
        if (_fileBrowserRegistry[name].modalId === modalId) {
            return _fileBrowserRegistry[name].instance;
        }
    }
    return null;
}

// 文件浏览器按钮事件委托（全局一次绑定，避免 inline onclick 转义问题）
$(document).on('click', '.fb-load', function() {
    var browser = _findBrowserByBtn($(this));
    if (browser) browser.load($(this).data('path'));
});

$(document).on('click', '.fb-select', function() {
    var browser = _findBrowserByBtn($(this));
    if (browser) browser.select($(this).data('path'));
});


// ============================================================
// 通用 Python 路径管理器（工厂函数）
// 用法：var mgr = AppPythonPathManager({ selectId:'xxx', tableBodyId:'xxx', ... })
//       mgr.init();
// ============================================================
function AppPythonPathManager(config) {
    var self = {
        paths: [],

        init: function(callback) {
            $.getJSON('/api/python-paths', function(res) {
                var data = (res.code === 200) ? res.data : [];
                self.paths = Array.isArray(data) ? data : [];
                if (callback) callback();
            }).fail(function() {
                self.paths = [];
                if (callback) callback();
            });
        },

        renderSelect: function(selectedVal) {
            var select = document.getElementById(config.selectId);
            if (!select) return;
            var html = '';
            if (config.allowSystemDefault) {
                html += '<option value="">系统默认 Python</option>';
            }
            if (self.paths.length === 0 && !config.allowSystemDefault) {
                html = '<option value="python">python (系统默认)</option>';
            } else {
                self.paths.forEach(function(item) {
                    var label = escapeHtml(item.path) + (item.is_default ? ' (默认)' : '');
                    var sel = (selectedVal && selectedVal === item.path) ? ' selected' : '';
                    html += '<option value="' + escapeHtml(item.path) + '"' + sel + '>' + label + '</option>';
                });
            }
            select.innerHTML = html;
        },

        renderTable: function() {
            var tbody = document.getElementById(config.tableBodyId);
            if (!tbody) return;
            var html = '';
            var hasHeader = config.threeColumn;  // 是否使用三列布局（带表头）
            if (self.paths.length === 0) {
                var colspan = hasHeader ? 3 : 2;
                html = '<tr><td colspan="' + colspan + '" class="text-center text-muted py-2">暂无已配置的 Python 路径</td></tr>';
            } else {
                self.paths.forEach(function(item) {
                    html += '<tr>';
                    if (hasHeader) {
                        // 三列布局：路径 | 默认 | 操作
                        html += '<td><small>' + escapeHtml(item.path) + '</small></td>';
                        html += '<td class="text-center">';
                        if (item.is_default) {
                            html += '<span class="badge bg-success">默认</span>';
                        }
                        html += '</td>';
                    } else {
                        // 两列布局：路径+徽章 | 操作
                        html += '<td><small>' + escapeHtml(item.path) + '</small>';
                        if (item.is_default) html += ' <span class="badge bg-success">默认</span>';
                        html += '</td>';
                    }
                    html += '<td class="text-center align-middle" style="white-space:nowrap;">';
                    if (!item.is_default) {
                        html += '<button class="btn btn-sm btn-outline-success me-1" onclick="' + config.varName + '.setDefault(' + item.id + ')" title="设为默认"><i class="bi bi-check2"></i></button>';
                        html += '<button class="btn btn-sm btn-outline-danger" onclick="' + config.varName + '.remove(' + item.id + ')" title="删除"><i class="bi bi-trash"></i></button>';
                    } else {
                        html += '<small class="text-muted">默认不可删</small>';
                    }
                    html += '</td></tr>';
                });
            }
            tbody.innerHTML = html;
        },

        showAddForm: function() {
            $('#' + config.addFormId).slideDown(150);
            $('#' + config.newPathInputId).val('').focus();
        },

        hideAddForm: function() {
            $('#' + config.addFormId).slideUp(150);
        },

        add: function() {
            var inputEl = document.getElementById(config.newPathInputId);
            var defaultEl = document.getElementById(config.setDefaultCheckboxId);
            var newPath = inputEl.value.trim();
            var setDefault = defaultEl ? defaultEl.checked : false;
            if (!newPath) { showToast('请输入Python路径', 'warning'); inputEl.focus(); return; }
            $.ajax({
                url: '/api/python-paths',
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({ path: newPath, is_default: setDefault }),
                success: function(res) {
                    if (res.code === 200) {
                        showToast('Python路径已保存', 'success');
                        inputEl.value = '';
                        if (defaultEl) defaultEl.checked = false;
                        self.hideAddForm();
                        self.refresh();
                    } else {
                        showToast(res.msg, 'danger');
                    }
                },
                error: function(xhr) {
                    showToast((xhr.responseJSON || {}).msg || '添加失败', 'danger');
                }
            });
        },

        setDefault: function(id) {
            $.ajax({
                url: '/api/python-paths/' + id,
                method: 'PUT',
                contentType: 'application/json',
                data: JSON.stringify({ is_default: true }),
                success: function(res) {
                    if (res.code === 200) { showToast('已设为默认', 'success'); self.refresh(); }
                    else { showToast(res.msg, 'danger'); }
                },
                error: function(xhr) { showToast((xhr.responseJSON || {}).msg || '操作失败', 'danger'); }
            });
        },

        remove: function(id) {
            if (!confirm('确定删除此路径？')) return;
            $.ajax({
                url: '/api/python-paths/' + id,
                method: 'DELETE',
                success: function(res) {
                    if (res.code === 200) { showToast('已删除', 'success'); self.refresh(); }
                    else { showToast(res.msg, 'danger'); }
                },
                error: function(xhr) { showToast((xhr.responseJSON || {}).msg || '删除失败', 'danger'); }
            });
        },

        refresh: function() {
            self.init(function() {
                self.renderSelect();
                self.renderTable();
            });
        }
    };
    return self;
}
