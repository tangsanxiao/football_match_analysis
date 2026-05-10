# 评估底座与 YOLO 球检测基线

> 日期：2026-05-10
> 范围：建立项目级评估底座；用真实金标对当前 YOLO 球检测做基线测量；A/B 验证模型升级是否能解决球检测问题。
> 结论：**模型升级（YOLO11n → YOLO11s）不能解决球检测问题**。下一步应转向自训练球头。

---

## 1. 背景与动机

在 design.md 反复强调"承认不确定性"、"诚实置信度"的同时，项目实际**没有任何方式量化**改动是否真正改善了报告质量。改了检测参数、换了过滤器，最终都靠"看起来更好"判断。

为支持后续模型迭代和重构有据可依，需要：

1. 一份可重复运行的**评估底座**（System），使每次提交都能跑出指标对比。
2. 一份可扩展的**金标数据**（Data），逐场积累。
3. 一份可信的**起点基线**，让任何"我提升了 X"的 PR 必须用数字说话。

---

## 2. 落地的评估底座

### 目录结构

```
evals/
├── README.md                     # 操作指引
├── gold_schema_v1.yaml           # 金标 schema 契约（manifest / tracks / ball_points / events）
├── gold/<match_id>/              # 一场比赛对应一份金标，可逐步扩展（部分标注也可用）
│   ├── manifest.yaml
│   ├── ball_points.csv
│   ├── tracks.csv (optional)
│   └── events.csv (optional)
└── runs/<eval_id>/               # 生成的评估报告（gitignored）
    ├── report.md
    ├── report.html
    ├── layer_a.yaml
    ├── layer_b.yaml
    └── manifest.yaml
```

### 两层评估

| 层 | 检查内容 | 是否需要金标 | 适用范围 |
|---|---|---|---|
| **Layer A · Sanity** | 文件存在、CSV schema、值域、置信度合法、跨表一致性 | 否 | 任何完成态比赛 |
| **Layer B · Accuracy** | 球员检测召回率、身份绑定准确率、球点召回率与位置误差、事件 precision/recall | 是 | 当 `evals/gold/<match_id>/` 存在时自动启用 |

### 关键设计原则

- **底座是系统、不是数据**：金标可以增量积累，30 秒标注也能产出部分 Layer B 指标。
- **匹配容差参数化**：球员 `pos_tol=3m, ts_tol=0.5s`；球 `1.5m / 1.0s`；事件 `5s / 5m`。所有容差都在 `gold_schema_v1.yaml#match_tolerances` 默认，CLI 可覆盖。
- **球指标 `--ball-source` 区分三种系统输出**：
  - `raw`：YOLO 原始检测，无过滤
  - `filtered`（默认）：原始 + `filter_static_ball_false_positives`，**production 实际行为**
  - `reviewed`：人工复核后；如果金标本来就是从这份文件提取的，则**循环**，禁用

### 文件清单

| 文件 | 行数 | 职责 |
|---|---:|---|
| `scripts/analysis_eval.py` | 691 | 纯逻辑库；Finding/Metric/LayerAResult/LayerBResult 数据结构 + 6 个核心算法 + gold 加载 |
| `scripts/17_eval_against_gold.py` | 459 | CLI 驱动 + 多 match 聚合 + MD/HTML/YAML 渲染 |
| `evals/README.md` | 168 | 标注金标的操作指引 + 当前基线 + 改进方向 |
| `evals/gold_schema_v1.yaml` | 89 | 金标 schema 契约 |
| `tests/test_analysis_core.py` | (+219 新增) | 11 个新测试锁定 Layer A/B 算法 |

### 提交 hash 锚点

- `0e4d0bc` Add eval harness (Layer A sanity + Layer B accuracy)
- `03e999e` Add first real gold + honest YOLO ball detection baseline
- `1454998` Add --ball-source filtered + clarify YOLO ball detection bottleneck
- `405dc1e` Add --detections-dir override + YOLO11n vs 11s A/B baseline

---

## 3. 第一段真实金标

### 来源

`matches/中青赛_1_20260506_213657/review/ball_review/ball_review_points.csv` 共 120 行，**`source=human` 100%**。这是用户在原有人工复核流程中已经标注的真实数据，不是 AI 编造。

### 选窗口

`78–138s`（60 秒）。原因：
- 连续主动比赛段，包含位置切换
- 30 个 `in_play` 帧给 `ball_recall` 一个有意义的分母
- 系统在该窗口有 9 个候选事件（压迫/高速跑/进攻三区），将来标注事件金标时有素材

### 金标内容

```
evals/gold/中青赛_1_20260506_213657/
├── manifest.yaml                 # 声明只标了 ball_points
└── ball_points.csv               # 44 行 = 30 in_play + 11 invisible + 3 out_of_play
```

未标注：
- `tracks.csv`（球员位置）：人工成本太高（约 120 帧×多球员=数百标注），首版不投入
- `events.csv`（事件）：将来需要重看视频独立标注，**不能直接用系统输出充数**（会循环验证）

---

## 4. 关键实验与结论

### 实验 1 · 静态过滤对球检测的贡献

**问题**：现有 `analysis_detection_filters.filter_static_ball_false_positives` 究竟救了多少？

**做法**：同金标，对比 `--ball-source raw` vs `--ball-source filtered`。

**结果**：

| ball-source | 窗口内"球"数 | `ball_recall` |
|---|---:|---:|
| raw | 103（全聚在 (2.7m, 9.0m) 单点静态标记） | 0/30 (0%) |
| filtered | 66（剩下的 66 个全在 y≈22m 即场地外侧，`inside_play_area=False`） | 0/30 (0%) |

**结论**：
1. 过滤器**确实在工作**（移除了 63 行 / 3 个静态组）
2. 过滤器**没有遮蔽好球**（剩下的也都是噪声）
3. **瓶颈不在过滤器，而在 YOLO 模型本身**

### 实验 2 · YOLO11n vs YOLO11s 模型升级

**问题**：换更大模型能否让球检测从 0% 起飞？

**做法**：同金标、同窗口、同参数（`conf=0.18, imgsz=1280, sample-fps=2.0, ByteTrack`），只换权重。检测目录分别保留至：

```
matches/中青赛_1_20260506_213657/data/interim/detections/segment_0060_0100_2fps_yolo11n
matches/中青赛_1_20260506_213657/data/interim/detections/segment_0060_0100_2fps_yolo11s
```

**结果**：

| 模型 | 参数量 | 球检测数 | 帧覆盖率 | 轨迹数 | `recall@1.5m` | `recall@10m` |
|---|---:|---:|---:|---:|---:|---:|
| YOLO11n | 2.6M | 92 raw / 66 filtered | 42.8% | 3 | 0/30 | 0/30 |
| **YOLO11s** | **9.4M** | **268 raw / 176 filtered** | **87.6%** | **7** | **0/30** | **0/30** |

**位置分析**（YOLO11s 的 176 个 filtered 检测在窗口内的分布）：

| 聚类中心 | 检测数 |
|---|---:|
| (3m, 21m) | 89 |
| (6m, 24m) | 71 |
| (9m, 21m) | 15 |
| (12m, 21m) | 1 |

场地范围 `y ∈ [0, 20]`，**所有 11s 检测都在场地外**（y ≥ 21）。真实球在 `x ∈ [26, 38]`、`y ∈ [-0.4, 8.8]`，离最近的 11s 检测 **25.8 米**。

**结论**：
1. 11s 检测数量是 11n 的 **2.9×**，覆盖率 **2×**——但**新增的全部是错的**
2. **0/30 即使在 10 米容差下仍成立**，说明这不是"差一点"的问题，是**完全没看见**
3. **COCO `sports ball` 类训练数据严重不匹配** 5 人制业余画质（球只有 5–10 像素）。模型容量不能产生原本不在训练分布里的能力。

### 综合诊断

| 假设 | 数据反应 | 结论 |
|---|---|---|
| 过滤逻辑不够好 | 过滤的全是真噪声，没误删 | ❌ 不是瓶颈 |
| 模型容量不够（11n→11s） | 数量翻倍，召回仍 0% | ❌ 不是瓶颈 |
| COCO 训练数据与目标域错配 | 11s 误检高度集中在场外大型圆白物体 | ✅ **真正瓶颈** |
| 人工球点流程过度保守 | YOLO 在此画质下确实无法检测真球 | ❌ 反而是必要的 |

---

## 5. 已排除 / 待验证的改进方向

### ❌ 已用数据排除

- **YOLO11n → YOLO11s** 升级：不解决问题（本文实验 2）
- **改进静态过滤**：过滤器已在做正确的事；剩下的噪声本来就被 `inside_play_area` 兜底（实验 1）

### ⚠️ 大概率同结果，仅在便宜时确认

- YOLO11m / 11l：同样 COCO 训练，画质不变，预期同样 0%。如要验证，沿用本次 A/B 流程即可。

### ✅ 还在桌面上、值得投入

按 ROI 排序：

1. **🥇 自训练 YOLO 球头（domain-specific）**
   - 数据现成：`ball_review_points.csv` 里 84 个 `marked` 真球点
   - 反向投影：`(review_image_x, review_image_y)` + 固定 bbox 半径（≈±20 px）→ YOLO 标签
   - ultralytics 直接 fine-tune 预训权重
   - 评估底座就位，训完直接对同一金标跑 eval，看 0% → ?% 的实际跳变
   - 工程量：约一个工作日

2. **🥈 图像级裁剪到场地区域**
   - 在 YOLO 推理前把 4K 帧切到场地多边形 bbox
   - 减少边线干扰物搜索面积，相当于变相提升小目标分辨率
   - 但单独使用**不能从 0% 起飞**（前提是 YOLO 至少能"偶尔看到"，今天数据说没有）
   - 与方向 1 配合时是加成

3. **🥉 球类单独降低 conf + 收紧 play_area**
   - 工程量极小，但前两次实验显示阈值不是主因
   - 仅作为方向 1 训练后的 fine-tuning

---

## 6. 操作纪律（写入 CLAUDE.md / evals/README.md）

任何修改 detection / tracking / 报告生成相关代码，**合并 PR 前必须**：

```bash
.venv/bin/python scripts/17_eval_against_gold.py \
  --match $(ls matches | tr '\n' ' ' | sed 's/ /  --match /g') \
  --output baseline_<short_change_name>
```

对比 `evals/runs/baseline_<change>/` 与之前的 baseline，看数字变化。Layer A 任何 fail 视为构建失败；Layer B 任何指标回退必须在 commit message 解释。

---

## 7. 留给后续工作的接口

- `scripts/17_eval_against_gold.py --detections-dir <path>` 可把 ball metric 重定向到任何 tracks.csv，专为模型 A/B 设计
- `scripts/17_eval_against_gold.py --eval-label <text>` 把自定义标签写进 `manifest.yaml`，方便一组实验后回溯
- `analysis_eval.compute_player_detection_recall` / `compute_ball_metrics` / `compute_event_metrics` 都是无副作用纯函数，可被未来的工作区 UI 复用展示

---

## 8. 引用文件

- 评估底座代码：[scripts/analysis_eval.py](../../scripts/analysis_eval.py)、[scripts/17_eval_against_gold.py](../../scripts/17_eval_against_gold.py)
- Schema：[evals/gold_schema_v1.yaml](../../evals/gold_schema_v1.yaml)
- 操作指引与基线：[evals/README.md](../../evals/README.md)
- 第一段金标：[evals/gold/中青赛_1_20260506_213657/manifest.yaml](../../evals/gold/中青赛_1_20260506_213657/manifest.yaml)
- 项目根上下文（agent 入口）：[CLAUDE.md](../../CLAUDE.md)（"Eval harness" 章节）
- A/B 检测目录（gitignored，本地保留）：
  - `matches/中青赛_1_20260506_213657/data/interim/detections/segment_0060_0100_2fps_yolo11n/`
  - `matches/中青赛_1_20260506_213657/data/interim/detections/segment_0060_0100_2fps_yolo11s/`
- A/B 评估输出（gitignored，本地保留）：
  - `evals/runs/ab_yolo11n_raw/`、`ab_yolo11n_filtered/`
  - `evals/runs/ab_yolo11s_raw/`、`ab_yolo11s_filtered/`
