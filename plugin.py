#the e621 plugin's own commands. tool/ is the downloader, run on a pool's address, writing its pages into
#the folder the updater hands it. a pool has no chapters, so nothing is remembered by chapter: the posts
#already taken are kept by their ids, and the next run leaves those out without asking the site for them.
#the account, its API key and the User-Agent are the plugin's own settings (plugin.json's setup), set once
#in the updater's Plugins dialog: the updater hands them to the downloader in its environment, never on its
#command line, and to the functions here as settings["setup"].
import importlib.util
import os
import re
import sys

PLUGIN = os.path.dirname(os.path.abspath(__file__))
#the downloader names each page <position>_<post id>.<ending>, and intake puts its own number in front
TAKEN = re.compile(r"_(\d+)\.[A-Za-z0-9]+$")


def build_command(settings, state, staging):
    own = settings.get("plugin") or {}
    command = [sys.executable, os.path.join(PLUGIN, "tool", "e621_pool.py"), settings["url"],
               "--output", staging,
               #kept in the plugin's own folder, since the staging folder is cleared after every run
               "--log-dir", os.path.join(PLUGIN, "logs")]
    if own.get("skip_media"):
        command.append("--skip-media")
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
    #never came. this is only a hint to the updater, which ticks ended on True; a person who unticks it has
    #every run look at the pool again, as runs always do. False, as soon as the pool is active again, lets
    #the updater mark it once more when it next goes inactive. a request that fails raises, which the
    #updater takes as no answer at all
    own = settings.get("plugin") or {}
    tool = downloader()
    tool.setup_logging(os.path.join(PLUGIN, "logs"))
    host, number = tool.pool_id(settings["url"])
    setup = settings.get("setup") or {}
    site = tool.Site(host or "e621.net", setup.get("username"), setup.get("api_key"), setup.get("user_agent"))
    pool = site.json("/pools/{0}.json".format(number))
    if pool.get("is_active", True):
        return False
    missing = set(pool.get("post_ids") or []) - set(state.get("known_posts") or [])
    if missing and own.get("skip_media"):
        #a video or flash post this comic is set to skip is never fetched, so is never taken; it is not
        #missing. asked only of a pool already inactive, and only until it is marked, so this costs a search
        #now and then, not every run
        posts = tool.pool_posts(site, pool)
        missing = set(post for post in missing if post not in posts
                      or tool.post_file(posts[post])[1] not in tool.MEDIA)
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
