#downloads an e621 (or e926) pool through the site's API, into the folder given as --output, one picture per
#post named <position in the pool>_<post id>.<ending> so the pool's order is the order they sort in.
#
#it is a program of its own, as any plugin's would be: it shares nothing with the comic updater.
#the API's rules, which this follows: a User-Agent that says who is asking, no more than one request a
#second, and the account's own API key for anything the site hides from a visitor with none.
#to log in, give it an account and that account's "API key" (from its profile) in E621_USERNAME and
#E621_API_KEY, and the agent in E621_USER_AGENT: the updater sets them from the plugin's settings. by hand,
#--username and --user-agent do too; the key is only ever read from the environment.
import argparse
import hashlib
import html
import logging
import logging.handlers
import os
import re
import sys
import time
from datetime import date
from urllib.parse import urlparse

import requests

#the site asks for at most one request a second; a little more than that, to be on the right side of it
PAUSE = 1.1
#the most posts one search gives
PAGE_SIZE = 320
RETRIES = 4
HOSTS = ("e621.net", "e926.net")
#the posts that are not still pictures: animations, fetched as they are. the updater keeps each as the
#page in the comic's folder, the reader plays it, and an archive holds a stand-in page saying where it is.
#a gif is a picture to every reader, so it is not one of these
MEDIA = ("webm", "mp4", "swf")
#the site's format is <program>/<version> (by <account> on e621); plugin.json's default for the setting is the same
DEFAULT_AGENT = "ZC-ComicUpdater-e621-api/0.1 (by {username} on e621)"
#the post endpoints' new response format (a bare array, files grouped under "files") is opt-in with v2=true
#until the site makes it the default, which it has said is December 2026, and the old format goes in May
#2027. asked for until the end of December, so a switchover that comes late still finds this asking; after
#it, nothing is sent, since v2 is the default and v1 is never wanted. what is read does not depend on this:
#a response is read as whichever format it turns out to be
V2_OPT_IN_UNTIL = date(2026, 12, 31)

LOG = logging.getLogger("e621")
#a response body is kept in the log file only this far: a page of 320 posts is a megabyte of json
BODY_LIMIT = 4000

#exit codes, checked by whatever runs this
OK, USAGE, NOT_FOUND, FAILED, DENIED, BLOCKED = 0, 2, 3, 4, 5, 6


class Failed(Exception):
    def __init__(self, message, code=FAILED):
        super().__init__(message)
        self.code = code


class Site:
    def __init__(self, host, username=None, key=None, user_agent=None):
        self.host = host
        self.session = requests.Session()
        #the site wants to know who is behind a script, and blocks the generic agents. {username} in the
        #agent is put in, so one default serves every account
        self.session.headers["User-Agent"] = (user_agent or DEFAULT_AGENT).replace(
            "{username}", username or "an anonymous user")
        if username and key:
            self.session.auth = (username, key)
        self.last = 0.0

    def wait(self):
        left = self.last + PAUSE - time.monotonic()
        if left > 0:
            time.sleep(left)
        self.last = time.monotonic()

    def get(self, url, **kwargs):
        #every request and what came back is logged: the address as asked (the account goes in a header, never
        #the address, so nothing secret is in it), the status, the size and the time. the body is logged for
        #anything that is not a picture, to the log file only, and in full for an error
        shown = requests.Request("GET", url, params=kwargs.get("params")).prepare().url
        for attempt in range(1, RETRIES + 1):
            self.wait()
            LOG.info("API request %d/%d: GET %s", attempt, RETRIES, shown)
            began = time.monotonic()
            try:
                response = self.session.get(url, timeout=60, **kwargs)
            except requests.RequestException as error:
                LOG.warning("API request failed after %.1fs: %s: %s", time.monotonic() - began,
                            type(error).__name__, error)
                if attempt == RETRIES:
                    raise Failed("could not reach {0}: {1}".format(url, error))
                time.sleep(5 * attempt)
                continue
            kind = response.headers.get("Content-Type", "?").split(";")[0]
            size = response.headers.get("Content-Length") or (len(response.content) if not kwargs.get("stream") else "?")
            LOG.info("API response: HTTP %s, %s, %s bytes, %.1fs", response.status_code, kind, size,
                     time.monotonic() - began)
            if response.status_code >= 400:
                LOG.warning("API error body: %s", response.text[:BODY_LIMIT])
            elif kind != "application/json" or kwargs.get("stream"):
                pass
            else:
                LOG.debug("API response body: %s", response.text[:BODY_LIMIT])
            if response.status_code == 404:
                raise Failed("{0} is not there (404)".format(url), NOT_FOUND)
            if response.status_code == 403 and "Just a moment" in response.text[:2000]:
                #the site turns its Cloudflare check up when it is under attack, and it stops scripts whatever
                #their account. nothing here can pass it, so the run ends and the next one tries again
                raise Failed("{0} was stopped by the site's Cloudflare check, which has nothing to do with the "
                             "account. It is usually lifted within a day; the next run will try again".format(url),
                             BLOCKED)
            if response.status_code in (401, 403):
                raise Failed("{0} refused (HTTP {1}). Check the username and API key in the plugin's settings, "
                             "which the site wants for anything it hides from a visitor".format(url, response.status_code),
                             DENIED)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == RETRIES:
                    raise Failed("{0} gave HTTP {1} every time".format(url, response.status_code))
                #told to slow down, or the site is struggling: wait longer each time
                LOG.warning("waiting %ds before asking again", 10 * attempt)
                time.sleep(10 * attempt)
                continue
            response.raise_for_status()
            return response
        raise Failed("could not fetch {0}".format(url))

    def json(self, path, **params):
        return self.get("https://{0}{1}".format(self.host, path), params=params).json()


def pool_id(address):
    #the pool's page, https://e621.net/pools/12345, or just its number
    text = address.strip()
    if text.isdigit():
        return None, int(text)
    parsed = urlparse(text)
    found = re.match(r"^/pools/(\d+)", parsed.path)
    if parsed.hostname not in HOSTS and parsed.hostname not in ("www." + host for host in HOSTS) or not found:
        raise Failed("{0} is not a pool's address. It looks like https://e621.net/pools/12345".format(address), USAGE)
    return parsed.hostname.replace("www.", ""), int(found.group(1))


def post_file(post):
    #a post's picture as (address, ending), either of which is None for a post the account can't be given.
    #the new format groups it under files (original.url, meta.ext); the old one has file.url and file.ext
    if "files" in post:
        return ((post["files"].get("original") or {}).get("url"), (post["files"].get("meta") or {}).get("ext"))
    return (post.get("file") or {}).get("url"), (post.get("file") or {}).get("ext")


def post_list(body):
    #the posts in a search's response: the new format is the array itself, the old wraps it in {"posts": ...}
    return body if isinstance(body, list) else (body.get("posts") or [])


def pool_posts(site, pool):
    #every post of the pool by id, the search going back through the pool a page at a time
    found, below = {}, None
    while True:
        params = {"tags": "pool:{0}".format(pool["id"]), "limit": PAGE_SIZE}
        if date.today() <= V2_OPT_IN_UNTIL:
            params["v2"] = "true"
        if below is not None:
            params["page"] = "b{0}".format(below)
        posts = post_list(site.json("/posts.json", **params))
        LOG.info("  %d posts%s", len(posts), " (ids {0} to {1})".format(
            min(post["id"] for post in posts), max(post["id"] for post in posts)) if posts else "")
        for post in posts:
            found[post["id"]] = post
        if len(posts) < PAGE_SIZE:
            return found
        below = min(post["id"] for post in posts)


def comic_info(pool, host):
    #what the pool says about itself, for the reader and the archive
    def tag(name, text):
        return "  <{0}>{1}</{0}>\n".format(name, html.escape(text, quote=False)) if text else ""
    return ("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<ComicInfo>\n"
            + tag("Series", pool["name"].replace("_", " "))
            + tag("Summary", (pool.get("description") or "").strip()[:2000])
            + tag("Web", "https://{0}/pools/{1}".format(host, pool["id"]))
            + tag("LanguageISO", "en") + "</ComicInfo>\n").encode("utf-8")


def post_checks(post):
    #what the site says the file is, (size, md5), to know a download is whole: a video is large enough that
    #a connection dropped part way is a thing that happens. the new format has them under files.meta
    meta = (post.get("files") or {}).get("meta") if "files" in post else post.get("file")
    meta = meta or {}
    return meta.get("size"), meta.get("md5")


def save(site, url, path, size=None, md5=None):
    #written aside and moved into place, so a download cut off leaves nothing that looks like a page; and
    #checked against what the site says it is, so a short one is fetched again rather than kept
    for attempt in range(1, RETRIES + 1):
        response = site.get(url, stream=True)
        digest, got = hashlib.md5(), 0
        try:
            with open(path + ".part", "wb") as f:
                for chunk in response.iter_content(1 << 16):
                    f.write(chunk)
                    digest.update(chunk)
                    got += len(chunk)
        except requests.RequestException as error:
            LOG.warning("download of %s broke off after %d bytes: %s: %s", url, got, type(error).__name__, error)
        else:
            wrong = ("{0} bytes, not {1}".format(got, size) if size and got != size
                     else "md5 {0}, not {1}".format(digest.hexdigest(), md5) if md5 and digest.hexdigest() != md5
                     else None)
            if not wrong:
                os.replace(path + ".part", path)
                LOG.info("saved %s, %d bytes%s", os.path.basename(path), got, ", md5 checked" if md5 else "")
                return
            LOG.warning("download of %s came out wrong: %s", url, wrong)
        if attempt < RETRIES:
            time.sleep(5 * attempt)
    try:
        os.remove(path + ".part")
    except OSError:
        pass
    raise Failed("could not download {0} whole in {1} tries; the next run will try again".format(url, RETRIES))


def setup_logging(folder):
    #what is said goes to the updater's log as it is said, and to a file of its own with the times and the
    #response bodies, which is kept to a size so a pool scraped every night does not fill the disk
    LOG.setLevel(logging.DEBUG)
    shown = logging.StreamHandler(sys.stdout)
    shown.setLevel(logging.INFO)
    shown.setFormatter(logging.Formatter("%(message)s"))
    LOG.addHandler(shown)
    if folder:
        os.makedirs(folder, exist_ok=True)
        kept = logging.handlers.RotatingFileHandler(os.path.join(folder, "e621.log"), maxBytes=5 << 20,
                                                    backupCount=3, encoding="utf-8")
        kept.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(kept)


def credentials(args):
    #the account, its key and the agent to ask as: from the environment, which is how the updater hands over
    #the plugin's settings, or from the command line when run by hand. the key has no option, since a
    #command line is seen in a process list and the updater writes it into its log
    return (args.username or os.environ.get("E621_USERNAME") or None,
            os.environ.get("E621_API_KEY") or None,
            args.user_agent or os.environ.get("E621_USER_AGENT") or None)


def run(args):
    host, number = pool_id(args.url)
    host = host or "e621.net"
    username, key, agent = credentials(args)
    site = Site(host, username, key, agent)
    LOG.info("pool %s on %s, as %s (%s)", number, host, username or "no account (a visitor)",
             "with an API key" if username and key else "no API key")
    LOG.info("User-Agent: %s", site.session.headers["User-Agent"])
    pool = site.json("/pools/{0}.json".format(number))
    order = pool.get("post_ids") or []
    LOG.info("  pool \"%s\": %d posts, %s, category %s", pool.get("name"), len(order),
             "active" if pool.get("is_active", True) else "inactive", pool.get("category"))
    skip = set(int(bit) for bit in args.skip.split(",") if bit.strip()) if args.skip else set()
    if not pool.get("is_active", True):
        #an inactive pool is one nobody is adding to any more, which is no reason not to take it the first time.
        #on a later run there is nothing new to expect from it, unless it is asked for anyway
        if not skip:
            LOG.info("pool %s is marked inactive, and this is the first scrape of it: taking it anyway", number)
        elif not args.allow_inactive:
            LOG.info("pool %s is marked inactive and already taken; nothing to do. Tick \"Pool no longer active\" "
                     "in the comic's settings to look for changes anyway", number)
            return OK
    wanted = [(position, post) for position, post in enumerate(order, 1) if post not in skip]
    LOG.info("pool %s (%s): %d posts, %d new", number, pool["name"], len(order), len(wanted))
    if not wanted:
        return OK

    posts = pool_posts(site, pool)
    os.makedirs(args.output, exist_ok=True)
    with open(os.path.join(args.output, "ComicInfo.xml"), "wb") as f:
        f.write(comic_info(pool, host))
    saved = 0
    for position, post_id in wanted:
        post = posts.get(post_id)
        url, ext = post_file(post or {})
        if not url:
            #hidden from a visitor with no account, or blacklisted, or deleted: left out, and tried again next
            #run since it is never reported as taken
            why = "gone from the pool's posts" if post is None else "has no file for this account"
            LOG.warning("skipping post %s: %s", post_id, why)
            continue
        if ext in MEDIA and args.skip_media:
            LOG.info("skipping post %s: a %s, and this comic skips videos and flash", post_id, ext)
            continue
        size, md5 = post_checks(post)
        save(site, url, os.path.join(args.output, "{0:05d}_{1}.{2}".format(position, post_id, ext)), size, md5)
        saved += 1
        LOG.info("fetched post %s (%d/%d)", post_id, saved, len(wanted))
    LOG.info("done: %d of %d new posts fetched", saved, len(wanted))
    return OK


def main():
    params = argparse.ArgumentParser(description="Downloads an e621 pool, in the pool's order.")
    params.add_argument("url", help="The pool's address, https://e621.net/pools/12345, or its number.")
    params.add_argument("--output", default=".", help="The folder to write the pictures in.")
    params.add_argument("--skip", help="Post ids, comma separated, already taken and not to be fetched again.")
    params.add_argument("--skip-media", action="store_true", help="Leave out video (webm, mp4) and flash (swf) posts.")
    params.add_argument("--allow-inactive", action="store_true", help="Look for changes in a pool marked inactive that has been taken before.")
    params.add_argument("--username", help="The e621 account to log in as. Also E621_USERNAME; the key is E621_API_KEY.")
    params.add_argument("--user-agent", help="The User-Agent to send; {username} is put in. Also E621_USER_AGENT. "
                        "Default: " + DEFAULT_AGENT)
    params.add_argument("--log-dir", help="Where to keep e621.log: every request and response, and the posts that could not be fetched.")
    args = params.parse_args()
    setup_logging(args.log_dir)
    try:
        return run(args)
    except Failed as error:
        LOG.error("%s", error)
        return error.code


if __name__ == "__main__":
    sys.exit(main())
