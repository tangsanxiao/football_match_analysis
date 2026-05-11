#!/usr/bin/env python3
"""Generate a coach-facing scenario+mock-data preview PDF.

Audience: a B-level youth coach who has already seen the current report
and now wants to see where this could go — a near-future evolution of the
system, illustrated with mock data, mapped onto his real coaching scenarios.

NOT a feature pitch. NOT a sales deck. Every page repeats the disclaimer
that the inner data is mock so the coach knows what is real and what isn't.

Output: reports/coach_preview/coach_preview_<YYYYMMDD>.pdf

Usage:
    .venv/bin/python scripts/generate_coach_preview_pdf.py
    .venv/bin/python scripts/generate_coach_preview_pdf.py --out my.pdf
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


# === Style tokens (aligned with project design.md) ===
CHINESE_FONT = 'Hiragino Sans GB'
ACCENT = '#0f7b63'
ACCENT_SOFT = '#eaf5f1'
INK = '#1b241f'
MUTED = '#66726c'
LINE = '#d8ded8'
WARNING = '#9b5a00'
DANGER = '#982b16'
INFO = '#2f5f9f'
PANEL_BG = '#fafbf8'

plt.rcParams['font.family'] = CHINESE_FONT
plt.rcParams['axes.unicode_minus'] = False

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PAGE_SIZE = (8.5, 11)  # US letter portrait


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def new_page():
    fig = plt.figure(figsize=PAGE_SIZE)
    fig.patch.set_facecolor('white')
    return fig


def page_header(fig, title, subtitle=''):
    fig.text(0.06, 0.94, title, fontsize=18, color=ACCENT, weight='bold')
    if subtitle:
        fig.text(0.06, 0.910, subtitle, fontsize=10, color=MUTED)
    fig.add_artist(plt.Line2D([0.06, 0.94], [0.895, 0.895],
                              color=LINE, linewidth=0.8))


def page_footer(fig, page_no, total):
    fig.text(0.06, 0.04, '演示数据 · 非真实球员',
             fontsize=8, color=MUTED, alpha=0.8)
    fig.text(0.94, 0.04, f'{page_no} / {total}',
             ha='right', fontsize=8, color=MUTED)


def callout(fig, x, y, w, h, *, kind='neutral'):
    colors = {
        'neutral': (PANEL_BG, LINE),
        'warning': ('#fff8e6', WARNING),
        'feature': (ACCENT_SOFT, ACCENT),
        'danger':  ('#fde6e2', DANGER),
    }
    bg, border = colors.get(kind, colors['neutral'])
    rect = mpatches.FancyBboxPatch(
        (x, y), w, h, boxstyle='round,pad=0.005',
        linewidth=1.2, edgecolor=border, facecolor=bg,
        transform=fig.transFigure,
    )
    fig.add_artist(rect)


def big_number_box(fig, x, y, w, h, *, value, label, delta='', delta_color=None):
    rect = mpatches.FancyBboxPatch(
        (x, y), w, h, boxstyle='round,pad=0.004',
        linewidth=1.2, edgecolor=LINE, facecolor='white',
        transform=fig.transFigure,
    )
    fig.add_artist(rect)
    fig.text(x + w / 2, y + h * 0.66, value, ha='center', va='center',
             fontsize=20, color=ACCENT, weight='bold')
    fig.text(x + w / 2, y + h * 0.34, label, ha='center', va='center',
             fontsize=9, color=MUTED)
    if delta:
        fig.text(x + w / 2, y + h * 0.14, delta, ha='center', va='center',
                 fontsize=7.5, color=delta_color or INFO, weight='bold')


# ---------------------------------------------------------------------------
# Page 1 — Cover
# ---------------------------------------------------------------------------

def page_cover(pdf, page, total):
    fig = new_page()
    fig.text(0.5, 0.74, '足球青训分析系统', ha='center',
             fontsize=32, color=ACCENT, weight='bold')
    fig.text(0.5, 0.69, '场景与价值预览', ha='center',
             fontsize=20, color=INK)
    fig.text(0.5, 0.62,
             '—— 基于青训工作流,结合 AI 系统放大教学效果 ——',
             ha='center', fontsize=11, color=MUTED, style='italic')

    callout(fig, 0.15, 0.38, 0.70, 0.16, kind='warning')
    fig.text(0.5, 0.515, '● 关于本预览', ha='center',
             fontsize=12, color=WARNING, weight='bold')
    notes = [
        '• 文中所有数字、姓名、图表均为演示 mock 数据,非真实球员',
        '• 预览中"完整球员档案、训练前后对比、长期发展轨迹"',
        '  对应 1-3 个月内可演进出的能力(末页有诚实清单)',
        '• 目的:呈现"工具与教练教学理念结合后"的可能形态',
    ]
    for i, t in enumerate(notes):
        fig.text(0.18, 0.50 - i * 0.024, t, fontsize=10, color=INK)

    fig.text(0.5, 0.20,
             datetime.now().strftime('生成日期:%Y 年 %m 月 %d 日'),
             ha='center', fontsize=11, color=MUTED)
    fig.text(0.5, 0.17, 'Version 1.0 · Coach Preview',
             ha='center', fontsize=9, color=MUTED)

    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 2 — Positioning
# ---------------------------------------------------------------------------

def page_positioning(pdf, page, total):
    fig = new_page()
    page_header(fig, '这个工具是什么、不是什么',
                '在讨论场景之前,先把边界说清楚')

    fig.text(0.06, 0.84, '● 是', fontsize=14, color=ACCENT, weight='bold')
    is_items = [
        '• 一个本地化运行的视频分析工具,数据存在教练自己的设备上',
        '• 把场上发生的事情精确描述出来:谁、什么时间、在哪、做了什么',
        '• 为教练提供"客观证据",让训练效果、球员发展可被记录和回看',
        '• 教练评级答辩 / 公开课 / 比武 / 家长沟通的辅助材料生成器',
    ]
    for i, t in enumerate(is_items):
        fig.text(0.08, 0.81 - i * 0.028, t, fontsize=10.5, color=INK)

    fig.text(0.06, 0.65, '● 不是', fontsize=14, color=DANGER, weight='bold')
    is_not = [
        '• 不评判球员好坏,不替教练做选材决定',
        '• 不取代教练的经验、判断和对孩子的了解',
        '• 不是黑箱 AI 教练,所有指标的定义都公开、可解释',
        '• 不直接产出"训练计划";教练才是教练,我们只提供素材',
    ]
    for i, t in enumerate(is_not):
        fig.text(0.08, 0.62 - i * 0.028, t, fontsize=10.5, color=INK)

    callout(fig, 0.06, 0.32, 0.88, 0.14, kind='feature')
    fig.text(0.5, 0.44, '一句话总结', ha='center',
             fontsize=13, color=ACCENT, weight='bold')
    fig.text(0.5, 0.405,
             '教练原本凭感觉、记忆、笔记完成的部分,系统替教练变成可记录、',
             ha='center', fontsize=11, color=INK)
    fig.text(0.5, 0.382,
             '可查阅、可作为评级/公开课/比武答辩附件的客观数据。',
             ha='center', fontsize=11, color=INK)
    fig.text(0.5, 0.348,
             '数据怎么解读、怎么用、用到什么程度,完全由教练决定。',
             ha='center', fontsize=10, color=MUTED, style='italic')

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 3 — Coaching philosophy → quantifiable indicators (the amplification frame)
# ---------------------------------------------------------------------------

def page_philosophy(pdf, page, total):
    fig = new_page()
    page_header(fig, '教学理念的可量化与验证',
                '把"教练心里有的东西"变成"球员场上看得见的事实"')

    # === Closed-loop diagram at top ===
    ax = fig.add_axes([0.06, 0.69, 0.88, 0.18])
    ax.axis('off')

    nodes = [
        (0.08, 0.5, '教学理念\n(教练带来)'),
        (0.30, 0.5, '可量化目标\n(共同定义)'),
        (0.52, 0.5, '比赛 / 训练\n数据(系统采集)'),
        (0.74, 0.5, '教学反思\n(教练主导)'),
        (0.92, 0.5, '下一步\n训练调整'),
    ]
    node_colors = [ACCENT, INFO, MUTED, ACCENT, WARNING]
    for (x, y, label), color in zip(nodes, node_colors):
        circle = mpatches.Circle((x, y), 0.075, color=color, alpha=0.85)
        ax.add_artist(circle)
        ax.text(x, y, label, ha='center', va='center',
                fontsize=8.5, color='white', weight='bold')
    # Arrows
    for i in range(len(nodes) - 1):
        x1 = nodes[i][0] + 0.08
        x2 = nodes[i + 1][0] - 0.08
        ax.annotate('', xy=(x2, 0.5), xytext=(x1, 0.5),
                    arrowprops=dict(arrowstyle='->', color=INK, lw=1.5))
    # Return arrow from last to first
    ax.annotate('', xy=(0.08, 0.25), xytext=(0.92, 0.25),
                arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.2,
                                connectionstyle='arc3,rad=0.0'))
    ax.text(0.5, 0.10, '理念在数据反馈中迭代,数据在教练判断中升华',
            ha='center', fontsize=9, color=MUTED, style='italic')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # === Philosophy → metric mapping table ===
    fig.text(0.06, 0.65, '常见教学主张如何被系统"看见"',
             fontsize=12, color=INK, weight='bold')

    ax2 = fig.add_axes([0.06, 0.33, 0.88, 0.28])
    ax2.axis('off')

    headers = ['教练主张', '系统可观察的指标', '可推动的具体改变']
    rows = [
        ['传控为主 / 整体进攻',
         '平均位置纵深、攻击三区时间、\n传球候选数 / 链路',
         '球员上场时纵深站位向前压 2-3 米'],
        ['高位压迫 / 主动反抢',
         '前场压迫占比%、对抗距离%、\n高速跑次数(进攻半场)',
         '丢球后 5 秒内的就近压迫率 +30%'],
        ['整体防守 / 紧凑阵型',
         '站位纪律%、防守纪律、\n防守阶段球员间距',
         '失球后 10 秒回防到位率 +25%'],
        ['因材施教 / 多套体系',
         '每个球员的角色区域占比、\n跨场轨迹差异',
         '不同孩子分别在他强项上的曲线持续上扬'],
    ]
    col_x = [0.0, 0.27, 0.62]
    col_w = [0.27, 0.35, 0.38]

    # Header
    header_y = 0.94
    for i, h in enumerate(headers):
        ax2.text(col_x[i] + 0.005, header_y, h, ha='left', va='top',
                 fontsize=10, color=ACCENT, weight='bold')
    ax2.axhline(0.91, color=ACCENT, linewidth=1.2)

    row_h = 0.21
    row_y = 0.91
    for r_idx, row in enumerate(rows):
        bg = 'white' if r_idx % 2 == 0 else PANEL_BG
        ax2.axhspan(row_y - row_h, row_y, color=bg, alpha=0.5)
        for c_idx, cell in enumerate(row):
            ax2.text(col_x[c_idx] + 0.005, row_y - 0.015, cell,
                     ha='left', va='top', fontsize=9, color=INK)
        row_y -= row_h
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)

    # === Bottom: data-driven reflection example ===
    callout(fig, 0.06, 0.17, 0.88, 0.14, kind='feature')
    fig.text(0.08, 0.292, '数据驱动的教学反思 · 范例',
             fontsize=11, color=ACCENT, weight='bold')
    fig.text(0.08, 0.265,
             '本周设定目标"提升前场反抢";5 名球员中,3 人压迫占比明显上升(+5 个百分点),',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.246,
             '2 人(防守倾向较重)上升幅度小。说明理念已被进攻球员理解,但需要',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.227,
             '为偏防守的孩子单独设计"前场反抢替补"角色才能落到全队。',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.205,
             '下周调整:把"丢球后第一时间反抢"作为核心动作,在小场训练里反复演练。',
             fontsize=9.5, color=INK, weight='bold')
    fig.text(0.08, 0.185,
             '— 这种反思在评级答辩 / 公开课说课环节里,是核心加分项',
             fontsize=8.5, color=MUTED, style='italic')

    # Bottom limit reminder
    callout(fig, 0.06, 0.07, 0.88, 0.07, kind='warning')
    fig.text(0.08, 0.124, '系统的角色边界', fontsize=10, color=WARNING, weight='bold')
    fig.text(0.08, 0.099,
             '• 系统不替教练定义"什么是好的足球",只把教练已有理念变成可观察的数据',
             fontsize=9, color=INK)
    fig.text(0.08, 0.080,
             '• 教学反思的语言、判断、下一步训练设计 —— 全部由教练完成',
             fontsize=9, color=INK)

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 4 — Scenario 1: Training effect proof
# ---------------------------------------------------------------------------

def page_scenario_training_effect(pdf, page, total):
    fig = new_page()
    page_header(fig, '场景 1 · 训练效果证明',
                '「我设计了一节压迫训练,4 周后场上发生了什么?」')

    fig.text(0.06, 0.86, '教练日常工作', fontsize=12, color=INK, weight='bold')
    work = [
        '• 教练设计训练课时已经有清晰的目标(本例:提升前场压迫强度)',
        '• 教练已在比赛中观察孩子们是否学到了——靠经验和直觉',
        '• 评级答辩、公开课需要"训练前后对比"的证据,但拿不出来',
    ]
    for i, t in enumerate(work):
        fig.text(0.08, 0.835 - i * 0.022, t, fontsize=10, color=INK)

    fig.text(0.06, 0.755, '系统替教练做的', fontsize=12, color=ACCENT, weight='bold')
    fig.text(0.08, 0.730,
             '自动采集训练前后两场比赛的对比数据,输出可作为答辩材料的指标表',
             fontsize=10, color=INK)

    ax = fig.add_axes([0.10, 0.32, 0.80, 0.36])
    metrics = ['前场压迫\n占比%', '对抗距离\n占比%', '进攻三区\n时间%', '高速跑\n距离(m)']
    before = [8.2, 22.1, 35.4, 285]
    after = [13.5, 31.4, 42.8, 348]

    x = np.arange(len(metrics))
    width = 0.36
    ax.bar(x - width / 2, before, width, label='训练前一场比赛',
           color=MUTED, edgecolor='white')
    ax.bar(x + width / 2, after, width, label='训练后一场比赛',
           color=ACCENT, edgecolor='white')
    ax.set_xticks(x)
    ax.set_xticklabels(metrics, fontsize=10)
    ax.set_title('4 周「前场压迫」专项训练 · 红队整体指标对比',
                 fontsize=11, color=INK, pad=12)
    ax.legend(loc='upper left', fontsize=9, frameon=False)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.tick_params(axis='y', labelsize=9)
    ax.grid(axis='y', linestyle=':', color=LINE, alpha=0.7)

    for i, (b, a) in enumerate(zip(before, after)):
        delta_pct = (a - b) / b * 100
        sign = '+' if delta_pct >= 0 else ''
        ax.text(i, max(b, a) * 1.06, f'{sign}{delta_pct:.0f}%',
                ha='center', fontsize=9, color=ACCENT, weight='bold')

    callout(fig, 0.06, 0.16, 0.88, 0.10, kind='feature')
    fig.text(0.08, 0.232, '对对教练的具体价值', fontsize=10.5,
             color=ACCENT, weight='bold')
    fig.text(0.08, 0.207,
             '• 公开课答辩时,这张图就是"训练目标 → 训练效果"的硬证据',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.187,
             '• 评级时常被问"教练怎么知道这套训练有效?" —— 这张图回答',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.167,
             '• 比武时区别于"凭感觉"教练 —— 数据驱动的训练设计',
             fontsize=9.5, color=INK)

    callout(fig, 0.06, 0.07, 0.88, 0.07, kind='warning')
    fig.text(0.08, 0.124, '诚实的限制', fontsize=10, color=WARNING, weight='bold')
    fig.text(0.08, 0.099,
             '• 比赛环境(机位、天气、对手)有自然波动,变化幅度 <15% 时要谨慎',
             fontsize=9, color=INK)
    fig.text(0.08, 0.081,
             '• 建议每个训练周期跑 2-3 场比赛取平均值,减少单场波动影响',
             fontsize=9, color=INK)

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 4 — Scenario 2: Player longitudinal development
# ---------------------------------------------------------------------------

def page_scenario_player_trend(pdf, page, total):
    fig = new_page()
    page_header(fig, '场景 2 · 球员个体的长期发展曲线',
                '「这孩子跟我练了 5 个月,到底涨没涨?涨在哪?」')

    fig.text(0.06, 0.86, '教练日常工作', fontsize=12, color=INK, weight='bold')
    work = [
        '• 教练对每个孩子有不同的培养目标(因材施教是青训的核心)',
        '• 现在判断"是否进步"主要靠几个月的整体印象 + 比赛胜负',
        '• 家长追问"我家孩子比上学期强了吗?"时,只能给主观回答',
    ]
    for i, t in enumerate(work):
        fig.text(0.08, 0.835 - i * 0.022, t, fontsize=10, color=INK)

    fig.text(0.06, 0.755, '系统替教练做的', fontsize=12,
             color=ACCENT, weight='bold')
    fig.text(0.08, 0.730,
             '每场比赛自动归档到这名球员的档案;5 个月后自然产生 N 场数据轨迹',
             fontsize=10, color=INK)

    ax = fig.add_axes([0.10, 0.34, 0.80, 0.36])
    months = ['第 1 月', '第 2 月', '第 3 月', '第 4 月', '第 5 月']
    distance = [2.4, 2.7, 2.9, 3.1, 3.3]
    hs_distance = [180, 220, 265, 305, 340]
    pressing_pct = [4.2, 6.8, 9.5, 11.2, 13.4]

    ax.plot(months, distance, marker='o', color=ACCENT, linewidth=2,
            markersize=8, label='跑动距离 (km)')
    ax.set_ylabel('跑动距离 (km)', color=ACCENT, fontsize=10)
    ax.set_ylim(0, max(distance) * 1.3)
    ax.tick_params(axis='y', labelcolor=ACCENT, labelsize=9)
    ax.tick_params(axis='x', labelsize=10)
    ax.spines['top'].set_visible(False)
    ax.grid(axis='y', linestyle=':', color=LINE, alpha=0.6)

    ax2 = ax.twinx()
    ax2.plot(months, hs_distance, marker='s', color=INFO, linewidth=2,
             markersize=8, label='高速跑距离 (m)')
    ax2.set_ylabel('高速跑距离 (m)', color=INFO, fontsize=10)
    ax2.set_ylim(0, max(hs_distance) * 1.3)
    ax2.tick_params(axis='y', labelcolor=INFO, labelsize=9)
    ax2.spines['top'].set_visible(False)

    ax3 = ax.twinx()
    ax3.spines['right'].set_position(('axes', 1.10))
    ax3.plot(months, pressing_pct, marker='^', color=WARNING, linewidth=2,
             markersize=8, label='压迫意识 (%)')
    ax3.set_ylabel('压迫意识 (%)', color=WARNING, fontsize=10)
    ax3.set_ylim(0, max(pressing_pct) * 1.6)
    ax3.tick_params(axis='y', labelcolor=WARNING, labelsize=9)
    ax3.spines['top'].set_visible(False)

    ax.set_title('李梓萌(U13) · 5 个月发展轨迹',
                 fontsize=11, color=INK, pad=10)
    lines = ax.get_lines() + ax2.get_lines() + ax3.get_lines()
    labels = [l.get_label() for l in lines]
    ax.legend(lines, labels, loc='upper left', fontsize=9, frameon=False)

    callout(fig, 0.06, 0.18, 0.88, 0.12, kind='feature')
    fig.text(0.08, 0.275, '对对教练的具体价值', fontsize=10.5,
             color=ACCENT, weight='bold')
    fig.text(0.08, 0.250,
             '• 家长会:从"孩子还行"到"5 个月跑动从 2.4→3.3 km,压迫意识翻 3 倍"',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.230,
             '• 评级答辩:"我带过的球员真的进步了" —— 数据可视化背书',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.210,
             '• 选材推荐:孩子升初中、进体校时,有完整发展档案做简历',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.190,
             '• 因材施教:看到哪个孩子卡在哪个维度,有针对性的下一步',
             fontsize=9.5, color=INK)

    callout(fig, 0.06, 0.08, 0.88, 0.08, kind='warning')
    fig.text(0.08, 0.143, '需要的前置条件', fontsize=10,
             color=WARNING, weight='bold')
    fig.text(0.08, 0.117,
             '• 每场比赛走完一次分析流水线(约 30-40 分钟人工 + 系统时间)',
             fontsize=9, color=INK)
    fig.text(0.08, 0.098,
             '• 球员身份要稳定(球衣号码 + 姓名);1-3 个月内可加自动绑定',
             fontsize=9, color=INK)

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 5 — Scenario 3: Match review clip selection
# ---------------------------------------------------------------------------

def page_scenario_review(pdf, page, total):
    fig = new_page()
    page_header(fig, '场景 3 · 比赛复盘的关键时刻定位',
                '「30 分钟的视频,我要讲哪 5 个瞬间?」')

    fig.text(0.06, 0.86, '教练日常工作', fontsize=12, color=INK, weight='bold')
    work = [
        '• 复盘会要选出有代表性、能讲清楚战术意图的瞬间',
        '• 现在的做法:凭记忆 + 视频拖动来回找,每场 30-60 分钟',
    ]
    for i, t in enumerate(work):
        fig.text(0.08, 0.835 - i * 0.022, t, fontsize=10, color=INK)

    fig.text(0.06, 0.770, '系统替教练做的', fontsize=12,
             color=ACCENT, weight='bold')
    fig.text(0.08, 0.745,
             '自动标出 20-30 个候选瞬间(类型 + 球员 + 时间),教练只需快速勾选',
             fontsize=10, color=INK)

    ax = fig.add_axes([0.08, 0.40, 0.84, 0.28])
    events = [
        (1.4, '高速跑', '李梓萌', INFO),
        (3.2, '压迫候选', '王浩然', ACCENT),
        (5.8, '进攻三区跑动', '李梓萌', WARNING),
        (8.1, '传球候选', '张子涵', MUTED),
        (12.5, '压迫候选', '陈思源', ACCENT),
        (15.3, '射门候选', '李梓萌', DANGER),
        (17.2, '高速跑', '王浩然', INFO),
        (21.0, '压迫候选', '张子涵', ACCENT),
        (23.8, '1v1 攻防', '陈思源', '#7e3ab5'),
        (27.5, '进攻三区跑动', '李梓萌', WARNING),
    ]
    for t, kind, player, color in events:
        ax.scatter([t], [0.5], s=160, color=color,
                   edgecolor='white', linewidth=1.5, zorder=3)
        ax.text(t, 0.85, kind, ha='center', fontsize=8,
                color=color, rotation=20, weight='bold')
        ax.text(t, 0.20, player, ha='center', fontsize=7.5, color=MUTED)
    ax.set_xlim(0, 30)
    ax.set_ylim(0, 1)
    ax.set_xticks([0, 5, 10, 15, 20, 25, 30])
    ax.set_xticklabels([f"{i}'" for i in [0, 5, 10, 15, 20, 25, 30]],
                       fontsize=9)
    ax.set_yticks([])
    ax.set_title('上半场关键候选时刻时间线',
                 fontsize=11, color=INK, pad=12)
    for spine in ['left', 'right', 'top']:
        ax.spines[spine].set_visible(False)
    ax.grid(axis='x', linestyle=':', color=LINE, alpha=0.5)

    kind_colors = [('高速跑', INFO), ('压迫候选', ACCENT),
                   ('进攻三区跑动', WARNING), ('射门候选', DANGER),
                   ('传球候选', MUTED), ('1v1 攻防', '#7e3ab5')]
    for i, (k, c) in enumerate(kind_colors):
        x_pos = 0.10 + (i % 3) * 0.28
        y_pos = 0.34 - (i // 3) * 0.025
        fig.add_artist(plt.Circle((x_pos, y_pos), 0.007,
                                  color=c, transform=fig.transFigure))
        fig.text(x_pos + 0.014, y_pos - 0.003, k, fontsize=9, color=INK)

    callout(fig, 0.06, 0.16, 0.88, 0.12, kind='feature')
    fig.text(0.08, 0.255, '对对教练的具体价值', fontsize=10.5,
             color=ACCENT, weight='bold')
    fig.text(0.08, 0.230,
             '• 复盘选片时间从 30-60 分钟降到 5-10 分钟',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.210,
             '• 视频片段可以自动剪辑导出(后期演进,目前需要手工剪)',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.190,
             '• 公开课现场可拿出一份"教练精选的 5 个瞬间",每段 10 秒讲透',
             fontsize=9.5, color=INK)

    callout(fig, 0.06, 0.07, 0.88, 0.08, kind='warning')
    fig.text(0.08, 0.132, '诚实的限制', fontsize=10, color=WARNING, weight='bold')
    fig.text(0.08, 0.106,
             '• "候选" ≠ 真正发生的事件;教练需要快速判定是不是要讲的',
             fontsize=9, color=INK)
    fig.text(0.08, 0.087,
             '• 传球 / 射门 / 1v1 目前是低置信启发式,不应作为正式统计',
             fontsize=9, color=INK)

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 6 — Scenario 4: Parent communication card
# ---------------------------------------------------------------------------

def page_scenario_parent_card(pdf, page, total):
    fig = new_page()
    page_header(fig, '场景 4 · 家长沟通的客观语言',
                '「孩子今天表现怎么样?」可以这样回答')

    fig.text(0.06, 0.86, '教练日常工作', fontsize=12, color=INK, weight='bold')
    work = [
        '• 家长群、家长会、训练后接孩子时,经常被问:今天怎么样?',
        '• 凭印象回答的弱点:同一句话用多了显假;家长想要更具体的依据',
    ]
    for i, t in enumerate(work):
        fig.text(0.08, 0.835 - i * 0.022, t, fontsize=10, color=INK)

    fig.text(0.06, 0.770, '系统替教练做的', fontsize=12,
             color=ACCENT, weight='bold')
    fig.text(0.08, 0.745,
             '每场比赛自动生成"球员表现卡":一张纸,4 个核心指标 + 1 句教练评语',
             fontsize=10, color=INK)

    card_left, card_bottom, card_width, card_height = 0.10, 0.20, 0.80, 0.50
    rect = mpatches.FancyBboxPatch(
        (card_left, card_bottom), card_width, card_height,
        boxstyle='round,pad=0.005', linewidth=1.5,
        edgecolor=ACCENT, facecolor='white', transform=fig.transFigure,
    )
    fig.add_artist(rect)

    fig.text(card_left + card_width / 2, card_bottom + card_height - 0.04,
             '李梓萌  ·  本场表现卡', ha='center', fontsize=15,
             color=ACCENT, weight='bold')
    fig.text(card_left + card_width / 2, card_bottom + card_height - 0.07,
             '2026-05-15 周训练赛 vs 蓝队 (U13)', ha='center',
             fontsize=10, color=MUTED)

    grid_top = card_bottom + card_height - 0.10
    grid_h = 0.14
    box_w = (card_width - 0.06) / 4
    box_y = grid_top - grid_h
    boxes = [
        ('3.2 km', '全场跑动',      '↑ 比平均 +12%'),
        ('11.8%',  '前场压迫占比',  '↑ 比上场 +3.4 个百分点'),
        ('48%',    '进攻三区时间',  '= 进攻型前锋水平'),
        ('8/12',   '高速跑次数',    '↑ 比上场 +2 次'),
    ]
    for i, (v, l, d) in enumerate(boxes):
        bx = card_left + 0.02 + i * box_w + i * 0.005
        big_number_box(fig, bx, box_y, box_w, grid_h,
                       value=v, label=l, delta=d, delta_color=ACCENT)

    comment_y = box_y - 0.13
    comment = mpatches.FancyBboxPatch(
        (card_left + 0.02, comment_y), card_width - 0.04, 0.09,
        boxstyle='round,pad=0.004', linewidth=0.8,
        edgecolor=LINE, facecolor=ACCENT_SOFT, transform=fig.transFigure,
    )
    fig.add_artist(comment)
    fig.text(card_left + 0.04, comment_y + 0.07, '教练评语',
             fontsize=10, color=ACCENT, weight='bold')
    fig.text(card_left + 0.04, comment_y + 0.043,
             '今天前场压迫强度明显提高,这周的"无球反抢"训练有起色。',
             fontsize=9.5, color=INK)
    fig.text(card_left + 0.04, comment_y + 0.024,
             '下周训练重点:接球前观察 + 第一脚处理速度。',
             fontsize=9.5, color=INK)

    fig.text(card_left + card_width / 2, card_bottom + 0.02,
             '教练:王老师  ·  红队  ·  红日青训',
             ha='center', fontsize=8, color=MUTED)

    callout(fig, 0.06, 0.07, 0.88, 0.10, kind='feature')
    fig.text(0.08, 0.16, '对对教练的具体价值', fontsize=10.5,
             color=ACCENT, weight='bold')
    fig.text(0.08, 0.135,
             '• 家长群可分享,无需多解释 —— 数据自带说服力',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.115,
             '• 教练的教练评语保留主观判断空间;数据只是"由头"',
             fontsize=9.5, color=INK)
    fig.text(0.08, 0.095,
             '• 长期下来,这些卡片就是孩子的"足球成长档案"',
             fontsize=9.5, color=INK)

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 7 — Demo player report cover (radar)
# ---------------------------------------------------------------------------

def page_demo_player_cover(pdf, page, total):
    fig = new_page()
    page_header(fig, '演示:球员发展报告',
                '1-3 个月内可演进的"完整球员档案"形态')

    fig.text(0.10, 0.83, '李梓萌', fontsize=24, color=ACCENT, weight='bold')
    fig.text(0.10, 0.795, 'U13 · 红队 · 中前卫 / 影锋',
             fontsize=11, color=MUTED)
    fig.text(0.10, 0.775, '加入时间:2026-01-15 (5 个月)',
             fontsize=9.5, color=MUTED)
    fig.text(0.10, 0.758, '教练:王老师', fontsize=9.5, color=MUTED)

    photo_rect = mpatches.Rectangle(
        (0.72, 0.745), 0.16, 0.13, linewidth=1.2,
        edgecolor=LINE, facecolor=PANEL_BG, transform=fig.transFigure,
    )
    fig.add_artist(photo_rect)
    fig.text(0.80, 0.81, '球员照片', ha='center', fontsize=9, color=MUTED)
    fig.text(0.80, 0.78, '(占位)', ha='center',
             fontsize=8, color=MUTED, style='italic')

    categories = ['跑动覆盖', '站位纪律', '压迫意识', '进攻参与', '对抗距离']
    values = [7.2, 6.4, 5.8, 7.5, 6.1]
    team_avg = [6.0, 6.5, 5.2, 6.3, 6.0]

    N = len(categories)
    angles = [n / float(N) * 2 * np.pi for n in range(N)]
    angles += angles[:1]
    values_closed = values + values[:1]
    team_closed = team_avg + team_avg[:1]

    ax = fig.add_axes([0.20, 0.30, 0.60, 0.40], projection='polar')
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=11, color=INK)
    ax.set_ylim(0, 10)
    ax.set_yticks([2, 4, 6, 8, 10])
    ax.set_yticklabels(['2', '4', '6', '8', '10'], fontsize=8, color=MUTED)
    ax.spines['polar'].set_color(LINE)
    ax.grid(color=LINE, linestyle=':', alpha=0.6)

    ax.plot(angles, team_closed, color=MUTED, linewidth=1.5,
            linestyle='--', label='同队同位置均值')
    ax.fill(angles, team_closed, color=MUTED, alpha=0.10)
    ax.plot(angles, values_closed, color=ACCENT, linewidth=2.5,
            label='李梓萌(本月)')
    ax.fill(angles, values_closed, color=ACCENT, alpha=0.20)
    ax.legend(loc='upper right', bbox_to_anchor=(1.30, 1.10),
              fontsize=9, frameon=False)
    ax.set_title('综合能力雷达(5 维)', fontsize=11, color=INK, pad=20)

    callout(fig, 0.06, 0.10, 0.88, 0.16, kind='feature')
    fig.text(0.08, 0.245, '5 个月发展速览',
             fontsize=11, color=ACCENT, weight='bold')
    summary_lines = [
        '• 跑动覆盖、进攻参与显著高于同位置均值 → 体能 + 攻击意识发展良好',
        '• 压迫意识从 4.2 → 5.8,仍低于均值 5.2 → 是下阶段重点',
        '• 站位纪律(6.4)略低于均值 6.5 → 进攻欲望强,但回防偶有失位',
        '• 对抗距离 6.1 → 在小个子球员里属于积极拼抢类型',
    ]
    for i, t in enumerate(summary_lines):
        fig.text(0.08, 0.218 - i * 0.022, t, fontsize=9.5, color=INK)

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 8 — Demo player report: matches table + coach evaluation
# ---------------------------------------------------------------------------

def page_demo_player_table(pdf, page, total):
    fig = new_page()
    page_header(fig, '演示:球员发展报告(续)',
                '近 6 场比赛数据汇总 + 教练评语区')

    fig.text(0.06, 0.86, '近 6 场比赛 · 关键指标',
             fontsize=12, color=INK, weight='bold')

    headers = ['日期', '对手', '出场\n时间', '跑动\n(km)',
               '高速跑\n(m)', '压迫\n占比%', '进攻三区\n时间%', '评分']
    rows = [
        ['05-15', '蓝队',   '28 分钟', '3.2', '340', '11.8', '48%', '7.5'],
        ['05-08', '黄队',   '30 分钟', '3.1', '305',  '9.4', '45%', '7.2'],
        ['04-30', '蓝队',   '25 分钟', '2.9', '280',  '8.2', '42%', '6.8'],
        ['04-22', '绿队',   '30 分钟', '3.0', '262',  '7.6', '41%', '7.0'],
        ['04-15', '红蓝队', '27 分钟', '2.8', '245',  '6.8', '38%', '6.5'],
        ['04-08', '黄队',   '30 分钟', '2.7', '220',  '6.2', '36%', '6.4'],
    ]

    ax = fig.add_axes([0.06, 0.55, 0.88, 0.28])
    ax.axis('off')
    col_widths = [0.06, 0.08, 0.10, 0.09, 0.10, 0.10, 0.13, 0.08]
    total_width = sum(col_widths)
    col_x = []
    cur = 0
    for w in col_widths:
        col_x.append((cur + w / 2) / total_width)
        cur += w

    for i, h in enumerate(headers):
        ax.text(col_x[i], 0.90, h, ha='center', va='top',
                fontsize=9.5, color=ACCENT, weight='bold')
    ax.axhline(0.85, color=LINE, linewidth=0.8)

    row_h = 0.13
    for r_idx, row in enumerate(rows):
        y = 0.85 - (r_idx + 1) * row_h
        bg_color = 'white' if r_idx % 2 == 0 else PANEL_BG
        ax.axhspan(y, y + row_h, color=bg_color, alpha=0.6)
        for c_idx, cell in enumerate(row):
            color = INK if c_idx != 7 else ACCENT
            weight = 'bold' if c_idx == 7 else 'normal'
            ax.text(col_x[c_idx], y + row_h / 2, cell, ha='center',
                    va='center', fontsize=9, color=color, weight=weight)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    callout(fig, 0.06, 0.30, 0.88, 0.22, kind='feature')
    fig.text(0.08, 0.50, '教练评语(可手写或文字录入)',
             fontsize=11, color=ACCENT, weight='bold')
    evals = [
        '近 6 场跑动 + 高速跑均稳定提升,体能基础好,可作为持续输出型球员培养。',
        '前场压迫占比 6.2% → 11.8% 接近翻倍,"主动反抢"训练效果明显。',
        '进攻三区时间稳定在 36-48%,典型攻击型中场;但要注意防守纪律。',
        '下阶段重点:1) 接球前观察;2) 第一脚处理速度;3) 防守落位。',
    ]
    for i, t in enumerate(evals):
        fig.text(0.08, 0.46 - i * 0.030, t, fontsize=10, color=INK)

    callout(fig, 0.06, 0.13, 0.88, 0.13, kind='neutral')
    fig.text(0.08, 0.245, '升学 / 选拔推荐用语(系统辅助生成,教练审核修改)',
             fontsize=11, color=MUTED, weight='bold')
    fig.text(0.08, 0.215,
             '李梓萌(U13),近 5 个月在我队红队系统训练,5 场比赛平均跑动 2.95 km,',
             fontsize=10, color=INK)
    fig.text(0.08, 0.193,
             '高速跑距离从 220m 增长至 340m。攻击意识突出,适合中前卫 / 影锋位置。',
             fontsize=10, color=INK)
    fig.text(0.08, 0.171,
             '推荐进入更高年龄段联赛历练 / 入选区队选拔。',
             fontsize=10, color=INK)
    fig.text(0.08, 0.148,
             '— 红日青训 · 王老师(B 级教练员)',
             fontsize=9, color=MUTED, style='italic')

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Page 9 — Capability roadmap (honest)
# ---------------------------------------------------------------------------

def page_roadmap(pdf, page, total):
    fig = new_page()
    page_header(fig, '诚实的能力地图',
                '前面演示的每一项,目前处在什么阶段')

    headers = ['能力', '现状', '演进周期', '需要教练配合']
    rows = [
        ['per-match 球员指标', '已实现', '—',
         '拍摄视频 + 一次场地标定'],
        ['球检测(球点轨迹)', '已实现 83% 召回(单场)',
         '其他场地需重新评估', '可选:每场标 30 秒金标'],
        ['视频自动剪片段', '候选时刻已标记,剪辑待写',
         '1-2 周可做', '—'],
        ['跨场球员发展曲线', '数据齐全,聚合视图待开发',
         '1-2 周', '提供球员名单 + 号码'],
        ['完整球员发展报告 PDF', '当前为演示形态',
         '2-3 周(对接教练格式)',
         '提供 1-2 份当前使用模板'],
        ['训练前后效果对比', '可做(把训练当短比赛)',
         '2-3 周', '提供 1-2 节训练视频'],
        ['训练课视频分析', '未开始', '3-4 周',
         '训练机位 / 录制规范'],
        ['LLM 教练助手(语言生成)', '未开始',
         '3-4 周', '教练的语言习惯样本'],
        ['对接体育局 / 足协格式', '未开始',
         '和具体格式相关', '教练手头的官方模板'],
    ]

    ax = fig.add_axes([0.06, 0.18, 0.88, 0.69])
    ax.axis('off')
    col_widths = [0.27, 0.27, 0.18, 0.28]
    col_x_left = []
    cur = 0
    for w in col_widths:
        col_x_left.append(cur)
        cur += w

    header_y = 0.95
    for i, h in enumerate(headers):
        ax.text(col_x_left[i] + 0.005, header_y, h,
                ha='left', va='top', fontsize=10,
                color=ACCENT, weight='bold')
    ax.axhline(0.92, color=ACCENT, linewidth=1.2)

    row_y = 0.92
    row_height = 0.085
    for r_idx, row in enumerate(rows):
        bg = 'white' if r_idx % 2 == 0 else PANEL_BG
        ax.axhspan(row_y - row_height, row_y, color=bg, alpha=0.5)
        for c_idx, cell in enumerate(row):
            color = INK
            weight = 'normal'
            if c_idx == 1:
                if '已实现' in cell:
                    color = ACCENT
                    weight = 'bold'
                elif '未开始' in cell:
                    color = MUTED
            ax.text(col_x_left[c_idx] + 0.005, row_y - 0.013, cell,
                    ha='left', va='top', fontsize=9, color=color,
                    weight=weight, wrap=True)
        row_y -= row_height
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    page_footer(fig, page, total)
    plt.axis('off')
    pdf.savefig(fig, bbox_inches='tight')
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = (PROJECT_ROOT / 'reports' / 'coach_preview'
                   / f"coach_preview_{datetime.now().strftime('%Y%m%d')}.pdf")
    parser.add_argument('--out', default=str(default_out),
                        help=f'Output PDF path (default: {default_out})')
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pages = [
        page_cover,
        page_positioning,
        page_philosophy,
        page_scenario_training_effect,
        page_scenario_player_trend,
        page_scenario_review,
        page_scenario_parent_card,
        page_demo_player_cover,
        page_demo_player_table,
        page_roadmap,
    ]
    total = len(pages)
    with PdfPages(out_path) as pdf:
        for idx, page_fn in enumerate(pages, start=1):
            page_fn(pdf, idx, total)

    print(f'✅ PDF generated: {out_path}')
    print(f'   pages: {total}')
    print(f'   size:  {out_path.stat().st_size / 1024:.1f} KB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
