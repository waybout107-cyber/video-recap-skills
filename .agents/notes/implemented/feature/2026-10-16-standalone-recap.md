# Agent Note: 单 key 的独立自动解说入口

Status: implemented

## Problem

用户明确要求视频路径和目标时长即可自动出片；现有 Agent 工作流暂停等待人工创作产物，不能独立运行。

## Decision

在仓库 scripts 下新增独立的标准库 CLI，使用 MiMo 做 ASR、画面理解、选段和写稿，本地 ffmpeg 做检测与渲染。此请求是对原先禁止 CLI 创作的明确产品例外（参见 architecture/2026-05-18-agent-owned-narration-cli-mechanical.md），不修改七个已发布技能的契约。复用既有剪映导出子进程和时间线 v2 契约。提供完全离线的合成示例，明确它只验证媒体管线，不模拟真实配音质量。

## Alternatives considered

- 只提供现有技能说明：复用最充分，但仍需要 Agent 主持每次制作，不能满足单 key 独立命令的要求。
- 修改现有技能的暂停点：可复用编排，但会改变已发布的创作所有权和续跑契约；采用隔离入口。

## Consequences

增加独立 API 适配和媒体流程的维护成本；自动脚本仍需用户核对事实。所有输入分析与输出计划保留，拒绝覆盖已有目录，不做隐式断点复用。

## Mobile browser extension

Android 用户无法使用终端，且平台环境管理被席位权限挡住。使用标准库 `auto_web.py` 与移动端页面，上传本地视频、填写单次任务的 API key 并调用独立 CLI。默认只监听回环地址；云预览显式绑定 0.0.0.0。key 仅放在子进程环境、不写文件或日志、不回传浏览器；每个页面持有随机会话 token，上传与作业状态隔离，成片用不可猜作业 ID 下载。提供离线示例、进度与文件下载，不改平台权限。

共享 Chromium 无法解码 H.264/AAC 的构建通过可选的低分辨率 WebM 预览回退，MP4 仍为正式下载成片；通过真实浏览器播放验证后采用。

## Render deployment

用户要求协助建立独立托管账号；准备 Docker/Render Blueprint 和手机操作说明，不代用户创建账号、接受计费或部署。容器包含 Python 3.12、Debian ffmpeg 与 CJK 字体，以非 root 用户运行。公网页面由 `RECAP_WEB_PASSWORD` HTTP Basic 登录保护，健康检查不需要登录。容器启动必须设置网站密码；MiMo key 仍按单次请求输入。Blueprint 默认免费实例，仅用于小样例验证，明确免费重启及临时文件丢失边界。账号登录、GitHub 授权和最终部署由用户在手机确认。
