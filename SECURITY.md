# Security Policy / 安全策略

## 支持的版本 / Supported versions

项目处于早期活跃开发期，安全修复仅针对**最新发布版本与 `main` 分支**。

| Version | Supported |
| ------- | --------- |
| latest `main` / newest release | ✅ |
| older | ❌ |

## 报告漏洞 / Reporting a vulnerability

**请勿在公开 Issue 中披露安全漏洞。**

请通过以下任一私密渠道报告：

- GitHub **Security Advisories**：仓库 → *Security* → *Report a vulnerability*（推荐）。
- 或邮件联系维护者：`15510677351@163.com`。

报告请尽量包含：

- 受影响的组件 / 版本 / 部署方式（Docker、源码…）。
- 复现步骤或 PoC。
- 影响评估（数据泄露、提权、RCE 等）。

我们会在 **3 个工作日内**确认收到，并在合理时间内修复；修复发布后会在 Release Notes 致谢（如你愿意）。

## 部署安全须知 / Hardening notes

- **修改默认管理员密码**：用 `HUB_ADMIN_PASSWORD` 设定（不设则首启随机生成、打印一次）。
- **固定 JWT 密钥**：设置 `HUB_SECRET_KEY`（留空会自动生成并存到 `data/secret.key`）。
- **保护 `data/` 目录**：含 `secret.key`、`hub.db`（用户、API Key 哈希、配置）。切勿提交或公开。
- **网关鉴权**：对需要保护的端口启用「网关需 API Key」，客户端走 `/gw/<slug>/`；直连端口不校验鉴权、不计用量。
- **公网暴露**：务必置于反向代理（Nginx/Caddy，设置页可导出配置）+ TLS 之后，限制可访问的端口范围。
- **本地引擎**：Ollama / LM Studio / llama.cpp 由用户自行启动并保证其访问控制；平台只连接 `base_url`。

## 已知设计权衡 / Known trade-offs

- 流式请求在无上游 usage 时按字符估算 token（成本统计为近似）。
- API Key 以 SHA-256 哈希存储，仅在创建时显示一次明文。
