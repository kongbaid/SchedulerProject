-- ============================================================
-- 脚本定时任务管理系统 - MySQL 初始化脚本
-- 数据库：MySQL 8.0+
-- 字符集：utf8mb4（支持 emoji 等全 Unicode）
-- ============================================================

-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS `task_manager`
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE `task_manager`;

-- ============================================================
-- 1. 系统用户表
-- ============================================================
CREATE TABLE IF NOT EXISTS `sys_user` (
    `id`                INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `username`          VARCHAR(80)     NOT NULL                 COMMENT '用户名',
    `password_hash`     VARCHAR(256)    NOT NULL                 COMMENT '密码哈希（Werkzeug）',
    `role`              VARCHAR(20)     NOT NULL DEFAULT 'user'  COMMENT '用户角色（admin/user）',
    `is_active_user`    TINYINT(1)      NOT NULL DEFAULT 1       COMMENT '是否激活',
    `can_create_task`   TINYINT(1)      NOT NULL DEFAULT 0       COMMENT '是否允许创建任务',
    `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统用户表';

-- ============================================================
-- 2. 脚本定时任务表
-- ============================================================
CREATE TABLE IF NOT EXISTS `script_task` (
    `id`               INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `task_name`        VARCHAR(128)    NOT NULL                 COMMENT '任务名称（唯一）',
    `script_path`      VARCHAR(512)    NOT NULL                 COMMENT '脚本路径（.py 文件）',
    `cron_exp`         VARCHAR(64)     NOT NULL                 COMMENT 'cron 表达式（分 时 日 月 周）',
    `timeout`          INT UNSIGNED    NOT NULL DEFAULT 3600    COMMENT '执行超时时间（秒）',
    `is_active`        TINYINT(1)      NOT NULL DEFAULT 1       COMMENT '是否启用',
    `last_status`      VARCHAR(20)     DEFAULT NULL             COMMENT '最后执行状态',
    `last_executed_at` DATETIME        DEFAULT NULL             COMMENT '最后执行时间',
    `running_pid`      INT             DEFAULT NULL             COMMENT '运行中的进程 PID',
    `params`           TEXT            DEFAULT NULL             COMMENT '执行参数（JSON格式）',
    `python_path`      VARCHAR(512)    DEFAULT NULL             COMMENT 'Python执行路径',
    `max_retries`      INT UNSIGNED    NOT NULL DEFAULT 0       COMMENT '最大重试次数（0=不重试）',
    `retry_delay`      INT UNSIGNED    NOT NULL DEFAULT 1       COMMENT '重试间隔基数（秒，指数退避）',
    `sms_notify`       TINYINT(1)      DEFAULT NULL             COMMENT '失败短信通知（NULL=跟随全局, 1=开启, 0=关闭）',
    `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_task_name` (`task_name`),
    KEY `idx_is_active` (`is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='脚本定时任务表';

-- ============================================================
-- 3. 任务执行日志表
-- ============================================================
CREATE TABLE IF NOT EXISTS `task_log` (
    `id`           INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `task_id`      INT UNSIGNED    NOT NULL                 COMMENT '关联任务 ID',
    `trigger_type` VARCHAR(20)     NOT NULL DEFAULT 'manual' COMMENT '触发类型（manual/cron）',
    `status`       VARCHAR(20)     NOT NULL DEFAULT 'running' COMMENT '执行状态',
    `log_content`  LONGTEXT        DEFAULT NULL             COMMENT '完整输出日志',
    `pid`          INT             DEFAULT NULL             COMMENT '执行进程 PID',
    `exec_params`  TEXT            DEFAULT NULL             COMMENT '执行参数快照（JSON）',
    `retry_count`  INT UNSIGNED    NOT NULL DEFAULT 0       COMMENT '当前重试次数',
    `start_time`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '开始时间',
    `end_time`     DATETIME        DEFAULT NULL             COMMENT '结束时间',
    PRIMARY KEY (`id`),
    KEY `idx_task_id` (`task_id`),
    KEY `idx_task_status_endtime` (`task_id`, `status`, `end_time`),
    CONSTRAINT `fk_log_task` FOREIGN KEY (`task_id`)
        REFERENCES `script_task` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务执行日志表';

-- ============================================================
-- 4. 用户-任务权限关联表
-- ============================================================
CREATE TABLE IF NOT EXISTS `user_task_permission` (
    `user_id`          INT UNSIGNED    NOT NULL                 COMMENT '用户ID',
    `task_id`          INT UNSIGNED    NOT NULL                 COMMENT '任务ID',
    `can_edit_script`  TINYINT(1)      NOT NULL DEFAULT 1       COMMENT '是否允许编辑脚本内容',
    `created_at`       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '权限授予时间',
    PRIMARY KEY (`user_id`, `task_id`),
    CONSTRAINT `fk_perm_user` FOREIGN KEY (`user_id`)
        REFERENCES `sys_user` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT `fk_perm_task` FOREIGN KEY (`task_id`)
        REFERENCES `script_task` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户-任务权限关联表';

-- ============================================================
-- 5. Python 解释器路径表
-- ============================================================
CREATE TABLE IF NOT EXISTS `python_path` (
    `id`          INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `path`        VARCHAR(512)    NOT NULL                 COMMENT 'Python可执行文件路径',
    `is_default`  TINYINT(1)      NOT NULL DEFAULT 0       COMMENT '是否为默认路径',
    `description` VARCHAR(256)    DEFAULT NULL             COMMENT '备注说明',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_path` (`path`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='Python解释器路径表';

-- ============================================================
-- 6. 已保存项目路径表（文件编辑器使用）
-- ============================================================
CREATE TABLE IF NOT EXISTS `saved_project` (
    `id`                INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `project_name`      VARCHAR(128)    NOT NULL                 COMMENT '项目名称',
    `project_path`      VARCHAR(512)    NOT NULL                 COMMENT '项目路径',
    `description`       TEXT            DEFAULT NULL             COMMENT '备注说明',
    `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `last_accessed_at`  DATETIME        DEFAULT NULL             COMMENT '最后访问时间',
    `access_count`      INT UNSIGNED    NOT NULL DEFAULT 0       COMMENT '访问次数',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_project_path` (`project_path`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='已保存项目路径表';

-- ============================================================
-- 7. 初始化默认管理员账号（密码: admin123）
-- 注意：此处插入的是示例哈希，实际运行时由 Flask 应用自动初始化
-- 如果已通过应用启动创建，可跳过此步骤
-- ============================================================
-- INSERT IGNORE INTO `sys_user` (`username`, `password_hash`, `role`, `is_active_user`)
-- VALUES ('admin', 'pbkdf2:sha256:...', 'admin', 1);

-- ============================================================
-- 8. 任务标签表
-- ============================================================
CREATE TABLE IF NOT EXISTS `task_tag` (
    `id`          INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `name`        VARCHAR(64)     NOT NULL                 COMMENT '标签名称',
    `color`       VARCHAR(20)     NOT NULL DEFAULT '#6c757d' COMMENT '标签颜色(HEX)',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_tag_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务标签表';

-- ============================================================
-- 9. 任务-标签关联表
-- ============================================================
CREATE TABLE IF NOT EXISTS `task_tag_mapping` (
    `task_id`  INT UNSIGNED NOT NULL COMMENT '任务ID',
    `tag_id`   INT UNSIGNED NOT NULL COMMENT '标签ID',
    PRIMARY KEY (`task_id`, `tag_id`),
    CONSTRAINT `fk_tagmap_task` FOREIGN KEY (`task_id`)
        REFERENCES `script_task` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT `fk_tagmap_tag` FOREIGN KEY (`tag_id`)
        REFERENCES `task_tag` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务-标签关联表';

-- ============================================================
-- 10. 任务依赖关系表
-- ============================================================
CREATE TABLE IF NOT EXISTS `task_dependency` (
    `id`                 INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `upstream_task_id`   INT UNSIGNED    NOT NULL                 COMMENT '上游任务ID（先执行）',
    `downstream_task_id` INT UNSIGNED    NOT NULL                 COMMENT '下游任务ID（后触发）',
    `is_active`          TINYINT(1)      NOT NULL DEFAULT 1       COMMENT '是否启用',
    `created_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_dep` (`upstream_task_id`, `downstream_task_id`),
    KEY `idx_upstream` (`upstream_task_id`),
    KEY `idx_downstream` (`downstream_task_id`),
    CONSTRAINT `fk_dep_upstream` FOREIGN KEY (`upstream_task_id`)
        REFERENCES `script_task` (`id`) ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT `fk_dep_downstream` FOREIGN KEY (`downstream_task_id`)
        REFERENCES `script_task` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务依赖关系表';

-- ============================================================
-- 11. 系统配置表
-- ============================================================
CREATE TABLE IF NOT EXISTS `system_config` (
    `id`           INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `config_key`   VARCHAR(128)    NOT NULL                 COMMENT '配置键',
    `config_value` TEXT            DEFAULT NULL             COMMENT '配置值',
    `description`  VARCHAR(256)    DEFAULT NULL             COMMENT '配置说明',
    `updated_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_config_key` (`config_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='系统配置表';

-- ============================================================
-- 12. 备份目标目录配置表
-- ============================================================
CREATE TABLE IF NOT EXISTS `backup_target` (
    `id`            INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `target_name`   VARCHAR(128)    NOT NULL                 COMMENT '目录别名',
    `target_path`   VARCHAR(512)    NOT NULL                 COMMENT '备份存储路径',
    `description`   TEXT            DEFAULT NULL             COMMENT '备注说明',
    `is_default`    TINYINT(1)      NOT NULL DEFAULT 0       COMMENT '是否默认备份目标 1/0',
    `created_by`    VARCHAR(64)     DEFAULT NULL             COMMENT '创建人',
    `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`    DATETIME        DEFAULT NULL             COMMENT '更新时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_target_path` (`target_path`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='备份目标目录配置表';

-- ============================================================
-- 13. 备份记录表
-- ============================================================
CREATE TABLE IF NOT EXISTS `backup_record` (
    `id`                INT UNSIGNED    NOT NULL AUTO_INCREMENT  COMMENT '主键',
    `record_name`       VARCHAR(256)    NOT NULL                 COMMENT '备份名称',
    `source_type`       VARCHAR(20)     NOT NULL                 COMMENT '来源类型: project/folder/file/pre_restore_snapshot',
    `source_path`       VARCHAR(1024)   NOT NULL                 COMMENT '备份源路径',
    `source_name`       VARCHAR(256)    NOT NULL                 COMMENT '源名称',
    `target_id`         INT UNSIGNED    NOT NULL                 COMMENT '备份目标目录ID',
    `target_path`       VARCHAR(512)    NOT NULL                 COMMENT '冗余: 备份存储目录',
    `backup_file_name`  VARCHAR(256)    NOT NULL                 COMMENT '备份文件名',
    `backup_file_path`  VARCHAR(1024)   NOT NULL                 COMMENT '备份文件完整路径',
    `backup_size`       BIGINT          NOT NULL DEFAULT 0       COMMENT '文件大小(bytes)',
    `file_count`        INT UNSIGNED    NOT NULL DEFAULT 0       COMMENT '包含文件数',
    `status`            VARCHAR(20)     NOT NULL DEFAULT 'success' COMMENT 'success/failed',
    `error_message`     TEXT            DEFAULT NULL             COMMENT '失败原因',
    `created_by`        VARCHAR(64)     DEFAULT NULL             COMMENT '创建人',
    `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `restored_at`       DATETIME        DEFAULT NULL             COMMENT '最近恢复时间',
    `restore_count`     INT UNSIGNED    NOT NULL DEFAULT 0       COMMENT '恢复次数',
    PRIMARY KEY (`id`),
    KEY `idx_target` (`target_id`),
    KEY `idx_source` (`source_type`, `source_path`(255)),
    KEY `idx_created` (`created_at`),
    CONSTRAINT `fk_record_target` FOREIGN KEY (`target_id`)
        REFERENCES `backup_target` (`id`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='备份记录表';
