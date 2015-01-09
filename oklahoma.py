#!/usr/bin/env python

import json
import lockfile
import os
import requests
import shutil
import subprocess
import sys
import yaml


# program version
VERSION = "0.1.0"

# constants
MUTEX_FILE = "mutex"


class BranchStatus(object):
    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"
    FAILURE = "failure"


class Branch(object):
    """
    Container containing info about a branch that has been checked
    out and is locally up-to-date
    """

    def __init__(self, *args, **kwargs):
        self.source_dir = ""
        self.build_dur = ""
        self.repo_name = ""
        self.branch_name = ""
        self.commit_sha = ""
        self.update(kwargs)

    def update(self, update_dict):
        for k, v in update_dict.items():
            setattr(self, k, v)
    
    def get_status(self, config):
        """
        Return the most recent status that matches config['reporting_context']
        """
        status = requests.get(
            config['server'] + "/api/v3/repos/" + self.repo_name + "/commits/" + self.commit_sha + "/statuses",
            params={"access_token": config['token']},
            verify=config['ca']
        )
        status.raise_for_status()
        for s in status.json():
            if s['context'] == config['reporting_context']:
                return s['state']
        return BranchStatus.ERROR

    def set_status(self, config, status):
        if not config['publish_status']:
            return
        r = requests.post(
            config['server'] + "/api/v3/repos/" + self.repo_name + "/statuses/" + self.commit_sha,
            params={"access_token": config['token']},
            data=json.dumps({
                "state": status,
                "context": config['reporting_context'],
            }),
            verify=config['ca']
        )
        r.raise_for_status()


def check_exec(cmd, workdir):
    """
    Execute cmd inside workdir.
    Return True on success, False on failure.

    Will prompt to retry on failure.
    """
    res = raw_exec(cmd, workdir)
    if( res != 0 ):
        print "\033[0;33m" + "Could not execute " + cmdstr + "\033[0;m"
        print "\033[0;33m" + "Result: " + str(res) + "\033[0;m"
        sys.stdout.write("\033[0;33m")
        sys.stdout.flush()
        sys.stdout.write("\033[0;m")
        return False
    else:
        return True


def raw_exec(cmd, workdir):
    """
    Execute cmd inside workdir.
    Return the return value of cmd.
    """
    cmdstr = " ".join(cmd)
    print "\033[0;33m" + "exec: " + cmdstr + "; workdir: " + workdir + "\033[0;m"
    sys.stdout.write("\033[0;34m")
    sys.stdout.flush()
    oldcwd = os.getcwd()
    os.chdir(workdir)
    res = subprocess.call(cmd)
    os.chdir(oldcwd)
    sys.stdout.write("\033[0;m")
    sys.stdout.flush()
    return res


def get_entity_type(entity):
    return 'org' if entity['type'] == "Organization" else 'user'


def get_all_entities(config, pred=lambda x: True):
    """
    Return all Users and Organizations for which pred( entity ) returns True.

    The call to pred will be given a json object as specified in the GitHub API docs.
    """
    entities = requests.get(
        config['server'] + "/api/v3/users",
        params={"access_token": config['token']},
        verify=config['ca']
    )
    entities.raise_for_status()
    return [entity for entity in entities.json() if pred(entity)]


def get_entity_repos(config, entity, pred=lambda x: True):
    """
    Return all repositories belonging to entity that can be accessed using the given config.
    """
    # /api/v3/users only lists public repos, while /api/v3/user lists private repos as well
    api_path = "/api/v3/"
    api_path += "user" if entity['login'] == config['user'] else (get_entity_type(entity) + "s/" + entity['login'])
    api_path += "/repos"
    repos = requests.get(
        config['server'] + api_path,
        params={"access_token": config['token']},
        verify=config['ca']
    )
    repos.raise_for_status()
    return [repo for repo in repos.json() if pred(repo)]


def get_repo_branches(config, repo, pred=lambda x: True):
    """
    Return all branches of the given repo that satisfy pred.
    """
    branches = requests.get(
        config['server'] + "/api/v3/repos/" + repo['full_name'] + "/branches",
        params={"access_token": config['token']},
        verify=config['ca']
    )
    branches.raise_for_status()
    branches = branches.json()
    for b in branches:
        b.update({'type': "branch"})
    tags = requests.get(
        config['server'] + "/api/v3/repos/" + repo['full_name'] + "/tags",
        params={"access_token": config['token']},
        verify=config['ca']
    )
    tags.raise_for_status()
    tags = tags.json()
    for t in tags:
        t.update({'type': "tag"})
    combined = branches + tags
    return [branch for branch in combined if pred(branch)]


def get_branch_base_path(repo, branch):
    """
    Return a path (properly encoded for the system environment) where the given
    branch of the given repo should go.
    """
    return get_entity_type(repo['owner']) + "s/" + repo['full_name'] + "/" + branch['name'].replace("/","_")


def get_branch_source_path(repo, branch):
    """
    Return the path where the branch source code should go.
    """
    return get_branch_base_path(repo, branch) + "/src"


def get_branch_build_path(config, repo, branch):
    """
    Return the path where the branch should be built.
    """
    # get detailed commit info
    commit = requests.get(
        config['server'] + "/api/v3/repos/" + repo['full_name'] + "/git/commits/" + branch['commit']['sha'],
        params={"access_token": config['token']},
        verify=config['ca']
    )
    commit.raise_for_status()
    commit = commit.json()
    sha = commit['sha']
    timestamp = commit['author']['date']
    # some parts of the build can't handle colons in the path, apparently
    timestamp = timestamp.replace(":", "-")
    return get_branch_base_path(repo, branch) + "/builds/" + timestamp + "_" + sha

def get_repo_clone_url(config, repo):
    """
    Return the appropriate url from which to clone the branch from the repo.
    """
    clone_url = repo['clone_url']
    return clone_url.replace( "://", "://" + config['token'] + "@" )


def get_repo_filter(config):
    """
    Return a callable that satisfies the get_entity_repos(...) predicate.
    """
    whitelist = config['whitelist_repos'] or []
    blacklist = config['blacklist_repos'] or []
    whitelisting = len(whitelist) > 0
    def repo_filter(repo):
        """
        If whitelist is not empty, allow only whitelisted repos.
        If whitelist is empty, exclude any blacklisted repos.
        """
        if whitelisting:
            return repo['full_name'] in whitelist
        else:
            return repo['full_name'] not in blacklist
    return repo_filter


def find_json_file(path):
    """
    Find the first .json file in the given path.
    """
    files = os.listdir(path)
    files.sort()
    for f in files:
        if f == "ci.json":
            return path + "/" + f
    return None


def try_lock_branch(config, repo, branch):
    """
    Try to lock the given branch.
    Rerturn None if the lock cannot be aquired, or a LockFile object that
    has the lock.
    """
    branch_lock_path = config['output_dir'] + "/" + get_branch_base_path(repo, branch)
    branch_lock_mutex = branch_lock_path + "/" + MUTEX_FILE
    # make sure mutex file exists
    if not os.path.exists(branch_lock_path):
        os.makedirs(branch_lock_path)
    if not os.path.isfile(branch_lock_mutex):
        open(branch_lock_mutex, 'a').close()
        
    lock = lockfile.LockFile(branch_lock_mutex)
    try:
        # negative timeout will raise AlreayLocked
        lock.acquire(timeout=-1)
        return lock
    except:
        return None


def unlock_branch(lock):
    """
    Unlock the given branch. If the branch is not locked, this method does nothing.
    """
    try:
        lock.release()
    # all other exceptions are bad
    except NotLocked:
        pass


def clone_or_update(config):
    """
    Clone (or otherwise update) each repo, restoring it to a fresh state.
    Lock branch directory.
    Return a list of Branch objects.
    """
    available_branches = []
    for entity in get_all_entities(config):
        entitytype = get_entity_type(entity)
        # filter out repos that are in the list of repos to skip
        for repo in get_entity_repos(config, entity, get_repo_filter(config)):
            for branch in get_repo_branches(config, repo):
                print "\033[0;32m" + entitytype + ": " + entity['login'] + "; repo: " + repo['full_name'] + "; branch: " + branch['name'] + "\033[0;m"

                branch_lock = try_lock_branch(config, repo, branch)
                if branch_lock is None:
                    print "\033[0;31m" + "Skipping locked repo " + repo['full_name'] + " branch " + branch['name'] + "\033[0;m"
                    continue

                path = config['output_dir'] + "/" + get_branch_source_path(repo, branch)
                if os.path.exists(path + "/.git"):
                    # already cloned, perform update
                    print "\033[0;32m" + "Repo at " + path + " already exists, updating." + "\033[0;m"
                    update_success = check_exec(
                        [
                            "git",
                            "clean",
                            "-fxd"
                        ],
                        path
                    )
                    update_success &= check_exec(
                        [
                            "git",
                            "fetch",
                            "--all",
                        ],
                        path
                    )
                    update_success &= check_exec(
                        [
                            "git",
                            "fetch",
                            "--tags",
                        ],
                        path
                    )
                    # you can't reset a tag, so tags have to be handled differently
                    if branch['type'] == "branch":
                        update_success &= check_exec(
                            [
                                "git",
                                "reset",
                                "--hard",
                                "origin/" + branch['name'],
                            ],
                            path
                        )
                    else:
                        update_success &= check_exec(
                            [
                                "git",
                                "checkout",
                                branch['name'],
                            ],
                            path
                        )

                    if not update_success:
                        # update failed, delete repo and clone again
                        print "\033[0;32m" + "Updating repo at " + path + " failed. Cloning instead." + "\033[0;m"
                        shutil.rmtree(path)

                if not os.path.exists(path + "/.git"):
                    os.makedirs(path)
                    clone_success = check_exec(
                        # each part of the command is a list element
                        # since subprocess.call() expects it that way
                        [
                            "git",
                            "clone",
                            "-b",
                            branch['name'],
                            "-v",
                            get_repo_clone_url(config, repo),
                            path,
                        ],
                        '.'
                    )
                    if not clone_success:
                        print "\033[0;31m" + "Failed to clone repo " + repo['full_name'] + " branch " + branch['name'] + "\033[0;m"
                        continue

                build_dir = config['output_dir'] + "/" + get_branch_build_path(config, repo, branch)
                this_branch = Branch()
                this_branch.update({
                    "source_dir": path,
                    "build_dir": build_dir,
                    "repo_name": repo['full_name'],
                    "branch_name": branch['name'],
                    "commit_sha": branch['commit']['sha'],
                    "lock": branch_lock,
                })
                available_branches.append(this_branch)
    return available_branches

def build_and_publish_status(config, oak, branch):
    """
    Call oak with the given parameters.
    Unlock the branch when finished.
    """
    if os.path.exists(branch.build_dir) and not config['force_rebuild']:
        print "\033[0;32m" + "Build for repo " + branch.repo_name + " branch " + branch.branch_name + " commit " + branch.commit_sha + "already exists. Skipping." + "\033[0;m"
        unlock_branch(branch.lock)
        return
    else:
        print "\033[0;32m" + "Building repo " + branch.repo_name + " branch " + branch.branch_name + " commit " + branch.commit_sha + "\033[0;m"
        # find json config
        build_conf = find_json_file(branch.source_dir)
        if build_conf is None:
            print "\033[0;34m" + "No config for repo " + branch.repo_name + " branch " + branch.branch_name + ". Skipping." + "\033[0;m"
            unlock_branch(branch.lock)
            return
        else:
            # if we get here we're forceing the rebuild, so throw away the old build
            if os.path.exists(branch.build_dir):
                shutil.rmtree(branch.build_dir)
            os.makedirs(branch.build_dir)
            oak_status = -1
            branch.set_status(config, BranchStatus.PENDING)
            oak_args = [
                oak,
                "-i", os.path.abspath(branch.source_dir),
                "-o", os.path.abspath(branch.build_dir),
                "-r", branch.repo_name,
                "-b", branch.branch_name,
                "-c", branch.commit_sha,
            ]
            if 'report_file' in config:
                oak_args.extend([
                    "-O", os.path.abspath(branch.build_dir) + "/" + config['report_file']
                ])
            oak_args.append(build_conf)
            oak_status = raw_exec(
                oak_args,
                '.'
            )
            if oak_status == 0:
                print "\033[0;32m" + "Successfully built repo " + branch.repo_name + " branch " + branch.branch_name + "\033[0;m"
                branch.set_status(config, BranchStatus.SUCCESS)
            elif oak_status == 1:
                print "\033[0;33m" + "Error with build tool while building repo " + branch.repo_name + " branch " + branch.branch_name + "\033[0;m"
                branch.set_status(config, BranchStatus.ERROR)
                shutil.rmtree(branch.build_dir)
            elif oak_status == 2:
                print "\033[0;31m" + "Failed to build repo " + branch.repo_name + " branch " + branch.branch_name + "\033[0;m"
                branch.set_status(config, BranchStatus.FAILURE)
            else:
                print "\033[0;31m" + "Build tool returned with invalid value: " + str(oak_status) + "\033[0;m"
                branch.set_status(config, BranchStatus.ERROR)
                shutil.rmtree(branch.build_dir)
    unlock_branch(branch.lock)


def main(oak, config_file):
    config = yaml.load( open( config_file, "r" ) )
    out_dir = config['output_dir']
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    if not os.path.exists(out_dir + "/orgs/"):
        os.makedirs(out_dir + "/orgs/")
    if not os.path.exists(out_dir + "/users/"):
        os.makedirs(out_dir + "/users/")

    branches = clone_or_update(config)

    for b in branches:
        build_and_publish_status(config, oak, b)

if __name__ == "__main__":
    usage = sys.argv[0] + " <oak> <config>"
    if len(sys.argv) != 3:
        print usage
    else:
        main(sys.argv[1], sys.argv[2])
