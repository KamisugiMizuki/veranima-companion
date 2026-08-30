# Veranima Windows 开箱即用发行版设计方案

> **状态：仅设计，不实现。**
>
> 本文记录 Windows 家用机发行版的目标、边界、目录结构、初始化流程、资源策略、隐私清理和验收标准。未在本轮修改现有运行链。

## 1. 目标

构建一个面向 Windows 10/11 x64 家用机的 Veranima 私有发行包，使没有 Python、Node.js、Git、项目虚拟环境或开发机环境变量的电脑用户，能够通过安装器和首次初始化向导完成：

- 桌宠 GUI 启动；
- 远程 LLM 文字对话；
- 本地记忆；
- 角色卡与角色切换；
- 可选 QQ OneBot/NapCat 接入；
- 可选 SenseVoice STT；
- 可选 GPT-SoVITS TTS；
- 可选视觉注意力；
- 可选 Style Learning。

发行包不上传 GitHub，可通过百度网盘等方式分发。GitHub 仓库仍只保留源代码、模板、文档和不含个人数据的发行辅助脚本，不提交发行包、模型、用户数据或凭据。

## 2. 已确认的产品决策

以下选项均采用：

1. 发行形态采用 `Setup.exe + 首次初始化器 + 可选模块下载`。
2. QQ、STT、TTS、视觉注意力、Style Learning 均为可选模块。
3. GPT-SoVITS 不随主包提供完整 runtime 和模型，支持自动下载或选择已有目录；允许跳过。
4. 目标平台只考虑 Windows 10/11 家用机，不支持 Linux/macOS。
5. 角色卡和人物资源可以随包保留，但必须移除个人隐私和本机演化状态。
6. 不把当前电脑的聊天、记忆、API Key、QQ 白名单、日志、Style Learning 语料或产物带入发行包。
7. 用户数据与程序文件分离，用户数据默认位于 `%LOCALAPPDATA%\\Veranima`。
8. 发行版默认启用文字聊天和本地记忆；视觉注意力默认关闭；STT/TTS/QQ 由向导选择。

## 3. 当前项目盘点结论

当前工作区的主要体量：

| 目录 | 当前约占用 | 发行策略 |
| --- | ---: | --- |
| `data/models/` | 约 16.6 GiB | 不随主包提供，由初始化器按模块下载 |
| `tts/` | 约 16.2 GiB | 不随主包提供，作为 GPT-SoVITS 外部模块 |
| `pet/node_modules/` | 约 276 MiB | 随发行包提供或单独下载 Electron runtime |
| `characters/` | 约 242 MiB | 保留公开角色资源，清理个人状态和隐私 |
| `dsh/` | 约 211 MiB | 当前不属于桌宠主链，发行版移除 |
| `tests/` | 约 2.6 MiB | 发行版移除，开发包保留 |
| `logs/` | 约 870 KiB | 不打包 |
| `assets/` | 约 15 KiB | 先审计 fallback 引用，确认角色卡完整后再决定是否移除 |

当前启动链依赖项目 `.venv`、Electron、Python sidecar 和本地目录结构，不能直接作为新环境发行方案。必须先完成运行时和用户数据路径解耦。

## 4. 发行目录设计

建议最终发行目录：

```text
Veranima/
├─ Start/
│  ├─ 首次初始化.exe
│  ├─ 启动 Veranima.vbs
│  ├─ 修复环境.bat
│  └─ 卸载本地数据.bat
├─ app/
│  ├─ src/
│  ├─ pet/
│  ├─ characters/
│  ├─ config/
│  │  ├─ config.example.yaml
│  │  └─ character.example.json
│  ├─ scripts/
│  ├─ pyproject.toml
│  └─ README_USER.md
├─ runtime/
│  ├─ python/
│  └─ node/
├─ resources/
│  ├─ electron/
│  └─ frontend-deps/
├─ models/
│  ├─ sensevoice/
│  ├─ vad/
│  └─ bge-m3/
├─ external/
│  └─ gpt-sovits/
├─ user_data/
│  ├─ config.yaml
│  ├─ memory/
│  ├─ chat/
│  ├─ logs/
│  └─ style/
├─ setup/
│  ├─ manifest.json
│  ├─ hashes.json
│  ├─ download_sources.json
│  └─ licenses/
└─ VERSION
```

程序目录和用户目录必须分离：

```text
程序：安装目录\\Veranima\\app
用户数据：%LOCALAPPDATA%\\Veranima
```

不应让普通用户依赖程序目录写权限，也不应把数据库、日志、聊天记录和模型状态写入安装目录。

## 5. 运行时路径改造目标

实现阶段需要新增统一路径层，避免代码各处直接拼项目根目录：

- `VERANIMA_HOME`：用户数据根目录，可由环境变量覆盖；
- `VERANIMA_APP_ROOT`：只读程序目录；
- `VERANIMA_MODEL_ROOT`：模型目录；
- `VERANIMA_EXTERNAL_ROOT`：外部组件目录；
- `VERANIMA_LOG_ROOT`：日志目录。

预计涉及：

- `src/veranima/config.py`；
- `src/veranima/app.py`；
- `src/veranima/pet_server.py`；
- `src/veranima/qq.py`；
- `src/veranima/adapters/qq.py`；
- `pet/main.js`；
- `scripts/run_pet.py`；
- 相关测试和启动脚本。

迁移规则：

```text
角色卡、内置模板、程序资源 → app/
用户配置、数据库、聊天、日志、Style 产物 → %LOCALAPPDATA%\\Veranima
模型 → %LOCALAPPDATA%\\Veranima\\models 或发行目录 models
外部 GPT-SoVITS → %LOCALAPPDATA%\\Veranima\\external\\gpt-sovits
```

必须提供旧目录到新目录的迁移策略，并避免数据库中保存开发机绝对路径。

## 6. 安装器、初始化器和启动器分工

### 6.1 安装器

建议使用 Inno Setup 或 NSIS：

- 安装程序文件；
- 检查/安装必要的 VC++ runtime 和 WebView2；
- 创建开始菜单和桌面快捷方式；
- 创建 `%LOCALAPPDATA%\\Veranima`；
- 启动首次初始化器；
- 卸载时让用户选择是否删除用户数据。

安装器不负责下载十几 GiB 模型，也不负责填写 API Key。

### 6.2 首次初始化器

初始化器负责：

1. 检查 Windows 版本、架构、磁盘空间和目录写权限；
2. 检查端口 `8765`、`9880`、`9890`、`8099`；
3. 检查 CPU、内存、GPU、显存和麦克风；
4. 检查网络和 ModelScope 可访问性；
5. 选择功能模块；
6. 下载并校验模型；
7. 选择已有 GPT-SoVITS 目录或按需下载；
8. 生成用户配置；
9. 测试远程 LLM；
10. 创建快捷方式并提供启动按钮。

初始化器应支持：

```text
download / resume / verify / repair / skip
```

所有下载必须：

- 支持断点续传；
- 下载到临时文件；
- 显示进度和剩余空间；
- 完成后校验 SHA-256；
- 校验通过后原子移动；
- 失败后清理临时文件；
- 不把不完整目录标记为可用。

### 6.3 启动器

最终用户只需要双击：

```text
启动 Veranima.vbs
```

启动顺序：

```text
VBS hidden
→ 自带 Python launcher
→ Electron shell
→ Python core
→ 可选 STT/TTS/QQ
```

不能要求用户安装 Python、Node、Git 或手动激活 venv。

## 7. 功能模块与资源策略

### 7.1 核心文字聊天

必须默认可运行：

- 内置 Python runtime；
- 内置项目依赖；
- 远程 OpenAI 兼容 LLM；
- 不依赖本地 LLM；
- 不依赖 GPU。

首次向导或设置页填写：

- Base URL；
- Model；
- API Key。

API Key 只写入本地用户配置，读取打码，禁止进入日志、诊断报告和发行包。

### 7.2 本地记忆与 BGE-M3

BGE-M3 作为可选下载模块，优先使用 ModelScope，并提供手动导入目录作为备用：

```json
{
  "name": "BAAI/bge-m3",
  "source": "modelscope",
  "revision": "固定版本",
  "sha256": "固定校验值",
  "size": "实际大小"
}
```

模型目录存在不代表可用，启动前必须验证关键文件和校验值。

### 7.3 SenseVoice STT

STT 作为独立可选模块：

```text
models/sensevoice/
models/vad/
runtime/stt-overlay/
```

不得把 STT 依赖装进 GPT-SoVITS runtime，也不得污染用户已有 Python。

初始化器检查：

- 是否存在麦克风；
- SenseVoice 模型是否完整；
- FSMN-VAD 是否完整；
- STT overlay 是否完整；
- 9890 端口是否可用。

麦克风选择保存：

```text
input_device_id
input_device_label_snapshot
```

换电脑后：

- ID 存在则使用；
- ID 消失但同名设备存在则提示确认；
- 都不存在则回退系统默认麦克风。

### 7.4 GPT-SoVITS TTS

GPT-SoVITS 不随主包提供，支持：

```text
自动下载固定版本整合包
选择用户已有 GPT-SoVITS 目录
跳过 TTS
```

初始化器校验：

- `runtime/python.exe`；
- `api_v2.py`；
- 关键模型目录；
- 版本和许可证；
- 声音参考文件；
- 端口 9880。

必须准备主下载源和手动导入备用方案，不能让单个失效网盘链接成为唯一依赖。不得未经许可证核对就复制 Sakura 的 runtime、模型或私有训练资源。

### 7.5 QQ

QQ 作为可选模块：

- 不要求初始化器自动配置 NapCat；
- 提供 NapCat 连接说明；
- 设置页填写 WS 地址、端口、白名单和 token；
- 默认白名单为空；
- 不携带当前用户 QQ 号。

### 7.6 视觉注意力

视觉注意力默认关闭，首次启用时必须明确告知：

- 会读取屏幕局部内容；
- 可能发送给远程多模态模型；
- 敏感窗口会被策略拦截；
- 原始截图不应持久化。

启用条件还包括远程模型确实支持多模态输入。

### 7.7 Style Learning

保留功能但默认关闭，不附带任何语料：

```text
Style Learning 产物 → %LOCALAPPDATA%\\Veranima\\style
原文和语料库 → 用户自行导入的本地路径或私有目录
```

发行包不得包含：

- 当前用户语料；
- 《三体》语料；
- `style.json`；
- corpus、review queue、profile；
- 当前用户的学习画像。

## 8. 角色卡与隐私清理

可随包保留 `characters/yuki`、`characters/zima` 及其公开角色资源，但发行前必须扫描：

- API key、token；
- QQ 号；
- 手机号、邮箱；
- 真实姓名、地址；
- 用户关系和个人事实；
- 本机绝对路径；
- 聊天台词和数据库导出；
- 私人语音和录音 metadata。

角色运行状态重置为发行版默认状态，不携带本机演化结果，例如：

```json
{
  "state": {
    "affection": 0.5,
    "attachment": 0.5
  }
}
```

发行前生成 `release_audit.json`，安全扫描失败则禁止打包。

## 9. 清理与保留判断

### 可以从发行包移除

- `tests/`；
- 完整开发文档，仅保留用户文档和许可证；
- `.git/`、`.venv/`、`.pytest_cache/`；
- `logs/`；
- `data/` 下全部当前机器数据；
- `dsh/`，除非明确发行 M5 桌面 Agent；
- Qwen3-TTS 模型和已废弃 Qwen TTS runtime，前提是完成全仓调用链扫描并同步清理文档和配置。

### 暂不应直接移除

`assets/` 当前体积很小，但可能仍是角色卡缺少表达式时的 fallback。应先：

1. 扫描所有运行时引用；
2. 确认所有发行角色卡均提供完整 expressions；
3. 删除 fallback 代码和测试；
4. 完成干净环境启动验证；
5. 再决定是否从发行包移除。

不能只根据目录名判断它“应该没用”。

## 10. 下载清单与版本锁定

每个外部模块都有 manifest：

```json
{
  "id": "sensevoice-small",
  "version": "固定版本",
  "source": "modelscope",
  "url": "固定下载地址",
  "sha256": "固定值",
  "size_bytes": 0,
  "license": "许可证链接",
  "required_files": ["..."],
  "optional": true
}
```

至少覆盖：

- Electron runtime；
- Python runtime；
- BGE-M3；
- SenseVoice；
- FSMN-VAD；
- GPT-SoVITS；
- WebView2/VC++ runtime。

## 11. 用户首次启动流程

```text
安装 Setup.exe
→ 选择安装目录
→ 首次初始化器检查环境
→ 选择功能模块
→ 配置 LLM
→ 下载 BGE-M3（可跳过，记忆降级）
→ 检查麦克风并配置 STT（可跳过）
→ 选择 GPT-SoVITS 目录/下载/跳过
→ 配置 QQ（可跳过）
→ 视觉注意力默认关闭，明确选择后再开
→ 生成 %LOCALAPPDATA%\\Veranima\\config.yaml
→ 执行模块健康检查
→ 创建快捷方式
→ 启动 Veranima
```

## 12. 健康检查与修复

发行版需要提供用户可执行的“修复环境”：

- 检查 runtime；
- 检查模型完整性；
- 检查端口；
- 检查配置 YAML；
- 检查角色卡 JSON；
- 检查 Electron 依赖；
- 检查 GPT-SoVITS；
- 检查麦克风；
- 检查远程 LLM；
- 重新下载损坏文件。

健康检查输出要面向小白：

```text
[通过] Python runtime
[通过] 桌宠界面
[缺少] SenseVoice 模型
[跳过] GPT-SoVITS
[未配置] LLM API Key
[建议] 选择一个麦克风
```

不要直接输出 traceback 作为唯一错误信息。

## 13. 发行前隐私审计

构建发行包前必须 fail-closed 扫描：

- `config/config.yaml`；
- `.env`；
- SQLite 数据库；
- `data/`；
- `logs/`；
- `chat.json`；
- Style Learning 目录；
- 角色卡和 Markdown；
- 音频 metadata；
- 文件名和路径；
- API key/token/QQ/邮箱/手机号。

检查目标：

```text
无凭据
无聊天记录
无个人数据库
无个人 QQ
无本机绝对路径
无模型私有训练产物
无被忽略目录误进入发行包
```

## 14. 干净 Windows 验收

必须在没有开发环境的 Windows 10/11 虚拟机或测试机上验证：

- 无 Python；
- 无 Node.js；
- 无 Git；
- 无项目 `.venv`；
- 无开发机环境变量；
- 无当前机器用户数据。

验收矩阵：

| 功能 | 必须通过条件 |
| --- | --- |
| 安装器 | 安装、卸载、快捷方式正常 |
| 初始化器 | 检测、选择、下载、断点续传、校验正常 |
| 文字聊天 | 只填远程 LLM 即可回复 |
| 角色 | 立绘、角色切换、默认状态正常 |
| 记忆 | BGE-M3 存在时可写入/召回，缺失时有明确降级 |
| STT | 选择麦克风后能录音、转写、回填输入框 |
| TTS | GPT-SoVITS 已安装时播放；未安装时文字降级 |
| QQ | NapCat 配置后收发正常；未配置不影响桌宠 |
| 视觉 | 默认关闭；开启时隐私策略和主动闸门正常 |
| Style Learning | 默认无产物；用户导入后只写本地用户目录 |
| 重启 | 核心、TTS、STT、QQ 子进程生命周期正常 |
| 用户数据 | 升级和卸载可选择保留/删除 |

## 15. 推荐实施顺序（未来执行时）

### Phase 0：发行内容冻结

- 冻结发行功能范围；
- 冻结角色卡；
- 确认 GPT-SoVITS 版本和许可证；
- 确认 ModelScope 模型版本；
- 确认是否移除 dsh、Qwen TTS 和旧资源。

### Phase 1：路径与数据隔离

- 实现 app root / user root / model root / external root；
- 迁移配置、数据库、聊天、日志、Style 产物；
- 增加迁移测试和旧路径负向测试。

### Phase 2：发行前清理

- 全仓 import/调用扫描；
- 移除 Qwen TTS 无用链；
- 清理个人角色状态和资源；
- 清理 dsh、tests、开发缓存；
- 审计 assets fallback。

### Phase 3：内置 Windows runtime

- Python runtime；
- Node/Electron runtime；
- VC++/WebView2 检查；
- 无系统 Python/Node 启动测试。

### Phase 4：模块 manifest 与初始化器

- manifest、下载、断点续传、hash 校验；
- BGE-M3、SenseVoice、VAD、GPT-SoVITS 选装；
- 失败恢复和修复入口。

### Phase 5：首次启动向导

- 功能选择；
- LLM 配置；
- 麦克风选择；
- TTS 目录选择；
- QQ 配置；
- 视觉隐私确认；
- 生成本地配置。

### Phase 6：安装器与快捷方式

- Setup.exe；
- VBS/启动器；
- 开始菜单/桌面快捷方式；
- 卸载和数据保留策略。

### Phase 7：干净机验收

- Windows 10/11 clean machine；
- 完整功能矩阵；
- 资源下载失败、端口冲突、设备变化、模型损坏、网络中断测试；
- 发行包隐私审计；
- 生成可分享压缩包和校验文件。

## 16. 暂不实现的内容

本轮明确不执行：

- 不修改源代码；
- 不删除 Qwen TTS、dsh 或 assets；
- 不制作安装器；
- 不下载或移动模型；
- 不生成发行包；
- 不提交或推送代码；
- 不改变当前用户配置和 Style Learning 数据。

本文件只是后续实现依据，真正动工前仍需对目录清理清单、模型来源、许可证和默认功能再次确认。
