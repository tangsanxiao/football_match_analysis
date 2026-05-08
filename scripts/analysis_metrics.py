"""Shared metric definitions and display labels for match analysis reports."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


DEFAULT_METRICS = [
    "shot",
    "pass",
    "steal",
    "positional_discipline",
    "pressing_intensity",
    "off_ball_movement",
    "1v1_attack_defense",
    "defensive_off_ball_movement",
    "space_creation",
    "pass_success",
    "observed_coverage",
]

METRIC_DEFINITIONS: List[Dict[str, str]] = [
    {
        "key": "shot",
        "label": "射门",
        "description": "识别本队最后触球后球向球门或门区高速移动的候选片段；当前受足球检测覆盖率影响，先作为候选统计。",
        "report_status": "候选片段",
        "report_note": "结合人工/系统球点与前场球权归属输出可能射门时刻，暂不计入正式评分。",
    },
    {
        "key": "pass",
        "label": "传球",
        "description": "尝试基于球权从一名本队球员转移到另一名本队球员的轨迹链路统计传球候选。",
        "report_status": "候选片段",
        "report_note": "结合人工/系统球点、短缺口插值和本队球权转移输出候选传球，暂不作为正式次数。",
    },
    {
        "key": "pass_success",
        "label": "传球成功率",
        "description": "在可观察球权链路内估算成功传球占比；球检测不足时会降级为低置信候选。",
        "report_status": "待增强",
        "report_note": "需要更稳定的连续球权链路；当前只给参考值或样本不足提示。",
    },
    {
        "key": "steal",
        "label": "抢断",
        "description": "识别对手控球后，本队球员近距离施压并导致球权转换的候选事件。",
        "report_status": "候选片段",
        "report_note": "可结合近距离压迫和球权转换做候选，但当前不作为正式抢断统计。",
    },
    {
        "key": "1v1_attack_defense",
        "label": "1v1 攻防",
        "description": "统计持球人与最近防守人形成近距离对抗的次数、方向和成功倾向，是后续重点增强指标。",
        "report_status": "待增强",
        "report_note": "需要更稳定的持球人/防守人关系识别；当前以压迫距离和对抗距离作为前置代理。",
    },
    {
        "key": "positional_discipline",
        "label": "站位纪律",
        "description": "评估球员是否稳定出现在角色对应区域，以及攻防转换中是否保持合理纵深和宽度。",
        "report_status": "已输出代理指标",
        "report_note": "通过角色区域占比、平均位置和进攻/防守区域参与度衡量。",
    },
    {
        "key": "pressing_intensity",
        "label": "压迫强度",
        "description": "统计球员在进攻半场或对手附近进入压迫距离的占比，并结合移动方向做压迫候选。",
        "report_status": "已输出代理指标",
        "report_note": "通过进攻半场内接近最近对手的帧占比和关键片段输出。",
    },
    {
        "key": "off_ball_movement",
        "label": "无球跑动",
        "description": "评估非持球阶段的跑动距离、进入进攻三区、拉开宽度和接应路线等代理指标。",
        "report_status": "已输出代理指标",
        "report_note": "通过跑动距离、高速跑、进攻三区进入和关键跑动片段输出。",
    },
    {
        "key": "defensive_off_ball_movement",
        "label": "防守无球跑动",
        "description": "关注回收、补位、封堵中路和协防距离变化，用于区分只站位和主动防守移动。",
        "report_status": "部分输出",
        "report_note": "当前以后卫/回收区域、最近对手距离和移动强度为代理，仍需球权链路增强。",
    },
    {
        "key": "space_creation",
        "label": "创造空间",
        "description": "观察无球跑动是否拉开防线、创造接应角度或带走防守人；第一版以空间代理指标输出。",
        "report_status": "部分输出",
        "report_note": "当前以进攻三区、宽度/纵深跑动和接应空间代理衡量，暂不做正式创造空间次数。",
    },
    {
        "key": "observed_coverage",
        "label": "观察覆盖率",
        "description": "该球员被自动识别并绑定成功的去重帧数 / 本次采样处理总帧数 × 100%。它反映本场可评价样本量，不等同真实上场时间；低覆盖率提示遮挡、远景或身份绑定需要人工校验。",
        "report_status": "已输出辅助指标",
        "report_note": "计算方式为该球员被自动识别并绑定成功的去重帧数 / 本次采样处理总帧数 × 100%；它反映可评价样本量，不等同真实上场时间。",
    },
]

METRIC_BY_KEY = {item["key"]: item for item in METRIC_DEFINITIONS}
METRIC_STATUS: Dict[str, Tuple[str, str, str]] = {
    item["key"]: (item["label"], item["report_status"], item["report_note"])
    for item in METRIC_DEFINITIONS
}

ROLE_LABELS = {
    "goalkeeper": "门将",
    "defender": "后卫",
    "right_forward": "右前锋",
    "center_forward": "中锋",
    "left_forward": "左前锋",
    "substitute": "替补",
}

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "provisional": "待校正",
    "none": "无",
}


def metric_definition(key: str) -> Dict[str, str]:
    return METRIC_BY_KEY.get(
        key,
        {
            "key": key,
            "label": key,
            "description": "",
            "report_status": "已选择",
            "report_note": "该指标暂未配置详细说明。",
        },
    )


def selected_metric_definitions(keys: Iterable[str]) -> List[Dict[str, str]]:
    return [metric_definition(str(key)) for key in keys]


def metric_status_rows(keys: Iterable[str]) -> List[Dict[str, Any]]:
    rows = []
    for key in keys:
        item = metric_definition(str(key))
        rows.append(
            {
                "指标": item["label"],
                "当前报告状态": item["report_status"],
                "说明": item["report_note"],
            }
        )
    return rows
