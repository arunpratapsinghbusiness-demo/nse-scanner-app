# NSE Gap-Fill Scanner — 100% Free Stack

Har cheez free hai. Koi server rent nahi karna:

| Piece | Service | Cost |
|---|---|---|
| Scheduler + compute (weekly train + daily 10AM scan) | GitHub Actions | Free (public repo) |
| Model + signals storage | GitHub repo itself (`docs/data/`) | Free |
| Push notification | ntfy.sh | Free, no signup |
| PWA hosting | GitHub Pages | Free |

## Step 1 — GitHub account + repo
1. https://github.com par account banao (agar nahi hai).
2. New repository banao — **Public** rakhna zaroori hai (GitHub Actions free minutes public repos me unlimited hain).
3. Ye poora folder (`nse-scanner-app/`) us repo me upload/push kar do — GitHub website par "Add file → Upload files" se bhi kar sakte ho, drag-drop se saare files ek baar me daal do (folder structure preserve rakhna).

## Step 2 — GitHub Pages on (PWA hosting)
Repo → **Settings → Pages** → Source: `Deploy from a branch` → Branch: `main`, folder: `/docs` → Save.
Kuch minute me aapki app live hogi: `https://<username>.github.io/<repo-name>/`

## Step 3 — ntfy topic banao (push notification, free, no login)
1. Ek random unique naam socho, jaise `raj-nse-signals-8823` (kisi ko guess na ho paye isliye random suffix rakho — ye topic public hai, jo bhi naam jaanta hai wo subscribe kar sakta hai).
2. Phone me **ntfy** app install karo (Android Play Store / iOS App Store) → "+" dabao → wahi topic naam daal do → subscribe.
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - Name: `NTFY_TOPIC`
   - Value: `raj-nse-signals-8823` (wahi jo aapne phone me daala)

Ab jab bhi daily scan chalega, aapke phone pe seedha notification aayega — app band ho tab bhi.

## Step 4 — Pehli baar model train karo (manual run)
Repo → **Actions** tab → left side "Weekly model retrain" → **Run workflow** (manual trigger) → Run.
Ye 5-15 minute lega (poore universe ka 60-din backtest). Complete hone ke baad `docs/data/model.pkl` aur `watchlist.json` khud commit ho jaayenge.

**Ye step zaroori hai** — jab tak model.pkl exist nahi karta, daily scan fail hoga.

## Step 5 — Daily scan apne aap chalega
`.github/workflows/daily_scan.yml` already set hai: **Mon–Fri, 10:00 AM IST** par chalega, sirf watchlist stocks scan karega (fast), `docs/data/signals.json` update karega, aur push notification bhejega.

Chaho to test ke liye Actions tab se "Daily 10AM signal scan" ko bhi manually **Run workflow** kar sakte ho, kabhi bhi.

## Timing note (important, honest)
GitHub Actions ka free cron **exact 10:00:00 guarantee nahi karta** — kabhi-kabhi 2-10 min late fire hota hai (peak load pe). Trading signal ke liye ye usually theek hai. Agar second-level precision chahiye:
- https://cron-job.org par free account banao → ek HTTP request "GitHub Actions `workflow_dispatch` API" ko exact 10:00:00 par bhejo (repo Settings → Developer settings → Personal access token banake). Ye zyada punctual hota hai. Chaho to bata dena, main ye bhi wire kar dunga.

## Weekly retrain schedule
Har **Saturday 23:00 UTC** (~Sunday 4:30 AM IST, market band) full retrain hota hai — heavy backtest sirf tab hota hai jab market chalu nahi, daily 10AM job hamesha fast rehta hai.

## Position sizing / risk config
`scripts/common.py` me ye values edit kar sakte ho apni real capital ke hisaab se:
```python
ACCOUNT_BALANCE = 5000
LEVERAGE = 5
RISK_PCT_PER_TRADE = 4.0
```

## ⚠️ Disclaimer
Ye backtest-based strategy hai, live guarantee nahi hai. Gap-fill trades kabhi expected se different bhi chal sakte hain (slippage, liquidity, news). Circuit-breaker / daily-loss-cap discipline khud follow karna — model sirf entry/SL/target suggest karta hai, capital protection aapki zimmedari hai.
