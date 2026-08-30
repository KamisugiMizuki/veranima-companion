# STT_SPEC：桌宠语音输入设计与实现

> 状态：实现版 v1.2。
> 运行时：复用 `tts/gpt-sovits/runtime/python.exe` 的 Python 3.9/torch；STT 依赖使用隔离覆盖层 `data/stt-runtime/site`（FunASR 1.4.2），共享 TTS runtime 仍保持 FunASR 1.0.27。
> 主模型：ModelScope `iic/SenseVoiceSmall`，本地路径 `data/models/sensevoice-small`。
> 分段模型：本地 FSMN-VAD，路径 `tts/gpt-sovits/tools/asr/models/speech_fsmn_vad_zh-cn-16k-common-pytorch`。

## 目标

- 同一段音频允许中文、英文、日文混说；默认 `language=auto`。
- 语言优先级是中文 > 英文 > 日文，仅用于配置/降级和结果解释，不强制把整段音频锁成中文。
- 默认本地处理；只有用户显式把 `base_url` 改为远程兼容端点时才上传音频。原始音频不进入长期记忆；转写文本按普通用户消息进入 Agent。
- 本地模型未下载/不可加载时不进入固定间隔 crash-loop；可配置远程 OpenAI 兼容 STT，失败时返回空字符串并保留输入。

## 运行链

```text
Electron 聊天 MediaRecorder
  -> main.js IPC stt-transcribe
  -> 读取 stt.enabled/base_url/model/language/timeout/api_key
  -> 本地 base_url：先探测 /health；只有 provider=sensevoice 的健康服务才复用，否则预检 runtime/overlay/model/VAD 后 spawn scripts/run_stt_server.py（9890）
     远程 base_url：不启动本地进程
  -> multipart POST <base_url>/audio/transcriptions
  -> 隔离 overlay 加载 SenseVoiceSmall + FSMN-VAD；按语音段分别 language=auto，禁止 merge_vad 重新合并为单语言 utterance
  -> {text, language, provider, model}
  -> 聊天输入框草稿，不自动发送
  -> 用户确认/编辑后进入 Agent.handle(text)
```

STT 服务与 GPT-SoVITS TTS 共用 Python/torch runtime，但依赖覆盖层、进程、端口和模型对象分离。模型只加载一次；推理串行并从 FastAPI 事件循环卸载到工作线程。

## 配置

```yaml
stt:
  enabled: true
  base_url: "http://127.0.0.1:9890/v1"
  model: "sensevoice-small"
  model_path: "data/models/sensevoice-small"
  vad_model_path: "tts/gpt-sovits/tools/asr/models/speech_fsmn_vad_zh-cn-16k-common-pytorch"
  language: "auto"
  language_priority: [zh, en, ja]
  device: "cpu"
  timeout: 120
  max_audio_bytes: 20971520
```

- `language=auto`：中英日混合主路径。
- `language=zh`：用户明确要求整段中文时使用，不再执行其他语言 fallback。
- `language=auto` 首次结果为空时，后端才按 `language_priority`（默认 zh → en → ja）逐个重试；auto 有结果时不重跑，不把正常 code-switch 锁成中文。
- `stt.enabled=false` 时 Electron 不启动服务也不发请求；本地 9890 `base_url` 才由 Electron 拉起本地服务，其他 URL 直接作为远程兼容端点。
- Python server 读取 `model_path/vad_model_path/device/language/language_priority/max_audio_bytes`；旧 `config.yaml` 缺少 `vad_model_path` 时仍使用内置本地默认路径。Electron 请求读取 `base_url/model/language/timeout/api_key`。Python `STTClient` 使用同一 URL 拼接和 multipart 契约。

## 输出契约

```json
{
  "text": "这个 API 的 timeout 设成三十秒，然后あとで再确认一下",
  "language": "auto",
  "provider": "sensevoice",
  "model": "sensevoice-small"
}
```

当前 UI 只消费 `text`；其他字段供日志。模型不提供可靠逐词语言置信度时不伪造 confidence。

## 降级与边界

1. 未配置：返回空，不抛异常。
2. 模型、VAD 或 overlay 缺失：Electron 预检后不启动本地服务；启动前用 1.5 秒 `/health` 探测，只有 `ok=true/provider=sensevoice` 才复用；端口被其他 HTTP 服务占用时记录冲突并停止重启。异步 spawn error/异常 exit 共用 3s→6s→…→60s 的去重退避，成功转写后复位。
3. 音频过大、空文件、非音频 MIME、MIME 与 WAV/MP3/Ogg/FLAC/WebM magic bytes 不一致：在服务入口、模型加载前拒绝；上传文件名不参与容器判定。localhost POST 同时拒绝任何非空 `Origin`，防止浏览器页面跨域触发本地模型。
4. 服务超时：使用配置 `timeout` 的 `AbortSignal` 中止请求，保留用户输入，不自动发送。
5. 中英日快速 code-switch：允许识别结果混合，但短词、专有名词和日语短句可能误识别，不做二次“翻译修正”。
6. 原始音频不写长期记忆；只在服务请求期间存在，服务日志不得写音频内容。

## 验收

- `language=auto` 请求不被预先改写为 `zh`；只有 auto 空结果才按配置顺序 fallback。
- Fake backend 的中英日混合文本能通过 HTTP 契约；真实模型已分别通过中、英、日样例，并将三段有效语音加静音拼为同一 18.5 秒音频，输出同时含汉字、英文和日文假名。
- 模型/VAD/overlay 未下载时预检停用；其他进程故障使用封顶指数退避，不会每 3 秒永久 crash-loop。
- 真实 `sensevoice-small` 模型对仓库音频返回非空文本。
- 重新启动服务不会重复加载多个模型进程。

## 暂缓

- 麦克风持续流式 VAD：当前 FSMN-VAD 只在录音完成后离线分段；实时字幕仍暂缓。
- 自动发送转写结果：不做，避免误识别直接发给 QQ/LLM。
