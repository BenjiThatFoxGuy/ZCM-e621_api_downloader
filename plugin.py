#the e621 plugin's own commands. tool/ is the downloader, run on a pool's address, writing its pages into
#the folder the updater hands it. a pool has no chapters, so nothing is remembered by chapter: the posts
#already taken are kept by their ids, and the next run leaves those out without asking the site for them.
import importlib.util
import json
import os
import re
import sys
import tempfile

PLUGIN = os.path.dirname(os.path.abspath(__file__))
#the downloader names each page <position>_<post id>.<ending>, and intake puts its own number in front
TAKEN = re.compile(r"_(\d+)\.[A-Za-z0-9]+$")
#where a run's credentials wait for the downloader, which deletes them as it reads them
RUN = os.path.join(PLUGIN, ".run")


def credentials_file(own):
    #the account and agent as a file the downloader reads, not as arguments: the updater prints a command in
    #the log and keeps it in the comic's run record, and an API key must not be in either. private to the
    #user running the updater, and empty settings leave nothing to write
    given = dict((key, own.get(key)) for key in ("username", "api_key", "user_agent") if own.get(key))
    if not given:
        return None
    os.makedirs(RUN, exist_ok=True)
    handle, path = tempfile.mkstemp(dir=RUN, suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as f:
        json.dump(given, f)
    return path


def build_command(settings, state, staging):
    own = settings.get("plugin") or {}
    command = [sys.executable, os.path.join(PLUGIN, "tool", "e621_pool.py"), settings["url"],
               "--output", staging,
               #kept in the plugin's own folder, since the staging folder is cleared after every run
               "--log-dir", os.path.join(PLUGIN, "logs")]
    if own.get("include_inactive"):
        command.append("--allow-inactive")
    credentials = credentials_file(own)
    if credentials:
        command += ["--credentials-file", credentials]
    known = state.get("known_posts") or []
    if known:
        command += ["--skip", ",".join(str(post) for post in known)]
    return command


def downloader():
    #the downloader's own code, loaded from tool/, so ended() asks the site the way a run does: the same
    #agent, the same pause between requests, the same log
    spec = importlib.util.spec_from_file_location("e621_pool", os.path.join(PLUGIN, "tool", "e621_pool.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ended(settings, state, taken):
    #complete when the site marks the pool inactive - nobody adding to it - and every post in it has been
    #taken. a post this account could not see is still missing, and marking the comic ended would mean it
    #never came; so would a comic whose settings ask to keep checking an inactive pool
    own = settings.get("plugin") or {}
    if own.get("include_inactive"):
        return False
    tool = downloader()
    tool.setup_logging(os.path.join(PLUGIN, "logs"))
    host, number = tool.pool_id(settings["url"])
    site = tool.Site(host or "e621.net", own.get("username"), own.get("api_key"), own.get("user_agent"))
    pool = site.json("/pools/{0}.json".format(number))
    if pool.get("is_active", True):
        return False
    missing = set(pool.get("post_ids") or []) - set(state.get("known_posts") or [])
    if missing:
        #a video or flash post is never fetched, so is never taken; it is not missing. asked only of a pool
        #already inactive, and only until it is marked, so this costs a search now and then, not every run
        posts = tool.pool_posts(site, pool)
        missing = set(post for post in missing if tool.post_file(posts.get(post) or {})[1] not in tool.NOT_PICTURES
                      or post not in posts)
    if missing:
        tool.LOG.info("pool %s is inactive, but %d of its posts have not been taken, so it is not marked ended",
                      number, len(missing))
        return False
    tool.LOG.info("pool %s is inactive and every post in it has been taken", number)
    return True


def remember(state, taken):
    #the posts taken in, by id, added to those from the runs before. a post the site no longer lists stays
    #here, which costs nothing: the downloader only looks the ids up in the pool's own list
    known = set(state.get("known_posts") or [])
    for name in taken["pages"]:
        found = TAKEN.search(name)
        if found:
            known.add(int(found.group(1)))
    state["known_posts"] = sorted(known)
    return state
