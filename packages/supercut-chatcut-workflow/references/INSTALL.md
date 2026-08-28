# 安装与导入

## Codex 直接导入

导入 `supercut-chatcut-workflow-1.2.0.zip`。首次使用以下任一触发词时，Skill 会自动运行首启程序并激活内置配置：

- `执行超级剪辑终版工作流`
- `按终版工作流剪辑`
- `参考片复刻终版`
- `原创规划终版`
- `$supercut-chatcut-workflow`

内置配置可离线使用；发布者配置 HTTPS 版本清单后，首启程序会自动下载并校验更新。

## Windows 脚本安装

在解压后的发布目录运行：

```powershell
.\install.ps1 -Source ".\supercut-chatcut-workflow-1.2.0.zip"
```

从 HTTPS 地址安装时必须提供发布清单中的 SHA-256：

```powershell
python .\install_workflow.py --source "https://example.invalid/supercut-chatcut-workflow-1.2.0.zip" --sha256 "发布清单中的64位SHA-256"
```

安装器把旧版本移动为带时间戳的备份，然后运行配置首启程序。默认 Skill 目录为 `$CODEX_HOME/skills`（若设置）或 `~/.codex/skills`；默认配置目录为 `~/.supercut-chatcut`。

## 验证

```powershell
python "已安装Skill目录\scripts\bootstrap.py" --ensure --json
```

输出必须包含 `status: ready`、`version`、`active_path` 和已验证的 `sha256`。随后在 Codex 中使用触发词，并提供持有使用权的素材。

## 发布者须知

当前包不虚构公网下载地址。若要实现团队的一键在线更新，先把配置 ZIP 上传到真实 HTTPS 地址，再按 `references/CONFIGURATION.md` 更新 URL 和摘要并重新构建。
