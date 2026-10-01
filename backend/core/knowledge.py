"""知识点目录：给题目打标签时只能从这里选，不让模型自己造词。

目录是数据目录里的一个文本文件 knowledge-points.txt，第一次用时写入默认目录
（人教A版高中数学 2019 版各节的名称）。使用者可以直接改这个文件：
以 # 开头的行是章名（只用来分组），其余每行一个知识点。
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

FILE_NAME = "knowledge-points.txt"

DEFAULT_TEXT = """\
# 题有据的知识点目录：给题目打知识点标签时，模型只能从下面选。
# 以 # 开头的行是章名，只用来分组；其余每行一个知识点。可以随意增删改，保存后下一次打标签就按新目录。
# 默认目录：人教A版高中数学（2019）各节。

# 必修第一册 · 第一章 集合与常用逻辑用语
集合的概念
集合间的基本关系
集合的基本运算
充分条件与必要条件
全称量词与存在量词

# 必修第一册 · 第二章 一元二次函数、方程和不等式
等式性质与不等式性质
基本不等式
二次函数与一元二次方程、不等式

# 必修第一册 · 第三章 函数的概念与性质
函数的概念及其表示
函数的基本性质
幂函数
函数的应用（一）

# 必修第一册 · 第四章 指数函数与对数函数
指数
指数函数
对数
对数函数
函数的应用（二）

# 必修第一册 · 第五章 三角函数
任意角和弧度制
三角函数的概念
诱导公式
三角函数的图象与性质
三角恒等变换
函数 y=Asin(ωx+φ)
三角函数的应用

# 必修第二册 · 第六章 平面向量及其应用
平面向量的概念
平面向量的运算
平面向量基本定理及坐标表示
平面向量的应用

# 必修第二册 · 第七章 复数
复数的概念
复数的四则运算
复数的三角表示

# 必修第二册 · 第八章 立体几何初步
基本立体图形
立体图形的直观图
简单几何体的表面积与体积
空间点、直线、平面之间的位置关系
空间直线、平面的平行
空间直线、平面的垂直

# 必修第二册 · 第九章 统计
随机抽样
用样本估计总体
统计分析案例

# 必修第二册 · 第十章 概率
随机事件与概率
事件的相互独立性
频率与概率

# 选择性必修第一册 · 第一章 空间向量与立体几何
空间向量及其运算
空间向量基本定理
空间向量及其运算的坐标表示
空间向量的应用

# 选择性必修第一册 · 第二章 直线和圆的方程
直线的倾斜角与斜率
直线的方程
直线的交点坐标与距离公式
圆的方程
直线与圆、圆与圆的位置关系

# 选择性必修第一册 · 第三章 圆锥曲线的方程
椭圆
双曲线
抛物线

# 选择性必修第二册 · 第四章 数列
数列的概念
等差数列
等比数列
数学归纳法

# 选择性必修第二册 · 第五章 一元函数的导数及其应用
导数的概念及其意义
导数的运算
导数在研究函数中的应用

# 选择性必修第三册 · 第六章 计数原理
分类加法计数原理与分步乘法计数原理
排列与组合
二项式定理

# 选择性必修第三册 · 第七章 随机变量及其分布
条件概率与全概率公式
离散型随机变量及其分布列
离散型随机变量的数字特征
二项分布与超几何分布
正态分布

# 选择性必修第三册 · 第八章 成对数据的统计分析
成对数据的统计相关性
一元线性回归模型及其应用
列联表与独立性检验
"""

MAX_TAGS = 3
_NONE = re.compile(r"^\s*(?:无|没有|none)\s*[。.]?\s*$", re.I)


def path() -> Path:
    return (Path(settings.DATA_ROOT) / FILE_NAME).resolve()


def ensure_file() -> Path:
    target = path()
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DEFAULT_TEXT, encoding="utf-8")
    return target


def parse_catalogue(text: str) -> list[dict]:
    """[{point, chapter}] in file order; duplicates and blank lines are skipped."""
    chapter = ""
    seen: set[str] = set()
    points: list[dict] = []
    for line in str(text or "").splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("#"):
            heading = value.lstrip("#").strip()
            # The file's own explanation lines are not chapters.
            if "·" in heading or "第" in heading:
                chapter = heading
            continue
        value = value[:60]
        if value in seen:
            continue
        seen.add(value)
        points.append({"point": value, "chapter": chapter})
    return points


def load() -> list[dict]:
    try:
        return parse_catalogue(ensure_file().read_text(encoding="utf-8-sig"))
    except OSError:
        return parse_catalogue(DEFAULT_TEXT)


def _key(value: str) -> str:
    return re.sub(r"[\s（）()·]", "", str(value or "")).lower()


def match_tags(raw: str, points: list[dict]) -> list[str]:
    """The catalogue points a model's answer names, in the order it names them, at most three.

    Points are looked up inside the answer rather than split out of it: some
    names contain 、 or ，（“二次函数与一元二次方程、不等式”）.  Longer names win, so
    “指数函数” is not also counted as “指数”.
    """
    if _NONE.match(str(raw or "")):
        return []
    text = _key(raw)
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, str]] = []
    for item in sorted(points, key=lambda entry: -len(_key(entry["point"]))):
        key = _key(item["point"])
        if not key:
            continue
        start = text.find(key)
        while start >= 0:
            end = start + len(key)
            if not any(start < other_end and other_start < end for other_start, other_end in taken):
                taken.append((start, end))
                found.append((start, item["point"]))
                break
            start = text.find(key, start + 1)
    chosen: list[str] = []
    for _position, point in sorted(found):
        if point not in chosen:
            chosen.append(point)
    return chosen[:MAX_TAGS]
