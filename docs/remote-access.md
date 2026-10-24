# Open the board from your phone or another computer (Tailscale)

The scholarship board listens on `127.0.0.1:8765`, so at first only the computer running it can
open it. [Tailscale](https://tailscale.com) gives your own devices a private network (a
*tailnet*) and can put the board on it at an `https://` address, with no router settings and no
open ports.

There are two ways to share it:

| | **Serve** (recommended) | **Funnel** |
| --- | --- | --- |
| Who can open it | Only devices signed in to your tailnet | Anyone on the internet who has the link |
| Phone needs the Tailscale app | Yes | No |
| Board password | Strongly recommended | **Required** |
| Command | `tailscale serve --bg 8765` | `tailscale funnel --bg 8765` |

The autofill companion is different: it must stay on `127.0.0.1` on the computer where you fill
in forms. **Never** serve or funnel its port (8781). Its answers are meant for your own browser
only.

## 1. Set a board password first

The board refuses to open through Serve or Funnel until a password is set; on this computer it
still opens without one.

Even on a private tailnet, the board holds your application list and notes, and it accepts
changes. Give it a login:

```bash
python3 apps/board/set_password.py YOUR_NAME          # asks for the password twice
```

The server reads the login on every request, so no restart is needed. To remove the login,
delete `~/.hermes/scholarship-board/.auth.json`.

## 2. Install Tailscale on the computer running the board

Linux (including Ubuntu on WSL):

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                       # opens a sign-in link
sudo tailscale set --operator="$USER"   # optional: run serve/funnel later without sudo
```

macOS and Windows: install the app from <https://tailscale.com/download> and sign in.

**Windows with WSL:** install Tailscale *either* inside WSL (commands above) *or* on Windows,
not both. Both work, because Windows forwards `localhost` into WSL, so a Windows-side
`tailscale serve 8765` reaches the board in WSL.

Then, in the Tailscale admin console (<https://login.tailscale.com/admin/dns>), turn on
**MagicDNS** and **HTTPS Certificates**. Serve and Funnel need both of them.

## 3. Share the board

### Private: Serve (only your devices)

```bash
tailscale serve --bg 8765
tailscale serve status         # prints the address, like https://my-pc.tail1234.ts.net
```

On your phone:
1. Install the **Tailscale** app (App Store or Google Play).
2. Sign in with the same account and switch the VPN on.
3. Open the address that `tailscale serve status` printed, then sign in with the board login.

On another computer, install Tailscale, sign in, and open the same address.

Tip: on the phone, use **Add to Home Screen** so the board opens like an app.

### Public: Funnel (anyone with the link)

Use this only if you can't install Tailscale on the device, for example on a school-managed
phone. Set the password (step 1) first.

```bash
tailscale funnel --bg 8765            # or --https=8443 to use port 8443 instead of 443
tailscale funnel status
```

The first time, Tailscale may ask you to approve Funnel for this machine in the admin console.
Funnel can only use ports 443, 8443 and 10000.

Check that the login is on before you share the link: opening it in a private browser window
should ask for the password. From the command line:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://my-pc.tail1234.ts.net/   # 401 = login is on
```

## Turn it off

```bash
tailscale serve --https=443 off      # or: tailscale serve reset
tailscale funnel --https=443 off     # use the same --https port you turned on
```

## Keep it running

- `apps/board/install.sh` runs the board as a systemd user service. Run
  `loginctl enable-linger "$USER"` so it keeps running while you are logged out.
- `tailscale serve --bg` and `tailscale funnel --bg` are remembered across restarts.
- On WSL, the board is only reachable while WSL is running. Opening a WSL terminal once after
  Windows starts is enough.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| The address doesn't load on the phone | Make sure the Tailscale app is connected and signed in to the same account. |
| `serve` says HTTPS is not enabled | Turn on HTTPS Certificates in the admin console (step 2). |
| `502 Bad Gateway` | The board isn't running: `systemctl --user status scholarship-board`. |
| The browser keeps asking for the password | Re-run `set_password.py` and use exactly that user name. |
| Funnel link works on Wi-Fi but not on cellular | Wait a few minutes; new Funnel DNS names can take up to 10 minutes to appear. |
