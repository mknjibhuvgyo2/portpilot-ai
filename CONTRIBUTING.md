# Contributing to PORTHUB / 贡献指南

感谢你愿意参与！Thanks for helping out. This guide keeps contributions smooth and consistent.

## 开始之前 / Before you start

- 小修复（typo、明显 bug）可直接提 PR。
- 较大改动（新功能、新应用模板、新厂商、架构变更）请先开一个 **Issue** 讨论方向，避免白做。
- 遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 开发环境 / Dev setup

要求：Python 3.12+、Node 20+（推荐 22）。

```bash
# 后端
cd backend
python -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt    # Windows: ./.venv/Scripts/python
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev      # http://localhost:5173 (代理 /api、/gw 到 :8000)
```

也可用 `./scripts/start.sh dev` / `./scripts/start.ps1 -Dev` 一键起开发环境。

## 提交前检查 / Before you push

请确保以下全部通过：

```bash
# 后端测试（新增/改动逻辑请附测试）
cd backend && pytest -q

# 前端类型检查 + 构建
cd frontend && npm run build
```

可选：本地 Docker 构建验证 `docker build -t porthub-ci .`。

## 代码风格 / Style

- **后端**：Python，类型注解齐全；保持现有模块边界（`models_layer` / `apps` / `ports` / `gateway` / `monitor` / `billing` …）。新 provider 实现 `BaseProvider`，新应用模板继承 `AppTemplate` 并在 `apps/registry.py` 注册。
- **前端**：Vue 3 `<script setup>` + TypeScript + TailwindCSS；图标用自绘的 `WaIcon.vue`（不引入图标库）；新增文案补齐 `i18n/{zh,en,ja}.ts` 三语。
- 不提交 `backend/.env`、`data/`、`node_modules/`、`dist/`、`.venv/`（已在 `.gitignore`）。

## 提交规范 / Commits & PR

- 提交信息建议用 [Conventional Commits](https://www.conventionalcommits.org/)：`feat: ...`、`fix: ...`、`docs: ...`、`refactor: ...`、`test: ...`、`chore: ...`。
- 一个 PR 聚焦一件事；描述清楚动机、做法、验证方式（贴测试/构建结果）。
- PR 请填写 [Pull Request 模板](.github/PULL_REQUEST_TEMPLATE.md) 的勾选项。
- 关联 Issue 用 `Closes #123`。

## 加新东西的快速指引

- **新模型厂商**：`backend/app/models_layer/providers/` 加适配器；如需预设，补 `models_layer/presets.py`；前端 `i18n` 加厂商名。
- **新应用模板**：`backend/app/apps/` 加模板 + 注册；前端端口编辑器会自动列出。
- **新 i18n 文案**：三语 `zh/en/ja` 同步，避免缺键。

## 行为准则 / License

提交即表示你同意你的贡献以本项目的 [GPL-3.0](LICENSE) 许可发布。
