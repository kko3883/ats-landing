# Deploy IB Gateway + FX daemon on the Synology NAS (paper)

Target: `/volume1/docker/ats-fx` on the NAS, Docker (Container Manager), docker-compose **V1**.
Everything runs on the default bridge — **not** in the gluetun VPN namespace.

## Prerequisites

- NAS reachable (Tailscale up — `ssh nas` works).
- The **second IBKR username** created for the Gateway, with **trading** permission.
- Its **paper account id** (starts with `DU`).
- Docker running on DSM.

---

## 1. Copy the project onto the NAS

From your workstation (uses the `nas` SSH alias):

```
scp -r "C:\Users\Kelvin Ko\ats-landing\trading\nautilus_port" nas:~/ats-fx
```

SSH in:

```
ssh nas
```

Authenticate sudo once (this prompts for your password; later sudo calls can chain):

```
sudo -v
```

Move it into the docker share:

```
sudo mkdir -p /volume1/docker/ats-fx && sudo cp -r ~/ats-fx/. /volume1/docker/ats-fx/
```

```
cd /volume1/docker/ats-fx
```

## 2. Create the secrets file

```
sudo cp .env.example .env && sudo chmod 600 .env
```

Edit it — fill in the **second-user** credentials, the `DU…` account id, and a VNC password:

```
sudo vi .env
```

## 3. Build and start

First run pulls the gateway image and builds the daemon image (a few minutes):

```
sudo docker-compose up -d --build
```

## 4. First login + 2FA

Watch the gateway boot — it logs in with your creds and fires a **2FA push** to IBKR Mobile:

```
sudo docker-compose logs -f ib-gateway
```

Approve the push on your phone. If a dialog needs clicking, open a VNC tunnel from your
workstation:

```
ssh -L 5900:127.0.0.1:5900 nas
```

Then point a VNC viewer at this address (use `VNC_SERVER_PASSWORD`):

```
localhost:5900
```

Once logged in, the API on 4002 comes alive and the healthcheck goes green.

## 5. Verify the daemon connected

```
sudo docker-compose logs -f fx-daemon
```

You want: Nautilus connects to IB → loads EUR/USD, AUD/JPY, NZD/JPY → subscribes to 1h bars.
No orders unless a signal fires.

Confirm it is **not** in the gluetun namespace (should print `default` / a bridge, not `container:gluetun`):

```
sudo docker inspect -f '{{.HostConfig.NetworkMode}}' ats-fx-daemon
```

---

## Ongoing operations

- **Daily:** `AUTO_RESTART_TIME` restarts the gateway with no input (token-based).
- **Weekly:** after IBKR's Sunday reset you get one 2FA push — approve on your phone.
- **autoheal** restarts either container if its healthcheck fails.
- Tail logs anytime:

```
sudo docker-compose logs -f
```

- Stop everything:

```
sudo docker-compose down
```

## Going live (only after the paper parallel-run)

1. Confirm the second user has **live trading** permission + funding.
2. In `docker-compose.yml`: set `TRADING_MODE: live`, change the published port and
   `IBG_PORT` from `4002` → `4001`.
3. Recreate, and verify on a **tiny** size first:

```
sudo docker-compose up -d --build
```

---

## Notes / safety

- Paper (4002) until validated. Live is 4001.
- Credentials live only in `.env` (chmod 600). IB is bound to `127.0.0.1` on the NAS —
  never exposed to the LAN.
- If the daemon can't reach IB ("connection refused"): the gateway's API allowed-IPs may
  need to include the docker subnet — check `sudo docker-compose logs ib-gateway`.
- The `autoheal=true` label assumes your autoheal watches that label; adjust if yours differs.
