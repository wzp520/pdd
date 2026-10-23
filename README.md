# pddbot · 拼多多客服自动回复

基于 Playwright + DeepSeek 的拼多多商家后台客服自动回复工具，带本地可视化控制台。

## 功能

- 🤖 **自动接待**：轮询新消息，FAQ 命中直接回复，其余交大模型生成
- 📚 **问答管理**：关键词 FAQ，支持 Excel 批量导入 / 下载模板
- 🧠 **从人工学习**：人工处理后自动把「问题+人工回复」提炼成 FAQ，下次相同问题自动回复
- 🔔 **飞书通知**：转人工时推送到飞书群并 @人
- 📊 **数据概览**：后台经营数据一览（只读）
- 🔗 **快捷入口**：商品 / 订单 / 售后 / 营销一键直达后台页，人工操作
- ⚙️ **防风控**：随机轮询间隔、拟人节奏、转人工会话暂停并超时自动恢复

## 使用（Windows，无需装 Python）

1. 从 [Releases](../../releases) 下载 `pddbot-windows.zip` 并解压到一个文件夹；
2. 把文件夹里的 `.env.example` 复制一份改名为 `.env`，填入 DeepSeek API Key（见下）；
3. 双击 `pddbot.exe` 启动，**首次会自动下载浏览器内核（约 150MB，需联网，请耐心等）**；
4. 控制台会在浏览器打开 → 点「扫码登录」，用拼多多**商家版 App** 扫码登录；
5. 配置好问答 / 模型 / 飞书后，点「启动」开始自动接待。

### 配置 .env

```
DEEPSEEK_API_KEY=你的key    # 必填，https://platform.deepseek.com 申请
DRY_RUN=true                # 建议先干跑观察，确认没问题再到界面关掉、真实发送
```

其余参数（轮询间隔、转人工暂停时长）可直接在界面「运行设置」页调整。

## ⚠️ 风险提示

拼多多对自动化操作有风控，使用请注意：

- 先用 **DRY_RUN 干跑**观察决策是否合理，再开启真实发送；
- 轮询间隔不要调太小（默认 3~8 秒随机已较拟人）；
- 退款 / 投诉等敏感问题走**转人工**，不要让 AI 乱答；
- 违规自动化可能导致**店铺限权或封禁**，请自行评估风险。

## 开发 / 本地运行

```bash
pip install -r requirements.txt
playwright install chromium
python -m pddbot.gui
```

## 打包成 exe

推送 `v` 开头的 tag（如 `v1.0`）会触发 GitHub Actions 自动打包并发布 Release；
也可在 Actions 页手动运行 **Build Windows EXE**。本地打包：

```bash
pip install pyinstaller
pyinstaller pddbot.spec   # 产物在 dist/pddbot/
```
