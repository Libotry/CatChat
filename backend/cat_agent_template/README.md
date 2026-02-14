# Cat Agent Template

这是猫猫子Agent参考实现（独立进程）。

## 启动

```bash
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 9001
```

可通过环境变量配置模型标识：

```bash
MODEL_TYPE=qwen-max uvicorn main:app --host 127.0.0.1 --port 9001
```

## 通信协议

- `POST /act`
  - 入参：`session_id/player_id/role/phase/visible_state/prompt_template`
  - 出参：`action/reasoning/timestamp`

注意：此模板仅示例规则动作生成，真实场景请在该进程内接入你自己的大模型 API（密钥只保存在子Agent进程内）。
