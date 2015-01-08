#!/usr/bin/env python

import json
import os
import requests
import shutil
import subprocess
import sys
import yaml


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


def get_branch_path(repo, branch, modifier=""):
    """
    Return a path (properly encoded for the system environment) where the given
    branch of the given repo should go.
    """
    if len(modifier) != 0:
        modifier = "/" + modifier
    return get_entity_type(repo['owner']) + "s/" + repo['full_name'] + "/" + branch['name'].replace("/","_") + modifier


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
        if f.endswith(".json"):
            return path + "/" + f
    return None


def clone_or_update(config):
    """
    Clone (or otherwise update) each repo, restoring it to a fresh state.

    Return a list of Branch objects.
    """
    available_branches = []
    for entity in get_all_entities(config):
        entitytype = get_entity_type(entity)
        # filter out repos that are in the list of repos to skip
        for repo in get_entity_repos(config, entity, get_repo_filter(config)):
            for branch in get_repo_branches(config, repo):
                print "\033[0;32m" + entitytype + ": " + entity['login'] + "; repo: " + repo['full_name'] + "; branch: " + branch['name'] + "\033[0;m"
                path = config['output_dir'] + "/" + get_branch_path(repo, branch, "src")
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
                        # TODO report this repo as failed
                        continue

                build_dir = config['output_dir'] + "/" + get_branch_path(repo, branch, "build")
                if os.path.exists(build_dir):
                    shutil.rmtree(build_dir)
                os.makedirs(build_dir)
                
                this_branch = Branch()
                this_branch.update({
                    "source_dir": path,
                    "build_dir": build_dir,
                    "repo_name": repo['full_name'],
                    "branch_name": branch['name'],
                    "commit_sha": branch['commit']['sha'],
                })
                available_branches.append(this_branch)
    return available_branches


def remove_orphans(config):
    """
    Find and delete repos that are checked-out locally but no longer exist on origin.
    """
    print "\033[0;32m" + "Finding orphans..." + "\033[0;m"
    path = [config['output_dir']]
    join_path = lambda path: "/".join(path)
    for entitytype in ["org", "user"]:
        path.append(entitytype + "s")
        print "\033[0;34m" + "Checking entity type: " + entitytype + "\033[0;m"
        for entity_name in os.listdir(join_path(path)):
            path.append(entity_name)
            print "\033[0;34m" + "Checking entity: " + join_path(path) + "\033[0;m"
            
            # check that entity exists and that type matches
            matches = get_all_entities(config, lambda x: (x['login'] == entity_name) and (get_entity_type(x) == entitytype))
            if len(matches) != 1:
                print "\033[0;33m" + "Entity " + entity_name + " is orphan, deleting." + "\033[0;m"
                shutil.rmtree(join_path(path))
                path.pop()
                continue

            # check that each repo exists
            entity = matches[0]
            for repo_name in os.listdir(join_path(path)):
                path.append(repo_name)
                print "\033[0;34m" + "Checking repo: " + join_path(path) + "\033[0;m"
                global_repo_filter = get_repo_filter(config)
                our_repo_filter = lambda x: (x['full_name'] == entity_name + "/" + repo_name) and global_repo_filter(x)
                matches = get_entity_repos(config, entity, our_repo_filter)
                if len(matches) != 1:
                    print "\033[0;33m" + "Repo " + repo_name + " is orphan, deleting." + "\033[0;m"
                    shutil.rmtree(join_path(path))
                    path.pop()
                    continue
                
                # check that each branch exits
                repo = matches[0]
                for branch_name in os.listdir(join_path(path)):
                    path.append(branch_name)
                    print "\033[0;34m" + "Checking branch: " + join_path(path) + "\033[0;m"
                    branch_filter = lambda x: config['output_dir'] + "/" + get_branch_path(repo, x) == join_path(path)
                    matches = get_repo_branches(config, repo, branch_filter)
                    if len(matches) != 1:
                        print "\033[0;33m" + "Branch " + branch_name + " is orphan, deleting" + "\033[0;m"
                        shutil.rmtree(join_path(path))
                        path.pop()
                        continue

                    path.pop() # pop branch name
                path.pop() # pop repo name
            path.pop() # pop entity name
        path.pop() # pop entity type


def build_and_publish_status(config, oak, branch):
    """
    Call oak with the given parameters.
    """
    oak_status = -1
    if (branch.get_status(config) != BranchStatus.SUCCESS) or not config['skip_if_last_success']:
        print "\033[0;32m" + "Building repo " + branch.repo_name + " branch " + branch.branch_name + "\033[0;m"
        branch.set_status(config, BranchStatus.PENDING)
        # find json config
        build_conf = find_json_file(branch.source_dir)
        if build_conf:
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
                    "-O", config['report_file'],
                ])
            oak_args.append(build_conf)
            oak_status = raw_exec(
                oak_args,
                '.'
            )
        else:
            print "\033[0;34m" + "No config for repo " + branch.repo_name + " branch " + branch.branch_name + ". Skipping." + "\033[0;m"
            return

        if oak_status == 0:
            print "\033[0;32m" + "Successfully built repo " + branch.repo_name + " branch " + branch.branch_name + "\033[0;m"
            branch.set_status(config, BranchStatus.SUCCESS)
        elif oak_status > 0 and oak_status < 200:
            print "\033[0;31m" + "Failed to build repo " + branch.repo_name + " branch " + branch.branch_name + "\033[0;m"
            branch.set_status(config, BranchStatus.FAILURE)
        else:
            print "\033[0;33m" + "Error building repo " + branch.repo_name + " branch " + branch.branch_name + "\033[0;m"
            branch.set_status(config, BranchStatus.ERROR)
    else:
        print "\033[0;32m" + "Status of repo " + branch.repo_name + " branch " + branch.branch_name + " is already Success. Skipping." + "\033[0;m"


def main(oak, config_file):
    config = yaml.load( open( config_file, "r" ) )
    out_dir = config['output_dir']
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    if not os.path.exists(out_dir + "/orgs/"):
        os.makedirs(out_dir + "/orgs/")
    if not os.path.exists(out_dir + "/users/"):
        os.makedirs(out_dir + "/users/")

    remove_orphans(config)
    branches = clone_or_update(config)

    for b in branches:
        build_and_publish_status(config, oak, b)

if __name__ == "__main__":
    usage = sys.argv[0] + " <oak> <config>"
    if len(sys.argv) != 3:
        print usage
    else:
        main(sys.argv[1], sys.argv[2])
