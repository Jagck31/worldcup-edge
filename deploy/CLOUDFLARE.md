# Public URL via Cloudflare Tunnel + Access (no open ports, login-gated)

Serves the dashboard at `https://wc.YOURDOMAIN.com` through an **outbound** tunnel — nothing
is opened on the box's firewall, and your trading box stays as locked-down as it is now. A
free **Cloudflare Access** policy puts an email-code login in front of it. Replaces Tailscale
for access (you can keep both during the switch).

## Prerequisites
- A domain added to a **free Cloudflare account** (its nameservers pointed at Cloudflare —
  Cloudflare shows you the two NS records when you add the domain). Any cheap domain works.
- You're SSH'd into the box as root (over Tailscale, as before).

## 1. Install cloudflared (if not already)
```bash
ARCH=$(dpkg --print-architecture)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared
cloudflared --version
```

## 2. Authorize + create the tunnel
```bash
cloudflared tunnel login          # prints a URL — open it, pick YOURDOMAIN, Authorize
cloudflared tunnel create worldcup
```
Note the line `Created tunnel worldcup with id <UUID>` and the credentials path
`/root/.cloudflared/<UUID>.json`.

## 3. Write the tunnel config
```bash
mkdir -p /etc/cloudflared
cat >/etc/cloudflared/config.yml <<'YML'
tunnel: worldcup
credentials-file: /root/.cloudflared/REPLACE_WITH_UUID.json
ingress:
  - hostname: wc.YOURDOMAIN.com
    service: http://127.0.0.1:8000
  - service: http_status:404
YML
nano /etc/cloudflared/config.yml   # put the real UUID + your hostname
```

## 4. Route DNS + run as a service
```bash
cloudflared tunnel route dns worldcup wc.YOURDOMAIN.com   # creates the CNAME for you
cloudflared service install
systemctl enable --now cloudflared
systemctl status cloudflared --no-pager | grep Active
```
`https://wc.YOURDOMAIN.com` is now live (Cloudflare issues the TLS cert automatically).

## 5. Put a login in front (Cloudflare Access — free)
In the Cloudflare dashboard → **Zero Trust → Access → Applications → Add an application →
Self-hosted**:
- Application domain: `wc.YOURDOMAIN.com`
- Add a policy: **Allow**, include → **Emails** → your email address.
- Save. Now visiting the site emails you a one-time code to log in. (Add more emails any time.)

## 6. (optional) retire the Tailscale serve mapping
Once the public URL works:
```bash
tailscale serve --https=8443 off
```

## Operating
- Logs: `journalctl -u cloudflared -f`
- The dashboard still binds `127.0.0.1:8000`; only Cloudflare can reach it.
- This is independent of Stock Claude and opens **no** inbound ports.
