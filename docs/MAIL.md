# 发邮件给家长：配置步骤

系统不自己架邮件服务器。原因有两个：云服务器一般都封了 25 端口；新服务器的 IP 没有发信信誉，发到 QQ、163 邮箱多半会进垃圾箱。
所以信先写进数据库里的发信队列（`mail_outbox`），再由后台 worker（`aistudy-worker`）通过发信服务的 SMTP 发出去，
失败了会自动重试。代码在 `app/mailer.py`（发信）和 `app/reports.py`（每周学习报告）。

现在只发一种信：**每周学习报告**。每周一早上 8 点以后发给每个家长，一封信讲这个家里每个未成年孩子上周的情况：
- 学了几天、学了多少分钟、现在连续学了几天；
- 比再上一周进步在哪（和孩子看到的每周进步卡用的是同一份数据）；
- 系统发现的问题。

家里的成人成员不进报告。家长可以在「我的账号 → 邮件通知」里关掉，每封信底部也有退订链接。

## 1. 开通发信服务（推荐阿里云邮件推送）

服务器本来就在阿里云，发到 QQ、163 的到达率也好，每天有免费额度。换成 Amazon SES 或 Resend 也行，见 `config/mail.example.toml` 里的注释。

1. 阿里云控制台 →「邮件推送」→ 选一个发信地域。地域决定第 4 步填哪个 SMTP 地址：杭州是 `smtpdm.aliyun.com`，新加坡是 `smtpdm-ap-southeast-1.aliyuncs.com`。
2. **发信域名**：新建 `mail.beejoy.ai`。建议用子域名，发信信誉和主域名分开，以后换服务商也不影响 beejoy.ai 本身的邮箱。
3. 控制台给出这个域名要加的 DNS 记录（见第 2 节），加完点「验证」。
4. **发信地址**：新建 `report@mail.beejoy.ai`，类型选「触发邮件」，再给它设置 SMTP 密码。
   - SMTP 用户名就是这个发信地址。
   - SMTP 密码就是刚才设置的密码。

## 2. 在 GoDaddy 加 DNS 记录

GoDaddy → 我的产品 → beejoy.ai → DNS → 添加记录。下面的「名称」都是相对 beejoy.ai 写的。**值以阿里云控制台给出的为准**，这里的值只是示例。

| 用途 | 类型 | 名称 | 值 |
|---|---|---|---|
| 证明域名是你的 | TXT | `aliyundm.mail` | 控制台给的一串字符 |
| SPF：允许阿里云代发 | TXT | `mail` | `v=spf1 include:spf1.dm.aliyun.com -all` |
| DKIM：签名 | TXT | 控制台给的名称（类似 `aliyun-cn-hangzhou._domainkey.mail`） | 控制台给的公钥 `v=DKIM1; k=rsa; p=…` |
| MX：收退信 | MX | `mail` | `mx01.dm.aliyun.com` |
| DMARC：告诉收件方怎么处理冒充的信 | TXT | `_dmarc.mail` | `v=DMARC1; p=none; rua=mailto:你的邮箱` |

注意：
- 一个名称下只能有**一条** SPF 记录。如果 `mail` 下已经有 SPF，把两条合成一条。
- DMARC 先用 `p=none` 观察一两周，确认自己的信都通过了，再改成 `p=quarantine`。
- DNS 生效一般要几分钟到几小时。生效后在阿里云控制台点「验证」，几项都变成通过才能发信。

## 3. 填服务器上的配置

```bash
sudo /opt/aistudy/install.sh upgrade        # 新版本会生成 config/mail.toml，并执行数据库迁移 0041
sudo nano /opt/aistudy/config/mail.toml
```

要改的几行：

```toml
provider = "smtp"
from = "BeeJoy <report@mail.beejoy.ai>"

[smtp]
host = "smtpdm.aliyun.com"   # 按第 1 步选的地域填
port = 465
security = "ssl"
username = "report@mail.beejoy.ai"
password = "控制台里设置的 SMTP 密码"
```

邮件里「看完整记录」「不想再收」两个链接要用到网址，所以 `.env` 里要有 `PUBLIC_URL=https://beejoy.ai`。没有就加上；不加，信照样能发，只是不带链接。

改完重启：

```bash
sudo systemctl restart aistudy aistudy-worker
```

## 4. 试一下

- 管理后台 →「站点设置」→「邮件」那一行 →「发一封测试邮件」。没发出去时，会直接显示服务商返回的原因。
- 或者在命令行：`cd /opt/aistudy && sudo -u aistudy .venv/bin/python -m app.cli mail-test 你的邮箱`。
- 往 QQ、163、Gmail 各发一封，看看是不是进了收件箱。如果进了垃圾箱，多半是第 2 节的 DNS 记录还没生效或者没加全。
- 「站点设置」→「邮件」→「最近发的信」里能看到每封信发出了没有，没发出去的写着原因。

## 发信的规矩（代码里已经做了）

- 同一个家长、同一周的报告只会写进队列一次。worker 重启、多个进程同时跑，都不会重复发。
- 发失败了，隔 5、10、20、40、80 分钟重试；试 6 次还不行就放弃，标成「没发出去」。收件地址不存在的，不再重试。
- 每天最多发 `daily_limit` 封（默认 500），防止程序出错时一下子发出一大堆。
- 退订链接带签名，别人改不了别人的；打开链接后还要点一次「确认退订」，因为有的邮箱会自动打开信里的链接。
  邮箱客户端自带的「退订」按钮（List-Unsubscribe 一键退订）不用确认，点了直接生效。
- `provider = "none"` 时完全不发信，也不往队列里写。
