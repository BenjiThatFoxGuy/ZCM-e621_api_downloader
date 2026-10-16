# e621 API plugin for ZC-ComicUpdater

A [scraper plugin](https://github.com/ZornTaov/ZC-ComicUpdater/wiki/Scraper-Plugins) that keeps content from e621 (or
e926) up to date, a pool for now, through the site's official API. The downloader is `tool/e621_pool.py`; it needs only
`requests`, which the updater's image already has, so the plugin has nothing to install.

## Installing

Clone it into the updater's config folder under the name `e621-api`:

```sh
git clone https://github.com/<you>/ZCM-e621_api_downloader.git config/plugins/e621-api
```

## Logging in

Posts the site hides from visitors with no account have no file address, and the API asks scripts to say who
they are. These are the plugin's own settings, set once for every e621 comic: in the updater's **Plugins**
dialog, press **Settings** beside e621.

- **e621 username** and **API key** (from the account's profile page, under Manage API Access). Without them
  the plugin still runs, but fetches only what a visitor can see.
- **User-Agent**, which starts as `ZC-ComicUpdater-e621-api/0.1 (by {username} on e621)`, `{username}` being
  filled in from the username. The site blocks generic agents, so keep one that names the program.

The updater keeps them in `config/plugin-setup/e621-api.json`, readable only by the user it runs as, and hands
them to the downloader as `E621_USERNAME`, `E621_API_KEY` and `E621_USER_AGENT`, so the key is never in the
log or a comic's run record. This needs a ZC-ComicUpdater with plugin settings; run by hand, the downloader
takes the same environment variables, or `--username` and `--user-agent`.

A comic added while these were per-comic settings still has them in its `mirror_metadata.json` under
`settings.plugin`. They are no longer used; delete them from there, since the key is in the clear.

## Adding a pool

On the updater's web page, pick **e621** as the scraper under Add comics, and give the pool's address,
`https://e621.net/pools/12345`. From a shell, run from the library's root:

```sh
python run_plugin.py --add e621-api Uncompressed/PoolName url=https://e621.net/pools/12345
```

A pool is one comic with no chapters. Each run fetches the pool's posts in the pool's order, leaving out the
ones already taken, so a pool that grows is carried on from where it was. Posts the account cannot see are
skipped, noted in `logs/e621.log`, and tried again next run. Every file is checked against the size and MD5
the site gives for it, and fetched again if a download breaks off or comes out wrong.

## Videos, GIFs and Flash

GIFs are pictures like any other. Video (`webm`, `mp4`) and Flash (`swf`) posts are fetched as they are: the
comic's folder keeps each one as its page, the reader plays it (Flash through Ruffle), and the comic's archive
holds a stand-in page for it, since a CBZ reader cannot show one. That needs a ZC-ComicUpdater that takes in
videos and Flash from a plugin; an older one drops them, and they are fetched again every run. Tick **Skip
videos and Flash** to leave them out, for such an updater or to save the space.

## Logs

Everything the downloader says goes to the updater's log for the run: each API request (the address as asked,
never the account, which goes in a header), its HTTP status, size and time, and a summary such as how many
posts a page held. `logs/e621.log` here keeps the same with the times, and the JSON body of each API response
(cut at 4000 characters, in full for an error), at up to 5 MB a file and three old files.

## Inactive pools

A pool marked inactive is always taken the first time. Once it is inactive and every post in it has been taken
(videos and Flash aside, when the comic skips them), the plugin marks the comic **ended**, so the updater leaves
it alone from then on. It does that once: untick ended and it is not marked again. This needs a
ZC-ComicUpdater with plugin `ended()` support; an older one never asks, and the comic stays as it is.

A pool still missing posts the account could not see is not marked ended, so they are tried again. Tick
**Keep checking an inactive pool** to never mark the comic ended and keep looking for changes.

## API format

The post endpoints are moving to a new response format: v2 is opt-in (`v2=true`) until December 2026, then the
default, and the old format is removed in May 2027. The downloader asks for v2 through the end of December
2026 and sends nothing after that, and reads either format whichever it gets. See `V2_OPT_IN_UNTIL` in
`tool/e621_pool.py`. Pools themselves (`/pools/{id}.json`) are not part of the change.

If the site's Cloudflare check blocks a run (a "Just a moment..." page, seen while the site is under attack),
the run ends with exit code 6 and the next scheduled run tries again.

## Limits

- Requests are held to one a second, as the API's rules ask. A large pool takes a while.
- Posts are remembered by id. A post reordered within the pool is not fetched again, and one added to the
  middle of the pool is taken in after the pages already held.
