# Ai-LiNK 电热水器 · Home Assistant 集成

把**国行 A.O.史密斯（A.O. Smith）电热水器**接入 Home Assistant。通过官方
「AI家智控 / AI-LiNK 智慧家」云端控制，支持水温、开关机、速热、杀菌、峰谷电、
中温保温、增容等，可桥接进 Apple 家庭（HomeKit Bridge）。

- 适用机型：`EWH-*HGAWi` 等 AI-LiNK 电热水器（官方 H5 里 `isHGA` / `isHGS` / `isHGX`
  / `isE9W` / `isBPW` / `isHT5` / `isPEorNPE` 等分支覆盖的机型族）
- 实测机型：**EWH-HGAWi**（`productMajorClassCode = 17`）
- 安装后只需**抓包一次**：token 由集成自动续期，失效也能自愈（见下）

## 为什么需要这个集成

| 方案 | 能接国行电热水器吗 |
| --- | --- |
| HA 官方 `aosmith` 集成 | ❌ 只认北美 iCOMM 账号 |
| 社区的 Ai-Link 集成（`mopocv`、`Doker9527`、`mylofsh`、`gzzwozuiai`） | ❌ 只支持燃气热水器（设备类别写死 `19`）/ 净水加热一体机（`21`）——指令集完全不同 |
| **本集成** | ✅ 使用电热水器的官方服务 `SetElectricWaterHeater` |

协议全部从官方 H5 前端（`ailink-appservice-h5-prd.hotwater.com.cn` 的
`/ElectricWaterHeater` 页面）逆向而来，字段名和取值逻辑与官方 App 一致；
指令格式已用真机抓包逐字节核对（`{"Temperature":"59"}` / `{"powerStatus":"1"}`）。

## 与同类项目的差异

同一朵云上的项目都靠手动抓包，思路一致；差别在于往下走了多远：

| 维度 | 燃气热水器那批 | 本集成 |
| --- | --- | --- |
| 设备识别 | 写死 `deviceCategory == "19"` | **动态**列出账号下全部设备供选择（显示类别码+型号） |
| token 过期 | 只做人工重新认证（仓库里明确写"不实现自动令牌刷新"），用户需反复抓包 | **主动同步 + 失效自愈 + 永不放弃**（见下） |
| 认证失败 | 抛 `ConfigEntryAuthFailed` → HA 停止轮询 | 抛 `UpdateFailed` → **保持轮询等自愈** |
| 指令确认 | 回读失败即抛错 | 回读失败打 warning（避免"1 秒内开→关互相覆盖"这类误报） |
| 签名 | 部分项目不签名、改用抓包得到的固定 `encode` | 按官方算法签名；**实测服务端当前不校验签名或 `encode`**，留着以防官方重新开启 |

## 亮点：token 自动续期（抓包一次即可）

官方 token 是短命 JWT（约 30 分钟），但云端保留了账号**最新**的那个 token：
用旧 token 调 `POST /AiLinkService/api/getLastToken` 就能换到它。本集成据此做了三层处理：

1. **主动同步**：token 剩余有效期不足 12 分钟时，每 **3 分钟**向云端要一次最新 token
   （手机 App 刷新过就会拿到新的，token 因此不会真正过期）；
2. **过期后降低频率**：JWT 声明已过期但云端仍接受时，探测间隔放宽到 **30 分钟**
   （实测「JWT 过期 ≠ token 不可用」，没必要频繁问）；
3. **失效等待而非报错**：万一真的失效了，集成**不会**把条目打成认证失败停止工作，
   而是每 **5 分钟**重试同步 —— 你只要在手机上打开一次「AI家智控」App，集成就会
   接上最新 token 自动恢复，**不需要重新抓包**；
4. **永不放弃**：同步尝试只按时间节流，不会因为连续失败就永久停止
   （有单测守着：连续失败 4 次后第 5 次仍会成功换到新 token）；
5. **兜底**：需要立刻恢复时，可以用「重新认证」粘贴新 token。

集成会把续期后的 token 写回配置项，重启后继续用；`token_expires_at` 作为热水器
实体属性暴露，方便观察。

### 实测记录（2026-09-16，型号 EWH-HGAWi）

| 观察 | 结果 |
| --- | --- |
| token `exp` | 抓包后 29 分钟 |
| **`exp` 过后 30 分钟 / 10 小时** | **依然完全可用**（读设备、下发指令都正常）→ **云端不做 JWT 过期校验**，它认的是服务端会话记录 |
| 用旧 token 调 `getLastToken` | 返回**同一个** token（App 未刷新时）→ 该接口取的是"账号最新 token" |
| 签名 / `encode` 是否被校验 | **不校验**：乱写 `sign`、完全不带签名头、乱写或省略 body 里的 `encode`，读写都成功。集成仍发送正确签名，以防官方重新开启校验 |
| 账号密码登录 | **不存在**：`/user/login` 只认 `mobile + captcha`，传 `password`/`pwd`/`account` 等一律返回"参数缺失"；8 个密码登录类接口名全部 404 |
| 短信验证码登录 | 存在，但服务端会拿 `ticket` 去腾讯校验（假 ticket → `验证失败`），所以绕不开滑块；作为"抓包彻底失效时"的应急手段 |
| 结论 | 社区流传的"30 分钟必须重抓"是误解；真实有效期取决于服务端会话存活时间，通常远长于 30 分钟 |

## 安装

### HACS（推荐）

1. HACS → 右上角三点 → **自定义仓库**
2. 地址填本仓库 URL，类别选 **Integration**
3. 搜索「Ai-LiNK 电热水器」安装 → 重启 Home Assistant

### 手动

把 `custom_components/ailink_ewh` 整个目录复制到 HA 的 `config/custom_components/`
下，重启 Home Assistant。

## 一、抓包（只需一次）

需要拿到 4 个值。手机装一个抓包工具（iOS：**Stream** / Charles；Android：**小黄鸟 HttpCanary**），
让手机流量走代理，然后打开「AI家智控」App，进入热水器详情页（多点几次、切几个页面）。

在抓包里找发往 **`ailink-api.hotwater.com.cn`** 的请求，取：

| 参数 | 从哪里取 | 例子 |
| --- | --- | --- |
| `access_token` | 请求头 `Authorization` 的值，去掉开头的 `Bearer ` | `eyJhbGciOiJIUzI1NiIs…` |
| `user_id` | 请求头 `UserId` | `YOUR_USER_ID` |
| `family_id` | 请求体 JSON 里的 `familyId` | `YOUR_FAMILY_ID` |
| `cookie`（可选） | 请求头 `Cookie` | `…` |

> ⚠️ 抓完**立刻关闭手机代理**，否则 App 会报「服务器异常 / -1004」。
> ⚠️ token 就是你账号的登录凭证，别外发；诊断信息里集成会自动打码。

**先验证一下再装**（可选但强烈建议）：

```bash
pip install aiohttp
python3 tools/probe.py --token 'eyJ...' --user-id YOUR_USER_ID --family-id YOUR_FAMILY_ID
```

它会列出账号下的设备、打印设备上报的全部字段、**并测试 token 续期是否可用**
（`--renew`）。

## 二、配置

设置 → 设备与服务 → 添加集成 → 搜「**Ai-LiNK 电热水器**」→ 填入上面 4 个值 →
在设备列表里选中你的热水器（列表会显示设备的**类别码**和**型号**，方便确认）→ 完成。

## 实体

| 实体 | 说明 |
| --- | --- |
| `water_heater` 电热水器 | 开关机、设定温度、当前水温，附带运行状态与全部原始字段 |
| `sensor` 当前水温 | `realTemp` |
| `sensor` 运行状态 | 关机 / 加热中 / 预约中 / 保温中（与官方 App 判定逻辑一致） |
| `sensor` 故障信息 | 故障/警告码与文案 |
| `binary_sensor` 加热中 / 故障 | 便于自动化 |
| `switch` 速热 / 杀菌 / AES 智能节能 / 峰谷电 / 中温保温 / 增容 | 只创建设备实际上报的开关（可在选项里强制全建） |

## 选项

设置 → 设备与服务 → 集成 → 配置：

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| 轮询间隔 | 60 秒 | 10–900 |
| 最低/最高设定温度 | 35 / 75 °C | 部分机型（如 HGX 速热系列）支持到 85 °C，可在第二个工作模式下手动放开 |
| 暴露全部原始字段 | 开 | 关掉后热水器实体只保留关键属性 |
| 创建全部开关实体 | 关 | 设备首次上报缺字段时用 |

## Apple 家庭 / HomeKit

在 HomeKit Bridge 里选择：
- 热水器实体（`water_heater`）→ 恒温器（开关 + 目标温度）
- `sensor` 当前水温 → 温度传感器
- 需要的开关（速热、杀菌等）

## 已知限制

- 预约加热（TimerOne / TimerTwo / CountdownOne）、峰谷电时段、中温保温温度等
  需要「带时间段」的写入，目前只支持开关，时段设置请用官方 App（后续可加）。
- 云端接口是私有的，A.O.史密斯随时可能改动；若报「校验失败 / 签名错误」，
  请提 issue 附上 HA 日志（集成会自动打码 token）。
- 依赖账号所在的「家庭」（familyId）；换了家庭需要重新配置。

## 排错

| 现象 | 原因 |
| --- | --- |
| 添加集成报「认证失败」 | token 过期或 `user_id`/`family_id` 不匹配。云端对无效凭证**不报错，只返回空列表**，集成据此判定 |
| 实体一直「不可用」 | 设备离线（App 里也看不到）或 token 失效 |
| 日志出现「命令已下发但设备未在 10 秒内确认」 | 指令被云端接受但设备没回状态；多数情况稍后会生效，若长期如此请提 issue |

## 开发 / 测试

```bash
python3 -m venv .venv && .venv/bin/pip install aiohttp
.venv/bin/python -m unittest discover -s tests -v   # 12 个离线用例（签名、状态解析、续期链路）

# 抓包验证工具：列设备 / 打印全部上报字段 / 实测换 token
.venv/bin/python tools/probe.py --token 'eyJ…' --user-id … --family-id … --renew

# token 寿命哨兵：每 30 分钟探一次，记录"是否还在用 / App 是否换过卡"
.venv/bin/python tools/token_watchdog.py --interval 1800 --days 7 --log .token-watchdog.log

# 部署到另一台主机的 HA（可选 --restart）
HA_HOST=root@ha.local HA_CONFIG=/opt/homeassistant/config ./scripts/deploy.sh --restart
```

### 如何自己再跑一遍协议核对

官方把设备详情页做成了 H5，前端代码可直接读：

```bash
base=https://ailink-appservice-h5-prd.hotwater.com.cn/dist
curl -s $base/config.js                      # BASE_API
curl -s $base/index.html                     # js 清单
curl -s $base/js/runtime.*.js | grep -o '7700:"[a-f0-9]*"'   # 分包映射（7700 = ElectricWaterHeater）
curl -s $base/js/ElectricWaterHeater.<hash>.js | grep -o 'SetElectricWaterHeater'  # 指令与字段
```

## 免责声明

- 本集成与 A.O.史密斯公司无关，非官方产品，仅供个人学习与自用。
- 接口为抓包/逆向所得，官方随时可能变更，不保证持续可用。
- 请自行保管好 `access_token`（它等同账号登录凭证）；本集成不会把它发给任何第三方。

## 致谢

协议研究站在这些项目的肩膀上：
[mopocv/Ai-Link_A.O.Smith](https://github.com/mopocv/Ai-Link_A.O.Smith)、
[Doker9527/Ai-Link-AOSmith-HA](https://github.com/Doker9527/Ai-Link-AOSmith-HA)
（签名算法修复）、
[xiaoyawei](https://github.com/xiaoyawei)（H5 签名算法思路）、
[gzzwozuiai/ha_aosmith_water_heater](https://github.com/gzzwozuiai/ha_aosmith_water_heater)
（同一朵云的另一个设备形态）。

## License

MIT
