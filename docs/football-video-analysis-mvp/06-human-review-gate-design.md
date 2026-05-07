# 人工校验闸口设计

## 目标

在最终报告输出前增加一个轻量人工校验阶段。系统先完成检测、追踪、身份绑定和关键候选事件抽样，并给出预标注；人工只处理“不对的地方”，其余一键确认。目标是提升身份、关键片段和球位置的可信度，而不是做逐帧标注。

## 总体流程

```text
提交场地标定
  -> 系统初步分析
  -> 生成校验包
  -> Home / 人工校验 Tab
  -> 人工确认或修正
  -> 应用修正
  -> 生成最终 Markdown / HTML / CSV 报告
```

最终报告前必须满足:

- 人工校验包已生成
- 所有必检项状态为 `confirmed`、`corrected` 或 `ignored`
- 修正结果已写入比赛项目目录

## 系统抽样策略

系统优先抽以下帧或片段:

| 类型 | 抽样原因 | 默认数量 |
| --- | --- | --- |
| 低身份置信 tracklet | 可能认错人或串号 | 10-20 |
| 关键片段时间戳 | 会进入报告，需要确认有效性 | 10-30 |
| 传球/射门/抢断/1v1 候选 | 当前属于低置信事件 | 10-30 |
| 球不可见或低置信帧 | 影响传球、射门和球权链路 | 5-20 |
| 跑动/速度异常帧 | 可能由映射误差或串号导致 | 5-10 |

每场建议总量控制在 `20-60` 个 review item。超过这个数量时，优先保留会影响报告结论的条目。

## 预标注内容

每个 review item 必须由系统先给出默认判断:

```yaml
review_id: review_0001
timestamp_sec: 136.0
timestamp: "02:16"
reason: low_identity_confidence
frame_image: review/frames/review_0001.jpg
system_labels:
  player:
    predicted_id: red_tyx
    name: TYX
    confidence: 0.46
    bbox_xyxy: [123, 456, 188, 590]
  ball:
    visible: false
    confidence: 0.12
    center_xy: null
  event:
    type: 压迫候选
    confidence: 中
    include_in_report: true
human_review:
  status: pending
  player_id: null
  ball_center_xy: null
  event_valid: null
  note: ""
```

## 人工操作设计

人工只做以下简单动作:

- `确认`: 系统预标注正确
- `改球员`: 从本队球员、对手、场外人员、不确定中选择
- `点球`: 如果球可见，点击球心；如果不可见，选择不可见
- `事件有效性`: 有效 / 无效 / 不确定
- `报告处理`: 保留 / 降低置信度 / 从报告删除

不做逐帧框选球员。球员框默认由检测模型提供，人工只改身份。

## 输出文件

```text
matches/<match_id>/review/human_review/
  review_manifest.yaml
  review_items.csv
  frames/
    review_0001.jpg
    review_0002.jpg
  corrections.yaml
```

`corrections.yaml` 记录人工修正:

```yaml
status: submitted
submitted_at: "2026-05-06T18:00:00+08:00"
items:
  review_0001:
    status: corrected
    player_id: red_zmc
    ball_visible: true
    ball_center_xy: [1420, 610]
    event_valid: true
    report_action: keep
  review_0002:
    status: confirmed
```

## 应用修正

报告生成前新增一个修正应用层:

1. 读取 `tracks_red_labeled.csv`、`key_timestamps.csv` 和候选事件。
2. 读取 `corrections.yaml`。
3. 对人工修正的 tracklet 或时间戳覆盖系统判断。
4. 对人工标为无效的事件从报告中删除或降置信。
5. 对人工点选的球位置写入 `ball_review_points.csv`，供传球、射门、1v1 和球权链路后续使用。

## 分阶段落地

第一阶段:

- 生成 review manifest
- Home 增加 `人工校验` Tab
- 支持确认/改球员/事件有效性
- 最终报告读取校验状态和事件修正

第二阶段:

- 支持点击球心
- 把人工球点接入传球、射门、1v1 候选判断
- 报告显示人工校验覆盖率

第三阶段:

- 支持更细的球权链路
- 将传球成功率、1v1 成功率从“待增强”升级为可统计指标

## 当前架构配置

新比赛配置中加入:

```yaml
review:
  human_review:
    enabled: true
    gate_before_final_report: true
    max_review_items: 60
    selection:
      - low_identity_confidence
      - key_timestamps
      - event_candidates
      - ball_uncertain_frames
      - speed_or_distance_outliers
    mode: system_prelabels_human_confirms_or_corrects
```

当前代码先写入该配置和设计文档；下一步实现时，应把 `提交打标并开启分析` 调整为“生成校验包”，校验提交后再启动最终报告生成。
