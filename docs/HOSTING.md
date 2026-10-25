# Putting Just in Case online (Render, free)

We host two copies of the same code. Both update by themselves when `main` is pushed.

| Site | Who it's for | AI key | Password |
|---|---|---|---|
| just-in-case-demo | Showing it around | No (guided planner, Ruth's example, tour) | No |
| just-in-case-live | Teammates trying the real agent | Yes | Yes |

## One-time setup (Seyi)

1. On openrouter.ai, open **Keys** and make a new key just for hosting. Set a **credit limit** on it (for example $5), so the site can never spend more.
2. Go to render.com and sign up with GitHub.
3. Click **New > Blueprint**, pick the `just-in-case` repo, and click **Connect**. Render reads `render.yaml` and shows both sites.
4. It asks for two values for the live site:
   - `OPENROUTER_API_KEY`: the hosting key from step 1.
   - `SITE_PASSWORD`: a password to give teammates (not your key, not a personal password).
5. Click **Apply**. The first build takes about 5 minutes. Each site's page on Render shows its link, like `https://just-in-case-demo.onrender.com`.

## Good to know

- Free sites sleep after 15 minutes with no visitors. The next visit takes about a minute to wake up.
- Shared helper lists are reset when a site restarts. Fine for a demo.
- Never put the key or the password in GitHub, a chat, or a screenshot. They live only in Render's settings.
- To change the password: Render > just-in-case-live > **Environment**, edit `SITE_PASSWORD`, then **Save**.
- To turn the live site off: Render > just-in-case-live > **Settings** > **Suspend**.
