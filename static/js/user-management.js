/**
 * 用户管理模块 - 前端交互逻辑
 */

// 全局变量
var currentPage = 1;
var currentKeyword = '';
var allTasks = []; // 缓存所有任务

// ============================================================
// 页面加载完成后初始化
// ============================================================
$(document).ready(function() {
    loadUsers(1);
    loadAllTasks(); // 预加载所有任务用于权限分配
    
    // 用户表格按钮事件委托（避免 inline onclick 转义问题）
    $(document).on('click', '.btn-edit-user', function() {
        var $btn = $(this);
        showEditUserModal({
            id: $btn.data('id'),
            username: $btn.data('name'),
            role: $btn.data('role'),
            can_create_task: $btn.data('can-create') === 1,
            is_active: $btn.data('active') === 1
        });
    });
    $(document).on('click', '.btn-delete-user', function() {
        var $btn = $(this);
        deleteUser($btn.data('id'), $btn.data('name'));
    });
    $(document).on('click', '.btn-reset-pwd', function() {
        var $btn = $(this);
        showResetPasswordModal($btn.data('id'), $btn.data('name'));
    });
    $(document).on('click', '.btn-assign-perm', function() {
        var $btn = $(this);
        showPermissionModal($btn.data('id'), $btn.data('name'));
    });
    
    // 权限弹窗关闭时释放任务缓存，避免长期占用内存
    $('#permissionModal').on('hidden.bs.modal', function() {
        allTasks = [];
    });
});

// ============================================================
// 加载用户列表
// ============================================================
function loadUsers(page) {
    currentPage = page;
    
    $.ajax({
        url: '/api/users',
        type: 'GET',
        data: {
            page: page,
            per_page: 10,
            keyword: currentKeyword
        },
        success: function(res) {
            if (res.code === 200) {
                renderUserTable(res.data);
                renderPagination('pagination', res.data.page, res.data.pages, loadUsers);
            } else {
                showToast(res.msg || '加载失败', 'danger');
            }
        },
        error: function() {
            showToast('网络错误', 'danger');
        }
    });
}

// ============================================================
// 渲染用户表格
// ============================================================
function renderUserTable(data) {
    var $tbody = $('#userTableBody');
    $tbody.empty();
    
    if (!data.items || data.items.length === 0) {
        $tbody.html('<tr><td colspan="7" class="text-center text-muted py-5">暂无数据</td></tr>');
        return;
    }
    
    var html = '';
    data.items.forEach(function(user) {
        var roleBadge = user.role === 'admin' 
            ? '<span class="badge bg-danger">管理员</span>'
            : '<span class="badge bg-info">普通用户</span>';
        
        var statusBadge = user.is_active
            ? '<span class="badge bg-success">启用</span>'
            : '<span class="badge bg-secondary">禁用</span>';
        
        var taskCount = user.managed_task_ids ? user.managed_task_ids.length : 0;
        
        // admin 用户不能删除
        var deleteBtn = user.username === 'admin'
            ? '<button class="btn btn-sm btn-outline-secondary" disabled title="不能删除管理员"><i class="bi bi-trash"></i></button>'
            : '<button class="btn btn-sm btn-outline-danger btn-delete-user" data-id="' + user.id + '" data-name="' + escapeHtml(user.username) + '" title="删除"><i class="bi bi-trash"></i></button>';
        
        html += '<tr>' +
            '<td>' + user.id + '</td>' +
            '<td><strong>' + escapeHtml(user.username) + '</strong></td>' +
            '<td>' + roleBadge + '</td>' +
            '<td>' + statusBadge + '</td>' +
            '<td><span class="badge bg-primary">' + taskCount + ' 个任务</span></td>' +
            '<td>' + escapeHtml(user.created_at) + '</td>' +
            '<td class="text-center">' +
                '<button class="btn btn-sm btn-outline-primary me-1 btn-edit-user" ' +
                    'data-id="' + user.id + '" ' +
                    'data-name="' + escapeHtml(user.username) + '" ' +
                    'data-role="' + escapeHtml(user.role) + '" ' +
                    'data-can-create="' + (user.can_create_task ? '1' : '0') + '" ' +
                    'data-active="' + (user.is_active ? '1' : '0') + '" ' +
                    'title="编辑"><i class="bi bi-pencil"></i></button>' +
                '<button class="btn btn-sm btn-outline-warning me-1 btn-reset-pwd" data-id="' + user.id + '" data-name="' + escapeHtml(user.username) + '" title="重置密码"><i class="bi bi-key"></i></button>' +
                '<button class="btn btn-sm btn-outline-success me-1 btn-assign-perm" data-id="' + user.id + '" data-name="' + escapeHtml(user.username) + '" title="分配任务"><i class="bi bi-shuffle"></i></button>' +
                deleteBtn +
            '</td>' +
            '</tr>';
    });
    
    $tbody.html(html);
}

// ============================================================
// 搜索用户
// ============================================================
function searchUsers() {
    currentKeyword = $('#searchKeyword').val().trim();
    loadUsers(1);
}

function resetSearch() {
    $('#searchKeyword').val('');
    currentKeyword = '';
    loadUsers(1);
}

// ============================================================
// 显示添加用户模态框
// ============================================================
function showAddUserModal() {
    $('#userModalTitle').text('添加用户');
    $('#userId').val('');
    $('#username').val('').prop('readonly', false);
    $('#password').val('').prop('required', true);
    $('#passwordGroup').show();
    $('#role').val('user');
    $('#canCreateTask').prop('checked', false);
    $('#isActive').prop('checked', true);
    $('#createTaskPermGroup').show();
    $('#userModal').modal('show');
}

// ============================================================
// 显示编辑用户模态框
// ============================================================
function showEditUserModal(user) {
    $('#userModalTitle').text('编辑用户');
    $('#userId').val(user.id);
    $('#username').val(user.username).prop('readonly', user.username === 'admin');
    $('#password').val('').prop('required', false);
    $('#passwordGroup').hide(); // 编辑时不显示密码
    $('#role').val(user.role);
    $('#canCreateTask').prop('checked', user.can_create_task || false);
    $('#isActive').prop('checked', user.is_active);
    // 管理员不显示创建任务权限选项
    if (user.role === 'admin') {
        $('#createTaskPermGroup').hide();
    } else {
        $('#createTaskPermGroup').show();
    }
    $('#userModal').modal('show');
}

// ============================================================
// 保存用户（添加或更新）
// ============================================================
function saveUser() {
    var userId = $('#userId').val();
    var username = $('#username').val().trim();
    var password = $('#password').val();
    var role = $('#role').val();
    var canCreateTask = $('#canCreateTask').is(':checked');
    var isActive = $('#isActive').is(':checked');
    
    // 验证
    if (!username || username.length < 3) {
        showToast('用户名至少3个字符', 'warning');
        return;
    }
    
    if (!userId && (!password || password.length < 6)) {
        showToast('密码至少6个字符', 'warning');
        return;
    }
    
    var data = {
        username: username,
        role: role,
        is_active: isActive,
        can_create_task: canCreateTask
    };
    
    // 添加时需要密码
    if (!userId) {
        data.password = password;
    }
    
    var url = userId ? '/api/user/' + userId : '/api/user';
    var method = userId ? 'PUT' : 'POST';
    
    $.ajax({
        url: url,
        type: method,
        contentType: 'application/json',
        data: JSON.stringify(data),
        success: function(res) {
            if (res.code === 200) {
                showToast(userId ? '用户更新成功' : '用户创建成功', 'success');
                $('#userModal').modal('hide');
                loadUsers(currentPage);
            } else {
                showToast(res.msg || '操作失败', 'danger');
            }
        },
        error: function() {
            showToast('网络错误', 'danger');
        }
    });
}

// ============================================================
// 删除用户
// ============================================================
function deleteUser(userId, username) {
    if (!confirm('确定要删除用户 "' + username + '" 吗？此操作不可恢复！')) {
        return;
    }
    
    $.ajax({
        url: '/api/user/' + userId,
        type: 'DELETE',
        success: function(res) {
            if (res.code === 200) {
                showToast('用户已删除', 'success');
                loadUsers(currentPage);
            } else {
                showToast(res.msg || '删除失败', 'danger');
            }
        },
        error: function() {
            showToast('网络错误', 'danger');
        }
    });
}

// ============================================================
// 显示重置密码模态框
// ============================================================
function showResetPasswordModal(userId, username) {
    $('#resetUserId').val(userId);
    $('#newPassword').val('');
    $('#resetPasswordModal').modal('show');
}

// ============================================================
// 重置密码
// ============================================================
function resetPassword() {
    var userId = $('#resetUserId').val();
    var newPassword = $('#newPassword').val();
    
    if (!newPassword || newPassword.length < 6) {
        showToast('密码至少6个字符', 'warning');
        return;
    }
    
    $.ajax({
        url: '/api/user/' + userId + '/password',
        type: 'PUT',
        contentType: 'application/json',
        data: JSON.stringify({ new_password: newPassword }),
        success: function(res) {
            if (res.code === 200) {
                showToast('密码重置成功', 'success');
                $('#resetPasswordModal').modal('hide');
            } else {
                showToast(res.msg || '重置失败', 'danger');
            }
        },
        error: function() {
            showToast('网络错误', 'danger');
        }
    });
}

// ============================================================
// 加载所有任务（用于权限分配）
// ============================================================
function loadAllTasks() {
    $.ajax({
        url: '/api/tasks',
        type: 'GET',
        data: { page: 1, per_page: 1000 },
        success: function(res) {
            if (res.code === 200) {
                allTasks = res.data.items || [];
            }
        }
    });
}

// ============================================================
// 显示权限分配模态框
// ============================================================
function showPermissionModal(userId, username) {
    $('#permUserId').val(userId);
    $('#permUsername').text(username);
    
    var renderAndShow = function() {
        renderTaskList(userId);
        $('#permissionModal').modal('show');
    };
    
    // 确保任务缓存已加载（可能在上次关闭弹窗后被清空）
    if (!allTasks || allTasks.length === 0) {
        $.ajax({
            url: '/api/tasks',
            type: 'GET',
            data: { page: 1, per_page: 1000 },
            success: function(res) {
                if (res.code === 200) {
                    allTasks = res.data.items || [];
                }
                renderAndShow();
            },
            error: function() {
                renderAndShow();
            }
        });
    } else {
        renderAndShow();
    }
}

// ============================================================
// 渲染任务列表（带勾选框和脚本编辑权限）
// ============================================================
function renderTaskList(userId) {
    var $taskList = $('#taskList');
    
    // 先获取用户当前的任务权限
    $.ajax({
        url: '/api/user/' + userId + '/tasks',
        type: 'GET',
        success: function(res) {
            var assignedTasks = [];
            if (res.code === 200 && res.data.tasks) {
                assignedTasks = res.data.tasks;
            }
            
            // 渲染任务复选框
            if (!allTasks || allTasks.length === 0) {
                $taskList.html('<p class="text-muted text-center">暂无任务</p>');
                return;
            }
            
            var html = '';
            allTasks.forEach(function(task) {
                var assignedTask = assignedTasks.find(function(t) { return t.id === task.id; });
                var isChecked = assignedTask !== undefined;
                var canEditScript = isChecked ? (assignedTask.can_edit_script !== false) : true;
                
                var statusBadge = task.last_status 
                    ? '<span class="badge bg-' + getStatusColor(task.last_status) + '">' + escapeHtml(task.last_status) + '</span>'
                    : '';
                
                html += '<div class="task-perm-item mb-2">' +
                    '<div class="form-check">' +
                        '<input class="form-check-input task-checkbox" type="checkbox" value="' + task.id + '" id="task_' + task.id + '" ' + (isChecked ? 'checked' : '') + '>' +
                        '<label class="form-check-label" for="task_' + task.id + '">' +
                            '<strong>' + escapeHtml(task.task_name) + '</strong> ' +
                            '<span class="text-muted">(ID: ' + task.id + ')</span> ' +
                            statusBadge +
                            '<br><small class="text-muted">' + escapeHtml(task.script_path) + '</small>' +
                        '</label>' +
                    '</div>' +
                    '<div class="perm-options ms-4 mt-1" ' + (isChecked ? '' : 'style="display:none"') + '>' +
                        '<label class="perm-option-label">' +
                            '<input type="checkbox" class="edit-script-perm" data-task-id="' + task.id + '" ' + (canEditScript ? 'checked' : '') + '>' +
                            '<span class="ms-1"><i class="bi bi-code-square me-1"></i>允许编辑脚本</span>' +
                        '</label>' +
                    '</div>' +
                '</div>';
            });
            
            $taskList.html(html);
            
            // 绑定主复选框事件
            $('.task-checkbox').on('change', function() {
                var taskId = $(this).val();
                var $permOptions = $(this).closest('.task-perm-item').find('.perm-options');
                
                if ($(this).prop('checked')) {
                    $permOptions.slideDown(200);
                } else {
                    $permOptions.slideUp(200);
                }
            });
        }
    });
}

// ============================================================
// 获取状态颜色
// ============================================================
function getStatusColor(status) {
    var colors = {
        'success': 'success',
        'failed': 'danger',
        'running': 'primary',
        'timeout': 'warning',
        'stopped': 'secondary'
    };
    return colors[status] || 'secondary';
}

// ============================================================
// 保存权限
// ============================================================
function savePermissions() {
    var userId = $('#permUserId').val();
    
    // 获取所有勾选的任务及其权限配置
    var selectedTasks = [];
    $('.task-checkbox:checked').each(function() {
        var taskId = parseInt($(this).val());
        var canEditScript = $('.edit-script-perm[data-task-id="' + taskId + '"]').prop('checked');
        selectedTasks.push({
            task_id: taskId,
            can_edit_script: canEditScript
        });
    });
    
    // 获取用户当前已分配的任务
    $.ajax({
        url: '/api/user/' + userId + '/tasks',
        type: 'GET',
        success: function(res) {
            var currentTasks = [];
            if (res.code === 200 && res.data.tasks) {
                currentTasks = res.data.tasks;
            }
            
            // 计算需要添加的任务
            var toAdd = selectedTasks.filter(function(selected) {
                return !currentTasks.find(function(current) { return current.id === selected.task_id; });
            });
            
            // 计算需要移除的任务
            var toRemove = currentTasks.filter(function(current) {
                return !selectedTasks.find(function(selected) { return selected.task_id === current.id; });
            });
            
            // 计算需要更新权限的任务（已存在但 can_edit_script 变更）
            var toUpdate = selectedTasks.filter(function(selected) {
                var current = currentTasks.find(function(c) { return c.id === selected.task_id; });
                return current && current.can_edit_script !== selected.can_edit_script;
            });
            
            // 执行批量操作
            batchAssignTasks(userId, toAdd, toRemove, toUpdate);
        }
    });
}

// ============================================================
// 批量分配/移除/更新任务权限
// ============================================================
function batchAssignTasks(userId, toAdd, toRemove, toUpdate) {
    var totalOps = toAdd.length + toRemove.length + toUpdate.length;
    
    if (totalOps === 0) {
        showToast('权限未变更', 'info');
        $('#permissionModal').modal('hide');
        loadUsers(currentPage);
        return;
    }
    
    var completed = 0;
    var hasError = false;
    
    function checkComplete() {
        completed++;
        if (completed === totalOps) {
            if (hasError) {
                showToast('部分操作失败，请查看日志', 'warning');
            } else {
                showToast('权限更新成功', 'success');
            }
            $('#permissionModal').modal('hide');
            loadUsers(currentPage);
        }
    }
    
    // 添加任务（带权限配置）
    toAdd.forEach(function(taskData) {
        $.ajax({
            url: '/api/user/' + userId + '/tasks',
            type: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                task_id: taskData.task_id,
                can_edit_script: taskData.can_edit_script
            }),
            complete: function(res) {
                if (res.responseJSON && res.responseJSON.code !== 200) {
                    hasError = true;
                    console.error('分配任务失败:', res.responseJSON.msg);
                }
                checkComplete();
            }
        });
    });
    
    // 移除任务
    toRemove.forEach(function(taskData) {
        var taskId = taskData.id || taskData.task_id;
        $.ajax({
            url: '/api/user/' + userId + '/tasks/' + taskId,
            type: 'DELETE',
            complete: function(res) {
                if (res.responseJSON && res.responseJSON.code !== 200) {
                    hasError = true;
                    console.error('移除任务失败:', res.responseJSON.msg);
                }
                checkComplete();
            }
        });
    });
    
    // 更新权限（先删除再添加）
    toUpdate.forEach(function(taskData) {
        // 先删除旧权限
        $.ajax({
            url: '/api/user/' + userId + '/tasks/' + taskData.task_id,
            type: 'DELETE',
            complete: function(res) {
                if (res.responseJSON && res.responseJSON.code !== 200) {
                    hasError = true;
                    console.error('删除旧权限失败:', res.responseJSON.msg);
                }
                // 再添加新权限
                $.ajax({
                    url: '/api/user/' + userId + '/tasks',
                    type: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({
                        task_id: taskData.task_id,
                        can_edit_script: taskData.can_edit_script
                    }),
                    complete: function(res) {
                        if (res.responseJSON && res.responseJSON.code !== 200) {
                            hasError = true;
                            console.error('更新权限失败:', res.responseJSON.msg);
                        }
                        checkComplete();
                    }
                });
            }
        });
    });
}
