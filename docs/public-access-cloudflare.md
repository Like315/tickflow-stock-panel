# Cloudflare Tunnel 公网访问方案

这是 Tailscale Funnel 之外的第二套方案：使用 **Cloudflare 远程管理的命名 Tunnel**，将家庭电脑中的应用发布到自己的 HTTPS 子域名。

```text
公网用户 https://stocks.example.com
  -> Cloudflare DNS / TLS / DDoS 防护
  -> 命名 Tunnel（仅由家庭服务器主动建立出站连接）
  -> cloudflared Docker 侧车
  -> Docker 私网 http://app:3018
  -> TickFlow Stock Panel 单密码认证
```

家庭路由器不需要端口转发，也不需要公网 IPv4。Cloudflare Tunnel 对所有方案开放；发布应用需要一个已经把 DNS 接入 Cloudflare 的域名。Tunnel 本身和 Cloudflare DNS 可以使用免费方案，但域名注册费取决于你的域名注册商。

## 为什么不能使用 Quick Tunnel

`cloudflared tunnel --url http://localhost:3018` 虽然不需要账号和域名，但官方将其定位为开发测试功能：URL 每次变化、没有 SLA、最多 200 个并发请求，而且明确不支持 SSE。本项目的实时行情与流式复盘依赖 SSE，因此必须使用命名 Tunnel。

- [Cloudflare Tunnel 官方说明](https://developers.cloudflare.com/tunnel/)
- [命名 Tunnel 设置流程](https://developers.cloudflare.com/tunnel/setup/)
- [Quick Tunnel 限制](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)

## 一次性控制台配置

1. 创建 Cloudflare 账号，将一个域名的 nameserver 切换到 Cloudflare。
2. 打开 Cloudflare 控制台的 **Networking > Tunnels**，创建名为 `tickflow-home` 的 Cloudflared Tunnel。
3. 在 Tunnel 的连接器安装页选择 Docker，只复制命令中的 token，不要执行控制台给出的临时命令。
4. 给 Tunnel 添加 **Published application**：

   | 项目 | 值 |
   |---|---|
   | Hostname | `stocks.example.com`（换成你的域名） |
   | Service type | `HTTP` |
   | Service URL | `http://app:3018` |

   `app` 是主 `docker-compose.yml` 中的服务名。cloudflared 和应用位于同一个 Docker 私网，不需要绕回宿主机公网端口。

5. 若只供少数固定人员使用，可以额外启用 Cloudflare Access；否则至少保留项目自身的强访问密码。
6. 建议增加一条 Cache Rule，让 `/api/*` **Bypass cache**；静态资源仍可由 Cloudflare 正常缓存。

## 本机配置

应用认证仍配置在 `.env`：

```ini
AUTH_PASSWORD=请替换为强密码
```

当前项目使用单一共享密码，所有访问者看到并操作同一套自选、复盘与设置数据，不提供公开注册或用户数据隔离。

单独创建 Tunnel 配置，避免 Cloudflare token 被传给应用容器：

```powershell
Copy-Item .env.cloudflare.example .env.cloudflare.local
```

填写：

```ini
TUNNEL_TOKEN=从Cloudflare控制台复制的token
PUBLIC_HOSTNAME=stocks.example.com
```

`.env.cloudflare.local` 已被仓库的 `.env.*.local` 规则忽略，不会提交到 Git。

## 启动

Windows PowerShell：

```powershell
.\scripts\public-cloudflare.ps1
```

Linux：

```bash
chmod +x scripts/public-cloudflare.sh
./scripts/public-cloudflare.sh
```

脚本会依次校验配置、构建应用、启动 cloudflared、检查本地 `/health`，最后从公网域名再次检查 `/health`。成功后会打印实际访问地址。快速重启可使用 `-SkipBuild` 或 `--skip-build`。

## 运维与撤销

查看状态：

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml ps
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml logs cloudflared
```

只停止公网入口，不停止本地面板：

```powershell
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml stop cloudflared
```

若 token 泄露，在 Cloudflare 控制台轮换 Tunnel token，然后更新 `.env.cloudflare.local` 并重建 cloudflared 容器。删除 Published application 或 Tunnel 即可彻底撤销公网入口，不会删除 `data/` 中的股票或面板数据。

## 与 Tailscale Funnel 的选择

| 维度 | Cloudflare 命名 Tunnel | Tailscale Funnel |
|---|---|---|
| 公网地址 | 自有域名，品牌化更好 | 固定 `*.ts.net` 地址 |
| 额外成本 | Tunnel 免费；可能需要购买域名 | 家庭用途可使用 Personal 免费方案 |
| 访客客户端 | 不需要安装 | 不需要安装 |
| 家庭端口转发 | 不需要 | 不需要 |
| SSE/流式响应 | 使用命名 Tunnel | 支持 TCP/HTTPS 代理 |
| 配置复杂度 | 需要 Cloudflare DNS、Tunnel token 和 Published application | 安装并登录 Tailscale 后即可开启 |

已经拥有 Cloudflare 域名时，推荐本方案；希望完全不购买域名并尽快上线时，优先使用 Tailscale Funnel。
