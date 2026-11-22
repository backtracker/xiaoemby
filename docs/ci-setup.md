# CI 镜像构建配置（维护者）

本文档面向维护者，说明 GitHub Actions 镜像构建流水线的权限配置。普通用户请直接看 README 的「Docker 运行」章节拉取现成镜像。

## 触发条件

`.github/workflows/docker.yml` 在以下情况自动构建并推送镜像到 `ghcr.io/backtracker/xiaoemby`：

- push 到 `master` / `main`
- push tag（`v*` 或数字开头）
- 手动触发（workflow_dispatch）

PR 只构建不推送。镜像 tag 包含分支名、commit short sha、`latest`（默认分支）。

## GHCR 登录权限（二选一）

CI 需要 GHCR 的写权限才能推送镜像。

### 方式一：内置 GITHUB_TOKEN

仓库 **Settings → Actions → General → Workflow permissions** 选 **Read and write permissions**，保存。workflow 用内置的 `${{ secrets.GITHUB_TOKEN }}` 登录。

### 方式二：Personal Access Token

若上述设置不可见或受限：

1. 头像 → **Settings → Developer settings → Personal access tokens → Tokens (classic)** → **Generate new token**
2. 勾选 `write:packages`、`repo`（公开仓库可只勾 `write:packages`）
3. 复制生成的 token
4. 仓库 **Settings → Secrets and variables → Actions** → **New repository secret**
   - Name: `GHCR_TOKEN`
   - Value: 粘贴 token

workflow 里的登录密码用 `${{ secrets.GHCR_TOKEN || secrets.GITHUB_TOKEN }}`，配了 PAT 就优先用 PAT，否则回退内置 token。

## 验证

推送后可查看 Actions 运行状态：

```shell
gh run list --repo backtracker/xiaoemby
gh run view <run-id> --repo backtracker/xiaoemby --log
```

成功日志里会有 `pushing manifest for ghcr.io/backtracker/xiaoemby:latest`。
