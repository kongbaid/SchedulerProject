#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
任务名称: __TASK_NAME__
创建时间: __CREATE_TIME__
"""

# ══════════════════════════════════════════════════════════
#  第 1 区｜导入（不用动）
# ══════════════════════════════════════════════════════════
import os
import sys
import time
import json
import signal
import logging
import argparse
import traceback
from datetime import datetime, timedelta

from sqlalchemy import (
    create_engine, Column,
    Integer, String, Text, Float, DateTime, Date,
)
from sqlalchemy.orm import sessionmaker, declarative_base

# ══════════════════════════════════════════════════════════
#  第 2 区｜配置（改数据库地址就行）
# ══════════════════════════════════════════════════════════


# ★ 数据库地址：建议通过环境变量 TASK_DB_URL 注入，勿在代码中硬编码明文账号密码
DB_URL = os.environ.get(
    "TASK_DB_URL",
    "mysql+pymysql://<user>:<password>@<host>:3306/<database>",
)

def parse_args():
    """
    接收调度系统传来的参数。
    需要新参数？照着格式加一行 add_argument 就行。
    """
    parser = argparse.ArgumentParser(description="任务: __TASK_NAME__")

    parser.add_argument("--date", type=int, default=7,
                        help="往前处理几天的数据（默认 7）")

    # ↓ 需要新参数就加在这里 ↓
    # parser.add_argument("--keyword", type=str, default="", help="关键词")

    return parser.parse_args()


# ══════════════════════════════════════════════════════════
#  第 3 区｜日志 & 优雅退出（不用动）
# ══════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    logger.warning("⚠️  收到终止信号，等当前批次跑完就退出...")


signal.signal(signal.SIGTERM, _handle_signal)
if sys.platform != "win32":
    signal.signal(signal.SIGINT, _handle_signal)

# ══════════════════════════════════════════════════════════
#  第 4 区｜表结构（★ 改成你的字段 ★）
# ══════════════════════════════════════════════════════════

Base = declarative_base()


class DataRecord(Base):
    """
    数据库表结构。

    规则：
      - id / spdate / sdate 是固定字段，别删别改。
      - 你的业务字段加在下面，字段名要和你爬虫返回的
        JSON 里的 key 一一对应，模板会自动映射。

    举例：
      你的爬虫返回 [{"title": "xxx", "url": "yyy"}, ...]
      那这里就要有 title 和 url 这两个字段。
    """
    __tablename__ = "data_record"  # ★ 改成你的表名

    # ----- 固定字段（别动）-----
    id = Column(Integer, primary_key=True, autoincrement=True)
    spdate = Column(DateTime, nullable=True, comment="爬取时间")
    sdate = Column(Date, nullable=True, comment="数据日期")

    # ----- 业务字段（★ 改成你的，key 名要和爬虫返回的 JSON 一致）-----
    title = Column(String(512), nullable=True, comment="标题")
    content = Column(Text, nullable=True, comment="正文")
    url = Column(String(1024), nullable=True, comment="链接")

    # score   = Column(Float,        nullable=True, comment="评分")
    # author  = Column(String(128),  nullable=True, comment="作者")

    def __repr__(self):
        return f"<DataRecord id={self.id} sdate={self.sdate}>"


# ══════════════════════════════════════════════════════════
#  第 5 区｜爬虫逻辑（★ 你的代码写这里 ★）
# ══════════════════════════════════════════════════════════

def crawl_data(target_date: str) -> list:
    """
    ★ 你只需要改这个函数 ★

    作用：爬取 target_date 这一天的数据。
    要求：返回一个 list[dict]，也就是 JSON 数组。
         dict 的 key 必须和第 4 区的字段名对得上。

    参数:
        target_date: 字符串，格式 "2026-07-27"

    返回:
        [
            {"title": "新闻1", "content": "...", "url": "https://..."},
            {"title": "新闻2", "content": "...", "url": "https://..."},
        ]

    如果这天没数据，返回空列表 [] 就行。
    """
    # ---------- 在下面写你的爬虫代码 ----------
    # 随便你怎么爬：requests / selenium / scrapy / 调接口，都行
    # 最终把结果整理成 list[dict] 返回

    data = []  # ← 你的爬虫结果放这里

    # 示例（删掉，换成你自己的）:
    # import requests
    # resp = requests.get(f"https://api.xxx.com/news?date={target_date}")
    # data = resp.json().get("list", [])

    return data


# ══════════════════════════════════════════════════════════
#  第 6 区｜映射 & 入库（不用动，模板自动处理）
# ══════════════════════════════════════════════════════════

def dict_list_to_orm(data_list: list, target_date) -> list:
    """
    把爬虫返回的 JSON 数组 → ORM 对象列表。

    自动做的事：
      1. 遍历每个 dict
      2. 只取 DataRecord 里有的字段（多余的 key 自动忽略，不会报错）
      3. 自动填上 spdate（爬取时间）和 sdate（数据日期）
      4. 跳过空 dict
    """
    # 拿到 DataRecord 里所有业务字段名（排除固定的 id/spdate/sdate）
    valid_columns = {
        col.name for col in DataRecord.__table__.columns
        if col.name not in ("id", "spdate", "sdate")
    }

    records = []
    for item in data_list:
        if not item or not isinstance(item, dict):
            continue

        obj = DataRecord()
        obj.spdate = datetime.now()  # 爬取时间，自动填
        obj.sdate = target_date  # 数据日期，自动填

        # 只映射表里有的字段，多余的自动忽略
        for key, value in item.items():
            if key in valid_columns:
                setattr(obj, key, value)

        records.append(obj)

    return records


def save_to_db(session, new_records: list, target_date):
    """
    入库：先删这天的旧数据，再插新数据。
    同一个事务，要么全成功，要么全回滚，不会出现删了旧的又没插进新的。
    """
    try:
        deleted = (
            session.query(DataRecord)
            .filter(DataRecord.sdate == target_date)
            .delete(synchronize_session=False)
        )
        if new_records:
            session.add_all(new_records)
        session.commit()
        logger.info("   ✅ 入库完成: 删旧 %d 条 → 插新 %d 条", deleted, len(new_records))
    except Exception as e:
        session.rollback()
        logger.error("   ❌ 入库失败: %s", e)
        traceback.print_exc()
        raise


# ══════════════════════════════════════════════════════════
#  第 7 区｜主函数（串联一切，一般不用改）
# ══════════════════════════════════════════════════════════

def main() -> int:
    args = parse_args()

    logger.info("=" * 50)
    logger.info("🚀 任务启动: __TASK_NAME__")
    logger.info("   Python : %s", sys.version.split()[0])
    logger.info("   数据库 : %s", DB_URL.split("@")[-1])  # 不打印密码
    logger.info("   天数   : 最近 %d 天", args.date)
    logger.info("=" * 50)

    # ----- 初始化数据库（表不存在会自动建）-----
    engine = create_engine(DB_URL, echo=False, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # ----- 算日期范围 -----
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=args.date)

    total_saved = 0
    current = start_date

    try:
        while current <= end_date:

            # 收到 kill 信号 → 优雅退出
            if _shutdown:
                logger.warning("⚠️  提前退出，已处理到 %s", current)
                break

            date_str = current.strftime("%Y-%m-%d")
            logger.info("📅 处理日期: %s", date_str)

            # ① 调你的爬虫函数，拿到 JSON 数组
            raw_data = crawl_data(date_str)
            logger.info("   爬虫返回 %d 条原始数据", len(raw_data))

            # ② JSON 数组 → ORM 对象（自动映射字段）
            records = dict_list_to_orm(raw_data, current)
            logger.info("   有效映射 %d 条", len(records))

            # ③ 入库（先删旧 → 再插新）
            if records:
                save_to_db(db, records, current)
                total_saved += len(records)
            else:
                logger.info("   ⏭️  无数据，跳过")

            # 下一天
            current += timedelta(days=1)

    except Exception:
        logger.exception("💥 任务异常")
        return 1
    finally:
        db.close()
        engine.dispose()

    logger.info("=" * 50)
    logger.info("🎉 全部完成! 共入库 %d 条", total_saved)
    logger.info("=" * 50)
    return 0


# ══════════════════════════════════════════════════════════
#  第 8 区｜入口（不用动）
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    sys.exit(main())
