# 免费公网访问

本项目推荐用 **Tailscale Funnel** 把家里的 TickFlow Stock Panel 暴露为公网 HTTPS 地址。访问者不需要安装 Tailscale，只需打开 `https://设备名.tailnet名.ts.net`，输入项目的共享访问密码后即可使用。

如果已经拥有接入 Cloudflare 的域名，也可以使用 [Cloudflare 命名 Tunnel 方案](./public-access-cloudflare.md)，获得自定义公网域名和独立的 Docker 侧车部署。

## 架构与数据边界

```text
公网浏览器
  -> Tailscale Funnel（HTTPS / 稳定 ts.net 域名）
  -> 家庭电脑 127.0.0.1:3018
  -> TickFlow Stock Panel
       ├─ data/市场数据：应用统一读取和同步
       └─ data/user_data：当前自选、复盘、配置与访问密码
```

公网映射不会复制或迁移本地数据，外部访问看到的就是家庭服务器当前的数据。当前认证采用单一共享密码，不提供用户注册、角色权限或按用户隔离的自选与复盘；把网址和密码交给他人，等同于允许对方操作同一套面板数据。

## 为什么使用 Tailscale Funnel

- [Personal 方案](https://tailscale.com/pricing)目前为家庭用途免费，[Funnel](https://tailscale.com/kb/1223/funnel) 对所有方案开放。
- 自动提供 HTTPS 和稳定的 `*.ts.net` 地址，不要求公网 IP、路由器端口转发或自购域名。
- 通过 TCP 代理传输，能够承载本项目的 SSE 行情与流式复盘。
- 免账号的 Cloudflare Quick Tunnel 不作为主方案：[官方限制](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)明确说明 Quick Tunnel 不支持 SSE，且 URL 不稳定，更适合临时演示。

Funnel 目前仍标记为 beta，并有不可配置的带宽限制；家庭看板访问通常足够，不适合作为高流量商业站点。

## 首次部署

1. 在家庭服务器安装 Tailscale，并用个人账号登录。确保 MagicDNS 和 HTTPS 已启用。首次执行 Funnel 时，Tailscale 会打开网页让管理员批准 Funnel 权限。
2. 准备配置：

   ```bash
   cp .env.example .env
   ```

   Windows PowerShell 可用：

   ```powershell
   Copy-Item .env.example .env
   ```

3. 编辑 `.env`，至少设置：

   ```ini
   AUTH_PASSWORD=请替换为强密码
   ```

   至少使用 12 位随机密码，并且不要与其他网站共用。应用初始化后会在 `data/user_data/auth.json` 保存密码哈希，不保存明文。

4. 启动应用与公网映射。

   Windows PowerShell：

   ```powershell
   .\scripts\public-funnel.ps1
   ```

   Linux：

   ```bash
   chmod +x scripts/public-funnel.sh
   ./scripts/public-funnel.sh
   ```

脚本会构建并启动 Docker、等待 `/health` 通过、检查 Tailscale 登录态，最后执行持久后台 Funnel 并打印公网 URL。后续只需快速恢复服务时，可以使用 `-SkipBuild`（PowerShell）或 `--skip-build`（Linux）。

## 日常运维

查看公网状态：

```bash
tailscale funnel status
```

停止本项目使用的 443 Funnel：

```bash
tailscale funnel --https=443 off
```

更新并重建：

```bash
git pull
docker compose up -d --build
```

建议定期备份整个 `data/`；其中市场数据通常可以重拉，但 `data/user_data` 中的自选、复盘和个人配置不可替代。

Funnel 映射的是家庭服务器上的实时服务，并不会把 `data/` 复制到第三方云盘。只要家庭服务器在线，外部访问看到的就是当前本地数据；服务器离线时公网地址也会离线。

## 已知限制

- 免费方案适用于个人和家庭非商业用途；商业用途需重新核对 Tailscale 当期条款。
- Funnel 只使用 Tailnet 的 `*.ts.net` 名称并强制 HTTPS。
- 家庭上行带宽和服务器性能决定多人访问体验。
- 所有访问者共享同一数据和操作权限，因此只应把地址与密码交给可信人员。
- Free 行情档的实时订阅数量仍受数据源能力限制。
