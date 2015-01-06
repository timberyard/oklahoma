#!/usr/bin/env python

import yaml
import requests
import sys
import os
import subprocess

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
    return branches.json()


def get_branch_path(repo, branch):
    """
    Return a path (properly encoded for the system environment) where the given
    branch of the given repo should go.
    """
    return get_entity_type(repo['owner']) + "s/" + repo['full_name'] + "/" + branch['name']


def get_repo_clone_url(config, repo):
    """
    Return the appropriate url from which to clone the branch from the repo.
    """
    clone_url = repo['clone_url']
    return clone_url.replace( "://", "://" + config['token'] + "@" )


def run_command(config, command):
    """
    Run command in each repository.
    If the repo does not exist, it will be cloned first.
    """
    for entity in get_all_entities(config):
        entitytype = get_entity_type(entity)
        # filter out repos that are in the list of repos to skip
        for repo in get_entity_repos(config, entity, lambda x: x['full_name'] not in config['skip_repos']):
            for branch in get_repo_branches(config, repo):
                print "\033[0;32m" + entitytype + ": " + entity['login'] + "; repo: " + repo['full_name'] + "; branch: " + branch['name'] + "\033[0;m"
                path = get_branch_path(repo, branch)
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
                        inp = raw_input("continue? ")
                        if inp != "yes" and inp != "y" and inp != "":
                            return
                else:
                    print "\033[0;32m" + "Repo at " + path + " already exists, not cloning." + "\033[0;m"

                if not command is None:
                    if check_exec(['git', command, '-v'], path) == False:
                        inp = raw_input("reset? ")
                        if inp == "yes" or inp == "y" or inp == "":
                            check_exec(['git', 'reset', '--hard', 'origin/' + branch['name']], path)
                            check_exec(['git', command, '-v'], path)
                        else:
                            inp = raw_input("continue? ")
                            if inp != "yes" and inp != "y" and inp != "":
                                return


def check_orphans(config):
    print "\033[0;32m" + "finding orphans..." + "\033[0;m"
    oc = 0
    for entitytype in ["org", "user"]:
        for entity in os.listdir(entitytype + "s/"):
            entityreq = requests.get( config['server'] + "/api/v3/users/" + entity, params={"access_token": config['token']}, verify=config['ca'] )
            entityorphan = True if entityreq.status_code == 404 else False
            if entityorphan == False:
                entityreq.raise_for_status()
                entitydata = entityreq.json()
                entityorphan = True if ((entitydata['type'] == "Organization" and entitytype == "user") or (entitydata['type'] == "User" and entitytype == "org")) else False
                if entityorphan == False:
                    for repo in os.listdir(entitytype + "s/" + entity + "/"):
                        reporeq = requests.get( config['server'] + "/api/v3/repos/" + entity + "/" + repo, params={"access_token": config['token']}, verify=config['ca'] )
                        if reporeq.status_code != 404:
                            reporeq.raise_for_status()
                            for branch in os.listdir(entitytype + "s/" + entity + "/" + repo + "/"):
                                if branch == "feature" or branch == "release":
                                    for subbranch in os.listdir(entitytype + "s/" + entity + "/" + repo + "/" + branch + "/"):
                                        subbranchreq = requests.get( config['server'] + "/api/v3/repos/" + entity + "/" + repo + "/branches/" + branch + "/" + subbranch, params={"access_token": config['token']}, verify=config['ca'] )
                                        if subbranchreq.status_code != 404:
                                            subbranchreq.raise_for_status()
                                        else:
                                            print "\033[0;33m" + "orphan detected: branch '" + branch + "/" + subbranch + "' of repo '" + repo + "' of " + entitytype + " '" + entity + "'\033[0;m"
                                            oc += 1
                                else:
                                    branchreq = requests.get( config['server'] + "/api/v3/repos/" + entity + "/" + repo + "/branches/" + branch, params={"access_token": config['token']}, verify=config['ca'] )
                                    if branchreq.status_code != 404:
                                        branchreq.raise_for_status()
                                    else:
                                        print "\033[0;33m" + "orphan detected: branch '" + branch + "' of repo '" + repo + "' of " + entitytype + " '" + entity + "'\033[0;m"
                                        oc += 1
                        else:
                            print "\033[0;33m" + "orphan detected: repo '" + repo + "' of " + entitytype + " '" + entity + "'\033[0;m"
                            oc += 1
            if entityorphan == True:
                print "\033[0;33m" + "orphan detected: " + entitytype + " '" + entity + "'\033[0;m"
                oc += 1
    print "\033[0;32m" + str(oc) + " orphans found" + "\033[0;m"

def main(command):
    config = yaml.load( open( "ghc.conf", "r" ) )
    if not os.path.exists("orgs/"):
        os.makedirs("orgs/")
    if not os.path.exists("users/"):
        os.makedirs("users/")
    run_command(config, command)
    check_orphans(config)

if __name__ == "__main__":
    command = None
    if len(sys.argv) >= 2 and sys.argv[1] in commands:
        command = sys.argv[1]
    main(command)
