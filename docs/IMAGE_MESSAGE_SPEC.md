# IMAGE_MESSAGE_SPEC：图片消息、剪贴板与多模态边界

> 状态：实现版 v1.0（2026-08-21）。
> 覆盖：Electron 桌宠聊天框、QQ OneBot 图片段、Agent 多模态输入。

## 统一契约

`ImagePayload` 在 `core/image_payload.py` 定义：

- 支持 PNG/JPEG/GIF/WebP；base64 在解码前先按编码长度拒绝超限输入，再检查 MIME、magic bytes 与 Pillow 完整性。
- 单图最大 10MB、最大 40MP，单次最多 4 张。
- 输出 `data_url/raw/content_type/animated/source`。
- Agent 进入 LLM 前 fail-closed 再次校验 data URL；历史和长期记忆不保存 base64。桌宠 WS 单帧上限 64MiB，可容纳 4 张各 10MB 图片的 base64 JSON，但只接受 Electron Node `ws` 的无-Origin连接；浏览器任意 Origin 会在握手阶段拒绝。真实 socket 测试覆盖大帧与恶意 Origin。

## 桌宠剪贴板

```text
浏览器 paste(image item)
  -> FileReader data URL（renderer 只做预览）
  -> preload chat-send(text, images)
  -> main validateChatImages：先校验整批 magic/大小/数量，再统一保存 userData/chat-images/*.ext
  -> WS stream_talk.images（当前轮 data URL）
  -> PetServer -> Agent.handle(text, images, channel=tts)
  -> LLM messages[user].content = text + image_url[]
```

- 粘贴即预览；每张可单独移除；纯图片也可发送。
- `chat.json` 只保存 `chat-images/<uuid>.ext` 引用；重启历史通过严格路径校验后的 `file://` URL 预览，不批量重建 base64。
- 仅重试当前消息时按 refs 读取原图为 data URL，复用原图片引用、不重复落盘。整批任一校验/写入失败会删除本批临时文件。
- 历史实际限制为 500 条；淘汰消息时删除剩余消息不再引用的图片。确认“清空窗口记录”时同步删除专用 `chat-images/` 目录，但不删除共享表情库。
- 图片不自动写入 SQLite 消息正文、FTS、长期记忆；当前轮仍保留 `[图片]` 占位的记忆语义。
- 录音最长 120 秒、最多 20MB；达到任一上限立即停止，超大录音不送入 IPC。聊天窗隐藏、pagehide、MediaRecorder error 或构造失败共用清理路径，停止 recorder、全部麦克风 track 和 timer；隐藏窗口不提交转写。转写结果只填入文本框，用户确认后发送。
- 离线发送已经创建本地失败消息时，“重试”复用该 `message_id` 和图片引用；只有主进程尚未创建消息时才保留输入框并重新发送，避免重复消息和孤儿图片。

## QQ 图片

按 OneBot 图片段依次尝试：data URL、HTTP URL、本地 `url/path/file`、最多一次 `get_image` API。所有来源统一进入 `ImagePayload`。普通图、截图、静态表情和动图都送给当前轮 LLM；只有标注为静态表情包的图片才进入表情库。

- 本地绝对/相对路径必须位于 `qq.image_roots`；空配置只允许项目 `data/` 与根目录。
- HTTP 图片默认只允许解析结果全为公网 IP 的 HTTP(S) URL；请求连接固定到已校验 IP，并通过原 hostname 的 Host/SNI 验证 TLS，关闭重定向，按流读取并在 10MB 处停止。Clash fake-IP 环境仅在显式 `qq.trusted_image_proxy=true` 且 hostname 命中 `qq.image_proxy_hosts` 时允许 `198.18.0.0/15`，默认 allowlist 包含 `multimedia.nt.qq.com` 与 `multimedia.nt.qq.com.cn`；任意其他主机仍拒绝，不能全局放行 fake-IP。
- QQ 消息最多解析前 4 张图；其余丢弃并记录 warning。

## 安全与降级

图片解析失败不阻塞文本消息；无文本且图片失败则跳过该消息。原始图片只保留在桌宠用户数据目录或表情库，删除聊天记录不自动删除共享表情库，表情库提供显式删除。
