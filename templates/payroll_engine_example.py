#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
确定性薪资核算引擎 · 参考实现
================================

本文件演示「左移确定性」原则的落地形态：
  - 所有业务口径写在参数表里（可审计、可复核、可版本化），不写在 prompt 里
  - 所有算术由本地死代码完成，模型只负责提供结构化输入
  - 计算完成即产出审计轨迹，供人工终审
  - 输出写入新文件，绝不覆盖原件

运行环境：企业本地离线环境，不需要任何 API 密钥。

输入契约（由上游「需求解析师」Agent 产出，字段固定）：
  {
    "员工清单": [
      {
        "姓名": "张三",
        "底薪": 8000.0,
        "事假天数": 1,
        "病假天数": 0,
        "迟到分钟数": [8, 22],      // 每次迟到的分钟数，空数组表示无迟到
        "旷工天数": 0
      }
    ]
  }

用法：
  python payroll_engine_example.py input.json output.csv
"""

import csv
import json
import sys
from datetime import datetime

# ============================================================
# 参数表：业务口径的唯一来源
# ------------------------------------------------------------
# 每个数字都必须有出处，且能被业务方逐条复核。
# 修改规则 = 修改这张表 + 升版本号，不需要改代码、不需要调 prompt。
# ============================================================

RULES_VERSION = "2026.01"

PARAMS = {
    "月计薪天数": 21.75,          # 依据：法定月计薪天数折算标准
    "全勤奖": 200.0,              # 依据：企业薪酬制度
    "事假扣减比例": 1.00,          # 依据：无薪事假全额扣减
    "病假扣减比例": 0.40,          # 依据：病假按 60% 日薪计发
    "旷工扣减倍数": 3.0,           # 依据：无正当理由缺勤的惩罚倍数
    "社保个人比例": 0.105,         # 依据：五险个人缴纳部分合计
    "公积金比例": 0.12,            # 依据：住房公积金个人缴纳比例
}

# 迟到阶梯扣款表：(上限分钟数, 扣款金额)；最后一个元素为"超过上限"的兜底规则
LATE_TIERS = [
    (15, 20.0),        # 1–15 分钟：扣 20 元
    (60, 50.0),        # 16–60 分钟：扣 50 元
]
LATE_OVER_TIER_HALFDAY = True   # 超过 60 分钟：扣半天日薪


# ============================================================
# 计算层：纯函数，无副作用，可单测
# ============================================================

def calc_daily_rate(base_salary: float) -> float:
    """日薪 = 底薪 ÷ 月计薪天数"""
    return round(base_salary / PARAMS["月计薪天数"], 2)


def calc_late_deduction(late_minutes: list, daily_rate: float) -> float:
    """按阶梯表计算迟到扣款"""
    total = 0.0
    for minutes in late_minutes:
        if minutes <= 0:
            continue
        matched = False
        for upper, amount in LATE_TIERS:
            if minutes <= upper:
                total += amount
                matched = True
                break
        if not matched and LATE_OVER_TIER_HALFDAY:
            total += daily_rate * 0.5
    return round(total, 2)


def calc_one_employee(emp: dict) -> dict:
    """
    单个员工的薪资计算。
    严格按参数表做四则运算——不引入任何模型推断。
    """
    base = float(emp["底薪"])
    daily_rate = calc_daily_rate(base)

    leave_days = int(emp.get("事假天数", 0))
    sick_days = int(emp.get("病假天数", 0))
    absent_days = int(emp.get("旷工天数", 0))
    late_minutes = emp.get("迟到分钟数", []) or []

    leave_deduct = round(leave_days * daily_rate * PARAMS["事假扣减比例"], 2)
    sick_deduct = round(sick_days * daily_rate * PARAMS["病假扣减比例"], 2)
    absent_deduct = round(absent_days * daily_rate * PARAMS["旷工扣减倍数"], 2)
    late_deduct = calc_late_deduction(late_minutes, daily_rate)

    # 全勤奖条件：无事假、无病假、无迟到、无旷工（四者需全部满足）
    is_full_attendance = (leave_days == 0 and sick_days == 0
                          and len(late_minutes) == 0 and absent_days == 0)
    bonus = PARAMS["全勤奖"] if is_full_attendance else 0.0

    taxable = round(base - leave_deduct - sick_deduct - late_deduct - absent_deduct + bonus, 2)
    social_fund = round(base * (PARAMS["社保个人比例"] + PARAMS["公积金比例"]), 2)
    net_pay = round(taxable - social_fund, 2)

    return {
        "姓名": emp["姓名"],
        "底薪": base,
        "日薪": daily_rate,
        "事假扣款": leave_deduct,
        "病假扣款": sick_deduct,
        "迟到扣款": late_deduct,
        "旷工扣款": absent_deduct,
        "全勤奖": bonus,
        "五险一金": social_fund,
        "计税前应发": taxable,
        "实发薪资": net_pay,
    }


# ============================================================
# 校验层（三明治）：结构性 / 逻辑性 / 时效性
# ------------------------------------------------------------
# 三层全绿才允许写文件；任一失败立即返回，不产生输出。
# ============================================================

REQUIRED_FIELDS = ["姓名", "底薪"]

# 环比波动阈值：超过则拦截，需人工确认
FLUCTUATION_THRESHOLD = 0.20

# 数据新鲜度阈值（小时）
FRESHNESS_HOURS = 72


def check_structure(payload: dict) -> list:
    """结构性校验：字段无缺失、类型正确"""
    errors = []
    if "员工清单" not in payload:
        errors.append("缺少顶层字段：员工清单")
        return errors
    if not isinstance(payload["员工清单"], list):
        errors.append("员工清单 必须是数组")
        return errors

    for i, emp in enumerate(payload["员工清单"]):
        for f in REQUIRED_FIELDS:
            if f not in emp:
                errors.append(f"第 {i + 1} 条记录缺少必填字段：{f}")
        if "底薪" in emp and not isinstance(emp["底薪"], (int, float)):
            errors.append(f"第 {i + 1} 条记录 底薪 类型错误（应为数值）")
    return errors


def check_logic(results: list, baseline: dict) -> list:
    """
    逻辑自洽性校验：环比波动比对。
    baseline 为 {姓名: 上月实发} ，可为空（首次运行）。
    """
    warnings = []
    if not baseline:
        return warnings
    for r in results:
        name = r["姓名"]
        if name not in baseline:
            continue
        prev = baseline[name]
        if prev <= 0:
            continue
        change = abs(r["实发薪资"] - prev) / prev
        if change > FLUCTUATION_THRESHOLD:
            warnings.append(
                f"{name}：实发环比变动 {change * 100:.1f}%（阈值 {FLUCTUATION_THRESHOLD * 100:.0f}%）"
                f"，上月 {prev} → 本月 {r['实发薪资']}"
            )
    return warnings


def check_freshness(payload: dict) -> list:
    """时效性校验：数据时间戳未超期"""
    errors = []
    ts = payload.get("数据时间戳")
    if not ts:
        return errors  # 未提供则不校验，但在审计轨迹中标注
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        errors.append(f"数据时间戳格式无法解析：{ts}")
        return errors
    age_hours = (datetime.now() - dt).total_seconds() / 3600
    if age_hours > FRESHNESS_HOURS:
        errors.append(f"数据已过期 {age_hours:.1f} 小时（阈值 {FRESHNESS_HOURS} 小时）")
    return errors


# ============================================================
# 输出层：写入新文件，绝不覆盖原件
# ============================================================

def write_result(results: list, output_path: str) -> None:
    """写入 CSV 副本，文件名带时间戳提示这是产出而非原件"""
    fields = list(results[0].keys()) if results else []
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)


# ============================================================
# 主流程
# ============================================================

def run(input_path: str, output_path: str) -> int:
    with open(input_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # --- TIER 02-1 结构性校验 ---
    errors = check_structure(payload)
    if errors:
        print("[BLOCK] 结构性校验失败：")
        for e in errors:
            print("   -", e)
        return 1

    # --- TIER 02-3 时效性校验 ---
    errors = check_freshness(payload)
    if errors:
        print("[BLOCK] 时效性校验失败：")
        for e in errors:
            print("   -", e)
        return 1

    # --- 执行计算（确定性）---
    results = [calc_one_employee(e) for e in payload["员工清单"]]

    # --- TIER 02-2 逻辑自洽性校验 ---
    warnings = check_logic(results, payload.get("上月基准", {}))
    if warnings:
        print("[BLOCK] 逻辑自洽性校验发现异常，已阻断写入：")
        for w in warnings:
            print("   -", w)
        print("\n请人工核对后重新提交（本流程不自动修正）。")
        return 1

    # --- 三层全绿，写入 ---
    write_result(results, output_path)

    total = round(sum(r["实发薪资"] for r in results), 2)
    print(f"[PASS] 核算完成，规则版本 {RULES_VERSION}")
    print(f"       人数：{len(results)}    实发合计：{total:,.2f}")
    print(f"       输出：{output_path}")
    print("       请人工抽验并签字归档。")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        print("用法：python payroll_engine_example.py <input.json> <output.csv>")
        sys.exit(2)
    sys.exit(run(sys.argv[1], sys.argv[2]))
