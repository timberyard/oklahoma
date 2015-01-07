#!/usr/bin/env python

import os
import requests
import shutil
import subprocess
import sys
import yaml

commands = [ 'fetch', 'pull', 'push' ]

def check_exec(cmd, workdir):
    """
    Execute cmd inside workdir.
    Return True on success, False on failure.

    Will prompt to retry on failure.
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
    print "\033[0;33m" + "result: " + str(res) + "\033[0;m"
    if( res != 0 ):
        sys.stdout.write("\033[0;33m")
        sys.stdout.flush()
        print "could not exec: " + cmdstr
        inp = raw_input("retry? ")
        sys.stdout.write("\033[0;m")
        sys.stdout.flush()
        if inp == "yes" or inp == "y" or inp == "":
            return check_exec(cmd, workdir)
        else:
            return False
    else:
        return True


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


def clone_or_update(config):
    """
    Clone (or otherwise update) each repo, restoring it to a fresh state.

    Return a list of tuples in the form of (source_dir, build_dir) for each repo.
    """
    directories = []
    for entity in get_all_entities(config):
        entitytype = get_entity_type(entity)
        # filter out repos that are in the list of repos to skip
        for repo in get_entity_repos(config, entity, lambda x: x['full_name'] not in config['skip_repos']):
            for branch in get_repo_branches(config, repo):
                print "\033[0;32m" + entitytype + ": " + entity['login'] + "; repo: " + repo['full_name'] + "; branch: " + branch['name'] + "\033[0;m"
                path = get_branch_path(repo, branch, "src")
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
                        return

                build_dir = get_branch_path(repo, branch, "build")
                if os.path.exists(build_dir):
                    shutil.rmtree(build_dir)
                os.makedirs(build_dir)
                directories.append((path, build_dir))
    return directories


def remove_orphans(config):
    """
    Find and delete repos that are checked-out locally but no longer exist on origin.
    """
    print "\033[0;32m" + "Finding orphans..." + "\033[0;m"
    path = []
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
                matches = get_entity_repos(config, entity, lambda x: x['full_name'] == entity_name + "/" + repo_name)
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
                    matches = get_repo_branches(config, repo, lambda x: get_branch_path(repo, x) == join_path(path))
                    if len(matches) != 1:
                        print "\033[0;33m" + "Branch " + branch_name + " is orphan, deleting" + "\033[0;m"
                        shutil.rmtree(join_path(path))
                        path.pop()
                        continue

                    path.pop() # pop branch name
                path.pop() # pop repo name
            path.pop() # pop entity name
        path.pop() # pop entity type


def main(command):
    config = yaml.load( open( "config.yaml", "r" ) )
    if not os.path.exists("orgs/"):
        os.makedirs("orgs/")
    if not os.path.exists("users/"):
        os.makedirs("users/")
    remove_orphans(config)
    clone_or_update(config)

if __name__ == "__main__":
    command = None
    if len(sys.argv) >= 2 and sys.argv[1] in commands:
        command = sys.argv[1]
    main(command)
