# 球位置人工标注设计

## 目标

在球员身份校验之后，增加轻量球位置标注。系统先抽取连续关键帧并给出球的预标注；人工只需要在图片上点击球心，页面自动进入下一张。该结果用于提升传球、传球成功率、射门、抢断、1v1 攻防和球权归属等低置信指标。

## 流程

```text
生成球标注包
  -> Home / 人工校验 / 球位置标注
  -> 点击球心或标记球不可见
  -> 自动保存草稿
  -> 提交球点
  -> 使用人工球点重算报告
```

## 抽帧策略

- 系统检测到球的连续片段，前后各补若干帧。
- 关键片段时间戳前后连续帧。
- 全场按固定间隔抽样，补全球权链路。

默认每场控制在约 120-180 张图，先保证 5-15 分钟内可完成。

## 输出

```text
matches/<match_id>/review/ball_review/
  ball_review_manifest.yaml
  ball_review_items.csv
  ball_review_points.csv
  corrections.yaml
  frames/
```

`ball_review_points.csv` 包含:

- `frame_idx`
- `timestamp_sec`
- `ball_visible`
- `review_image_x/y`
- `source_image_x/y`
- `field_x_m/y`
- `source`
- `status`

## 指标接入

报告生成时优先读取人工球点。如果存在有效人工球点，则低置信技术参考表会使用这些球点估算最近控球人、传球候选、射门候选、1v1 和抢断候选；如果没有人工球点，则回退到系统检测到的足球轨迹。
