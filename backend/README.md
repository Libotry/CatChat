# Werewolf AI Backend (God-Agent Orchestrated)

基于 Python 3.10+ / FastAPI 的狼人杀后端，支持 **8~12猫猫全AI对战**：
- 后端是唯一可信源（上帝Agent）
- 猫猫子Agent仅负责决策生成
- 所有状态变更由后端规则引擎裁定

## 核心模块

- `app/room/room_manager.py`：房间生命周期、多房间并发、AI房间与Agent注册
- `app/engine/game_engine.py`：状态机 + 规则引擎（夜晚结算/投票/胜利判定）
- `app/roles/skills.py`：角色技能策略池
- `app/agent/agent_scheduler.py`：上帝调度核心（重试/熔断/超时/审计/指标）
- `app/agent/perspective_engine.py`：角色视角过滤器（防信息泄露）
- `app/agent/fallback_strategies.py`：超时/错误兜底策略（支持配置模板）
- `app/agent/god_orchestrator.py`：AI驱动流程编排（夜晚/白天/猎人触发）
- `app/storage/repository.py`：SQLite落库与复盘数据持久化
- `cat_agent_template/`：猫猫子Agent参考实现

## 游戏规则（已实现）

- 配置驱动局：8~12人模板（`config/role_balance.yaml`）
- 11人模板含白痴（`fool`）：白天被放逐时翻牌免死一次，随后失去投票权
- 胜利判定：
  - 狼人胜：存活狼人 >= 存活好人
  - 好人胜：狼人全灭
- 守卫：不可连续守同一人；同守同刀守护生效
- 女巫：首夜可自救；解药/毒药各一次；毒药无视守护
- 猎人：被狼杀/放逐可开枪，被毒杀不可开枪
- 狼队：多狼投票最高票目标，平票随机

## AI调度特性

- 视角隔离：按角色/阶段过滤可见状态
- 超时熔断：默认15秒；连续2次失败自动离线托管
- 错误降级：回退 `fallback_strategies`
- 发言沙箱：响应长度限制 + 敏感词脱敏
- 可观测性：平均响应时延、超时率、错误类型分布
- 审计日志：记录请求/响应（脱敏）

## 快速启动

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

服务默认启动：`http://127.0.0.1:8000`

- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`GET /health`

## AI模式接口（关键）

- `POST /api/ai/rooms`：创建AI房间（支持 `player_count`、`custom_role_config`）
- `POST /api/ai/rooms/{room_id}/agents/register`：注册猫猫子Agent
- `POST /api/ai/rooms/{room_id}/agents/hot-swap`：热替换故障猫猫
- `GET /api/ai/rooms/{room_id}/agents/health`：查看在线/托管/指标
- `POST /api/ai/rooms/{room_id}/run-phase`：上帝推进一个阶段
- `POST /api/ai/rooms/{room_id}/run-to-end`：自动运行到结算
- `GET /api/replay/{record_id}`：读取结构化复盘包

## 猫猫子Agent模板

目录：`cat_agent_template/`

```bash
cd backend/cat_agent_template
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 9101
```

`POST /act` 输入：`session_id/player_id/role/phase/visible_state/prompt_template`

## 一键联调（8~12猫猫 + 上帝）

```powershell
cd backend
./scripts/start_ai_battle.ps1
# 8人局示例
./scripts/start_ai_battle.ps1 -PlayerCount 8
```

脚本会：
1. 按配置启动猫猫子Agent进程
2. 创建AI房间
3. 注册全部Agent
4. 开局
5. 触发上帝Agent自动跑到结算

## 测试

```bash
cd backend
pytest -q
```

- `tests/test_night_resolution.py`：夜晚结算核心规则
- `tests/test_perspective_engine.py`：视角隔离规则
- `tests/test_dynamic_rules.py`：动态人数与白痴规则

## 复盘包说明

游戏结束后会写入SQLite，复盘包包含：
- `audit`：规则引擎行为日志
- `agent_audit`：猫猫请求/响应与fallback记录
- `metrics`：调度指标
- `players`：最终角色与存活状态