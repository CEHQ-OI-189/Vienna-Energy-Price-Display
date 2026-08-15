# Vienna Spot Price Display

A single self-contained webpage showing today's hourly Vienna electricity
spot price (aWattar / EPEX Spot AT) as a bar chart, with the current hour
highlighted and a low/medium/high tag. Updates itself every 15 minutes,
fully automatically, for free.

## One-time setup

1. **Create a new GitHub repository.**
   - Go to github.com, click the **+** in the top right → **New repository**.
   - Name it anything, e.g. `vienna-display`.
   - Set it to **Public** (required for free GitHub Pages).
   - Do NOT initialize with a README (we already have one).

2. **Upload these files** to the new repo.
   - On the repo page, click **Add file → Upload files**.
   - Drag in this entire folder's contents, keeping the folder structure
     (the `.github/workflows/update.yml` file must stay in that exact path).
   - Commit the files.

3. **Turn on GitHub Actions.**
   - Go to the **Actions** tab in your repo.
   - If prompted, click **"I understand my workflows, go ahead and enable
     them."**

4. **Run it once manually to generate the first page.**
   - Still in the **Actions** tab, click **"Update Vienna price display"**
     in the left sidebar, then **Run workflow → Run workflow**.
   - Wait ~30 seconds, refresh, and you should see a green checkmark.

5. **Turn on GitHub Pages.**
   - Go to repo **Settings → Pages**.
   - Under "Build and deployment", set **Source: Deploy from a branch**.
   - Set **Branch: main**, folder **/docs**, then **Save**.
   - GitHub will show you a URL like
     `https://yourusername.github.io/vienna-display/` — that's your
     display's permanent address.

6. **Open that URL on the iPhone** (Safari), add it to the Home Screen,
   and set up Guided Access as planned.

That's it — the Action re-runs every 15 minutes automatically and the
Pages site always reflects the latest run. Nothing further to maintain.

## Notes

- Prices shown are the **raw wholesale spot price** (matches aWattar's own
  public numbers), not your exact Wien Energie Voll Aktiv billed rate. We
  can add that markup calculation later once you're switched over.
- If a run ever fails (e.g. aWattar's API is briefly down), the page
  simply keeps showing the last successful result until the next
  successful run.
