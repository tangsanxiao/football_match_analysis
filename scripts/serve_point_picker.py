#!/usr/bin/env python3
"""Serve the calibration point picker and auto-save clicks into YAML."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import yaml

try:
    from create_point_picker import build_html, load_yaml, point_payload, resolve_path
except ModuleNotFoundError:
    from scripts.create_point_picker import build_html, load_yaml, point_payload, resolve_path


MIN_SUBMITTED_POINTS = 4
ROLE_CHOICES = ["goalkeeper", "defender", "right_forward", "center_forward", "left_forward", "substitute"]


def yaml_name_value(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped.startswith("- name:"):
        return None
    return stripped.split(":", 1)[1].strip().strip("\"'")


def replace_image_xy_line(line: str, image_xy: Optional[Tuple[int, int]]) -> str:
    indent = line[: len(line) - len(line.lstrip())]
    if image_xy is None:
        return f"{indent}image_xy:\n"
    return f"{indent}image_xy: [{image_xy[0]}, {image_xy[1]}]\n"


def update_point_image_xy(points_path: Path, point_name: str, image_xy: Optional[Tuple[int, int]]) -> None:
    lines = points_path.read_text(encoding="utf-8").splitlines(keepends=True)
    current_name: Optional[str] = None
    found_point = False

    for index, line in enumerate(lines):
        maybe_name = yaml_name_value(line)
        if maybe_name is not None:
            current_name = maybe_name
            found_point = current_name == point_name
            continue

        if found_point and line.strip().startswith("image_xy:"):
            lines[index] = replace_image_xy_line(line, image_xy)
            points_path.write_text("".join(lines), encoding="utf-8")
            return

    raise ValueError(f"Could not find image_xy line for point: {point_name}")


def write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)


def point_counts(points_yaml: Dict[str, Any]) -> Tuple[int, int]:
    enabled = 0
    marked = 0
    for point in points_yaml.get("points", []):
        if point.get("enabled") is False:
            continue
        enabled += 1
        image_xy = point.get("image_xy")
        if isinstance(image_xy, list) and len(image_xy) == 2:
            marked += 1
    return marked, enabled


def mark_submission_dirty(points_path: Path) -> None:
    points_yaml = load_yaml(points_path)
    calibration = points_yaml.setdefault("calibration", {})
    if not calibration.get("labeling_submitted") and calibration.get("labeling_status") != "submitted":
        return
    calibration["labeling_status"] = "draft_after_edit"
    calibration["labeling_submitted"] = False
    calibration["labeling_last_edited_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_yaml(points_path, points_yaml)


def submit_labeling(points_path: Path) -> Dict[str, Any]:
    points_yaml = load_yaml(points_path)
    marked, enabled = point_counts(points_yaml)
    if marked < MIN_SUBMITTED_POINTS:
        raise ValueError(f"At least {MIN_SUBMITTED_POINTS} enabled points must be marked before submit; got {marked}.")

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    calibration = points_yaml.setdefault("calibration", {})
    calibration["labeling_status"] = "submitted"
    calibration["labeling_submitted"] = True
    calibration["labeling_submitted_at"] = now
    calibration["labeling_submitted_point_count"] = marked
    calibration["labeling_enabled_point_count"] = enabled
    calibration["labeling_required_min_points"] = MIN_SUBMITTED_POINTS
    write_yaml(points_path, points_yaml)
    return {
        "submitted_at": now,
        "marked_points": marked,
        "enabled_points": enabled,
        "required_min_points": MIN_SUBMITTED_POINTS,
    }


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "item"


def coerce_number(value: Any) -> Any:
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else text


def clean_string_list(values: Any) -> List[str]:
    if isinstance(values, str):
        raw = values.split(",")
    elif isinstance(values, list):
        raw = values
    else:
        raw = []
    return [str(item).strip() for item in raw if str(item).strip()]


def match_info_payload(config: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    teams = config.get("teams", {})
    analyze_team = str(teams.get("analyze_team", "red"))
    team = teams.get(analyze_team, {})
    review_match = config.get("review", {}).get("match_info", {})
    return {
        "config_path": str(config_path),
        "match": {
            "id": config.get("match", {}).get("id", ""),
            "name": config.get("match", {}).get("name", ""),
            "video_path": config.get("match", {}).get("video_path", ""),
        },
        "field": {
            "length_m": config.get("field", {}).get("length_m", 40.0),
            "width_m": config.get("field", {}).get("width_m", 20.0),
        },
        "team": {
            "key": analyze_team,
            "display_name": team.get("display_name", analyze_team),
            "team_color": team.get("team_color", ""),
            "field_colors": team.get("kit", {}).get("field_colors", []),
            "goalkeeper_colors": team.get("kit", {}).get("goalkeeper_colors", []),
            "players": team.get("players", []),
            "substitutions": team.get("substitutions", []),
        },
        "analysis": {
            "target_metrics": config.get("analysis", {}).get("target_metrics")
            or config.get("analysis", {}).get("target_events")
            or [],
        },
        "review": {
            "match_info_status": review_match.get("status", ""),
            "match_info_confirmed_at": review_match.get("confirmed_at", ""),
        },
    }


def update_match_info(config_path: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    config = load_yaml(config_path)
    config.setdefault("match", {})
    config.setdefault("field", {})
    config.setdefault("teams", {})
    config.setdefault("analysis", {})
    config.setdefault("review", {})

    match_payload = payload.get("match", {})
    field_payload = payload.get("field", {})
    team_payload = payload.get("team", {})
    analysis_payload = payload.get("analysis", {})

    match_name = str(match_payload.get("name", "")).strip()
    if match_name:
        config["match"]["name"] = match_name
    config["field"]["length_m"] = float(field_payload.get("length_m") or config["field"].get("length_m") or 40.0)
    config["field"]["width_m"] = float(field_payload.get("width_m") or config["field"].get("width_m") or 20.0)

    teams = config["teams"]
    old_team_key = str(teams.get("analyze_team", "red"))
    team_key = slugify(str(team_payload.get("key") or old_team_key))
    existing_team = teams.get(old_team_key, {}) if isinstance(teams.get(old_team_key), dict) else {}
    if old_team_key != team_key and team_key not in teams:
        teams.pop(old_team_key, None)
    team = teams.setdefault(team_key, existing_team if existing_team else {})
    teams["analyze_team"] = team_key

    field_colors = clean_string_list(team_payload.get("field_colors"))
    goalkeeper_colors = clean_string_list(team_payload.get("goalkeeper_colors"))
    players_payload = team_payload.get("players") if isinstance(team_payload.get("players"), list) else []
    substitutions_payload = team_payload.get("substitutions") if isinstance(team_payload.get("substitutions"), list) else []

    players: List[Dict[str, Any]] = []
    for item in players_payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        player_id = str(item.get("player_id") or slugify(f"{team_key}_{name}")).strip()
        role = str(item.get("role", "")).strip() or "substitute"
        players.append(
            {
                "player_id": player_id,
                "name": name,
                "number": coerce_number(item.get("number", "")),
                "role": role,
                "visual_hint": str(item.get("visual_hint", "")).strip(),
            }
        )
    if not players:
        raise ValueError("At least one player is required before confirming match information.")

    substitutions: List[Dict[str, Any]] = []
    for item in substitutions_payload:
        if not isinstance(item, dict):
            continue
        at = str(item.get("at", "")).strip()
        out_player = str(item.get("out", "")).strip()
        in_player = str(item.get("in", "")).strip()
        note = str(item.get("note", "")).strip()
        if not any([at, out_player, in_player, note]):
            continue
        substitutions.append({"at": at, "out": out_player, "in": in_player, "note": note})

    team["display_name"] = str(team_payload.get("display_name") or team_key).strip()
    team["team_color"] = str(team_payload.get("team_color") or "").strip()
    team.setdefault("kit", {})
    team["kit"]["field_colors"] = field_colors
    team["kit"]["goalkeeper_colors"] = goalkeeper_colors
    team["players"] = players
    team["substitutions"] = substitutions

    metrics = clean_string_list(analysis_payload.get("target_metrics"))
    if not metrics:
        raise ValueError("At least one target metric is required.")
    config["analysis"]["target_metrics"] = metrics
    config["analysis"]["target_events"] = metrics
    config["analysis"].setdefault("report_formats", ["markdown", "csv", "html"])

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    config["review"]["match_info"] = {
        "status": "confirmed",
        "confirmed": True,
        "confirmed_at": now,
        "player_count": len(players),
        "substitution_count": len(substitutions),
        "target_metric_count": len(metrics),
    }
    write_yaml(config_path, config)
    return {
        "confirmed_at": now,
        "players": len(players),
        "substitutions": len(substitutions),
        "target_metrics": len(metrics),
        "team_key": team_key,
    }


def build_match_info_html(config: Dict[str, Any], config_path: Path) -> str:
    payload = json.dumps(match_info_payload(config, config_path), ensure_ascii=False)
    role_choices = json.dumps(ROLE_CHOICES, ensure_ascii=False)
    template = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>比赛信息确认</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f4;
      --panel: #ffffff;
      --ink: #18221c;
      --muted: #657169;
      --accent: #0f7b63;
      --line: #d8ded8;
      --warn: #9a5a00;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      max-width: 1180px;
      margin: 0 auto;
      padding: 16px;
    }
    header, section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 12px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 14px;
    }
    h1, h2 {
      margin: 0;
      font-size: 16px;
      letter-spacing: 0;
    }
    h2 {
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
    }
    .status {
      color: var(--muted);
      font-size: 13px;
      text-align: right;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      padding: 12px;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
    }
    input, select, textarea, button {
      font: inherit;
    }
    input, select, textarea {
      width: 100%;
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      padding: 6px 8px;
    }
    textarea {
      min-height: 86px;
      resize: vertical;
      line-height: 1.45;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 7px;
      text-align: left;
      vertical-align: top;
    }
    th {
      background: #f9faf7;
      color: var(--muted);
      font-weight: 600;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    .table-wrap {
      max-height: 360px;
      overflow: auto;
    }
    .actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      padding: 12px;
      border-top: 1px solid var(--line);
    }
    button {
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      cursor: pointer;
      font-weight: 650;
      padding: 0 12px;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    button.danger {
      color: #8a250f;
    }
    a {
      color: var(--accent);
      text-decoration: none;
      font-weight: 650;
    }
    .saved { color: var(--accent); font-weight: 700; }
    .failed { color: var(--warn); font-weight: 700; }
    .wide { grid-column: span 2; }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr 1fr; }
      header { align-items: flex-start; flex-direction: column; }
      .status { text-align: left; }
    }
    @media (max-width: 640px) {
      .grid { grid-template-columns: 1fr; }
      .wide { grid-column: auto; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>比赛信息确认</h1>
        <div class="status" id="configPath"></div>
      </div>
      <div class="status">
        <div id="saveStatus">待确认</div>
        <a href="/">去标定场地坐标</a>
      </div>
    </header>

    <section>
      <h2>比赛与球队</h2>
      <div class="grid">
        <label>比赛 ID<input id="matchId" disabled></label>
        <label>比赛名称<input id="matchName"></label>
        <label>球场长度米<input id="fieldLength" type="number" step="0.1"></label>
        <label>球场宽度米<input id="fieldWidth" type="number" step="0.1"></label>
        <label>球队 key<input id="teamKey"></label>
        <label>球队名称<input id="teamName"></label>
        <label>球队主色<input id="teamColor"></label>
        <label>场上球员颜色<input id="fieldColors"></label>
        <label>门将颜色<input id="goalkeeperColors"></label>
        <label class="wide">视频路径<input id="videoPath" disabled></label>
        <label class="wide">重点分析指标<textarea id="targetMetrics"></textarea></label>
      </div>
    </section>

    <section>
      <h2>人员与特征</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>球员 ID</th>
              <th>代号</th>
              <th>号码</th>
              <th>位置</th>
              <th>重点特征</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="playersBody"></tbody>
        </table>
      </div>
      <div class="actions">
        <button id="addPlayer">添加球员</button>
      </div>
    </section>

    <section>
      <h2>换人信息</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>时间</th>
              <th>下场</th>
              <th>上场</th>
              <th>备注</th>
              <th></th>
            </tr>
          </thead>
          <tbody id="subsBody"></tbody>
        </table>
      </div>
      <div class="actions">
        <button id="addSub">添加换人</button>
        <button id="confirmInfo" class="primary">确认并写入 match.yaml</button>
      </div>
    </section>
  </main>

  <script>
    const initial = __PAYLOAD__;
    const roleChoices = __ROLE_CHOICES__;
    const configPath = document.getElementById('configPath');
    const saveStatus = document.getElementById('saveStatus');
    const fields = {
      matchId: document.getElementById('matchId'),
      matchName: document.getElementById('matchName'),
      fieldLength: document.getElementById('fieldLength'),
      fieldWidth: document.getElementById('fieldWidth'),
      teamKey: document.getElementById('teamKey'),
      teamName: document.getElementById('teamName'),
      teamColor: document.getElementById('teamColor'),
      fieldColors: document.getElementById('fieldColors'),
      goalkeeperColors: document.getElementById('goalkeeperColors'),
      videoPath: document.getElementById('videoPath'),
      targetMetrics: document.getElementById('targetMetrics')
    };
    const playersBody = document.getElementById('playersBody');
    const subsBody = document.getElementById('subsBody');
    let players = [];
    let substitutions = [];

    function setStatus(message, className = '') {
      saveStatus.textContent = message;
      saveStatus.className = className;
    }

    function csv(values) {
      return Array.isArray(values) ? values.join(', ') : String(values || '');
    }

    function splitList(value) {
      return String(value || '').split(',').map(item => item.trim()).filter(Boolean);
    }

    function textCell(value, onChange, placeholder = '') {
      const input = document.createElement('input');
      input.value = value || '';
      input.placeholder = placeholder;
      input.addEventListener('input', () => onChange(input.value));
      return input;
    }

    function roleSelect(value, onChange) {
      const select = document.createElement('select');
      roleChoices.forEach(role => {
        const option = document.createElement('option');
        option.value = role;
        option.textContent = role;
        select.appendChild(option);
      });
      select.value = roleChoices.includes(value) ? value : 'substitute';
      select.addEventListener('change', () => onChange(select.value));
      return select;
    }

    function renderPlayers() {
      playersBody.innerHTML = '';
      players.forEach((player, index) => {
        const row = document.createElement('tr');
        const cells = [
          textCell(player.player_id, value => player.player_id = value, 'red_name'),
          textCell(player.name, value => player.name = value, 'HDA'),
          textCell(player.number, value => player.number = value, '1'),
          roleSelect(player.role, value => player.role = value),
          textCell(player.visual_hint, value => player.visual_hint = value, '颜色/号码/球鞋/发型')
        ];
        cells.forEach(cell => {
          const td = document.createElement('td');
          td.appendChild(cell);
          row.appendChild(td);
        });
        const action = document.createElement('td');
        const remove = document.createElement('button');
        remove.textContent = '删除';
        remove.className = 'danger';
        remove.addEventListener('click', () => {
          players.splice(index, 1);
          renderPlayers();
        });
        action.appendChild(remove);
        row.appendChild(action);
        playersBody.appendChild(row);
      });
    }

    function renderSubs() {
      subsBody.innerHTML = '';
      substitutions.forEach((sub, index) => {
        const row = document.createElement('tr');
        const cells = [
          textCell(sub.at, value => sub.at = value, '05:00'),
          textCell(sub.out, value => sub.out = value, 'LXY'),
          textCell(sub.in, value => sub.in = value, 'SUB'),
          textCell(sub.note, value => sub.note = value, '')
        ];
        cells.forEach(cell => {
          const td = document.createElement('td');
          td.appendChild(cell);
          row.appendChild(td);
        });
        const action = document.createElement('td');
        const remove = document.createElement('button');
        remove.textContent = '删除';
        remove.className = 'danger';
        remove.addEventListener('click', () => {
          substitutions.splice(index, 1);
          renderSubs();
        });
        action.appendChild(remove);
        row.appendChild(action);
        subsBody.appendChild(row);
      });
    }

    function loadInitial() {
      configPath.textContent = initial.config_path;
      fields.matchId.value = initial.match.id || '';
      fields.matchName.value = initial.match.name || '';
      fields.fieldLength.value = initial.field.length_m || 40;
      fields.fieldWidth.value = initial.field.width_m || 20;
      fields.teamKey.value = initial.team.key || 'red';
      fields.teamName.value = initial.team.display_name || '';
      fields.teamColor.value = initial.team.team_color || '';
      fields.fieldColors.value = csv(initial.team.field_colors);
      fields.goalkeeperColors.value = csv(initial.team.goalkeeper_colors);
      fields.videoPath.value = initial.match.video_path || '';
      fields.targetMetrics.value = csv(initial.analysis.target_metrics);
      players = JSON.parse(JSON.stringify(initial.team.players || []));
      substitutions = JSON.parse(JSON.stringify(initial.team.substitutions || []));
      renderPlayers();
      renderSubs();
      if (initial.review.match_info_status === 'confirmed') {
        setStatus(initial.review.match_info_confirmed_at ? `已确认: ${initial.review.match_info_confirmed_at}` : '已确认', 'saved');
      }
    }

    function collectPayload() {
      return {
        match: { name: fields.matchName.value.trim() },
        field: {
          length_m: Number(fields.fieldLength.value || 40),
          width_m: Number(fields.fieldWidth.value || 20)
        },
        team: {
          key: fields.teamKey.value.trim(),
          display_name: fields.teamName.value.trim(),
          team_color: fields.teamColor.value.trim(),
          field_colors: splitList(fields.fieldColors.value),
          goalkeeper_colors: splitList(fields.goalkeeperColors.value),
          players,
          substitutions
        },
        analysis: {
          target_metrics: splitList(fields.targetMetrics.value)
        }
      };
    }

    async function confirmInfo() {
      const payload = collectPayload();
      if (!payload.team.players.some(player => String(player.name || '').trim())) {
        setStatus('确认失败: 至少需要一名球员', 'failed');
        return;
      }
      if (!payload.analysis.target_metrics.length) {
        setStatus('确认失败: 至少需要一个指标', 'failed');
        return;
      }
      setStatus('写入中...');
      try {
        const response = await fetch('/api/match_info', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok || !result.ok) {
          throw new Error(result.error || `HTTP ${response.status}`);
        }
        setStatus(`已确认: ${result.confirmed_at}`, 'saved');
      } catch (error) {
        setStatus(`确认失败: ${error.message}`, 'failed');
      }
    }

    document.getElementById('addPlayer').addEventListener('click', () => {
      const teamKey = fields.teamKey.value.trim() || 'team';
      players.push({ player_id: `${teamKey}_new`, name: '', number: '', role: 'substitute', visual_hint: '' });
      renderPlayers();
    });
    document.getElementById('addSub').addEventListener('click', () => {
      substitutions.push({ at: '', out: '', in: '', note: '' });
      renderSubs();
    });
    document.getElementById('confirmInfo').addEventListener('click', confirmInfo);
    loadInitial();
  </script>
</body>
</html>
"""
    return template.replace("__PAYLOAD__", payload).replace("__ROLE_CHOICES__", role_choices)


def parse_image_xy(value: Any) -> Optional[Tuple[int, int]]:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("image_xy must be null or a two-item list")
    x, y = int(value[0]), int(value[1])
    if x < 0 or y < 0:
        raise ValueError("image_xy values must be non-negative")
    return x, y


class PointPickerServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: Tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        config_path: Path,
        points_path: Path,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.config_path = config_path
        self.points_path = points_path


class Handler(BaseHTTPRequestHandler):
    server: PointPickerServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/match", "/match_info.html"}:
            config = load_yaml(self.server.config_path)
            html = build_match_info_html(config, self.server.config_path)
            payload = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if parsed.path not in {"/", "/point_picker.html"}:
            self.send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        config = load_yaml(self.server.config_path)
        points_yaml = load_yaml(self.server.points_path)
        image_path = resolve_path(points_yaml["calibration"]["image_path"])
        html = build_html(
            image_path,
            point_payload(points_yaml),
            autosave=True,
            save_endpoint="/api/point",
            submit_endpoint="/api/submit",
            points_label=str(self.server.points_path),
            labeling_status=str(points_yaml.get("calibration", {}).get("labeling_status") or ""),
            labeling_submitted_at=str(points_yaml.get("calibration", {}).get("labeling_submitted_at") or ""),
        )
        payload = html.encode("utf-8")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/match_info":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body)
                result = update_match_info(self.server.config_path, payload)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"ok": True, **result})
            return

        if parsed.path == "/api/submit":
            try:
                result = submit_labeling(self.server.points_path)
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json({"ok": True, **result})
            return

        if parsed.path != "/api/point":
            self.send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body)
            point_name = str(payload["name"])
            image_xy = parse_image_xy(payload.get("image_xy"))
            update_point_image_xy(self.server.points_path, point_name, image_xy)
            mark_submission_dirty(self.server.points_path)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self.send_json({"ok": True, "name": point_name, "image_xy": image_xy})

    def send_json(self, payload: Dict[str, Any], status: Union[int, HTTPStatus] = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default="configs/match_red_mvp.yaml")
    parser.add_argument("--points", default=None)
    args = parser.parse_args()

    config_path = resolve_path(args.config)
    config = load_yaml(config_path)
    configured_points = config.get("calibration", {}).get("points_path")
    points_path = resolve_path(args.points or configured_points or "configs/calibration_points_red_mvp.yaml")
    server = PointPickerServer((args.host, args.port), Handler, config_path, points_path)
    print(f"Serving point picker at http://{args.host}:{args.port}/")
    print(f"Match information page at http://{args.host}:{args.port}/match")
    print(f"Auto-saving points to {points_path}")
    server.serve_forever()


if __name__ == "__main__":
    main()
