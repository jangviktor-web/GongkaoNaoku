#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SM-2 间隔重复引擎 (Anki 风格)。四档评分：忘记(1)/困难(3)/一般(4)/熟悉(5)"""
import datetime

def add_days(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

def grade_state(state, grade):
    """state: dict(ease,interval,repetitions,lapses,status). grade in {1,3,4,5}.
    返回新的 dict 含 due。interval 单位=天。"""
    ease = float(state.get("ease") or 2.5)
    interval = int(state.get("interval") or 0)
    reps = int(state.get("repetitions") or 0)
    lapses = int(state.get("lapses") or 0)
    status = state.get("status") or "new"
    grade = int(grade)

    if grade < 3:  # 忘记 / relearn
        reps = 0
        interval = 0            # 今天再练
        if status == "review":
            lapses += 1
        status = "learning"
    else:
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            factor = 1.2 if grade == 3 else (1.0 if grade == 4 else 1.3)
            interval = max(1, round(interval * ease * factor))
        # SM-2 ease 更新
        ease = ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
        if ease < 1.3:
            ease = 1.3
        status = "review" if reps >= 2 else "learning"

    due = add_days(interval)
    return dict(ease=round(ease, 3), interval=interval, repetitions=reps,
                lapses=lapses, status=status, due=due)

# 掌握度分桶（用于总览统计），基于 interval
def mastery_bucket(interval):
    if interval <= 0:
        return "学习中"
    if interval < 7:
        return "初记"
    if interval < 21:
        return "短期"
    if interval < 60:
        return "长期"
    return "稳固"
