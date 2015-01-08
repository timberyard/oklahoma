oklahoma
========

Framework to check out and build branches using Oak

### Installation & Use

```
./env_setup.sh
# Set up your config file, as specified below.
source ./env/bin/activate
python oklahoma.py /path/to/ci-run /path/to/config.yaml
deactivate
```

The repos will be checked out into subdirectoreis of the current working dir.

### $PWD/config.yaml

```
server: https://chicago.everbase.net
ca: /path/to/everbase.net.pem
user: username
token: OAUTH_TOKEN
blacklist_repos:
    - knowledge/books
    - everbase/builds
whitelist_repos:
    - foo/bar
output_dir: ./repos
reporting_context: ci_linux
publish_status: !!bool False
```

Access tokens can be generated in your GitHub account settings
under Applications. The token must grant access to "repos" and "private repos".

### Whitelisting and Blacklisting

Repos can be white- and blacklisted, as shown above in the sample config.
Full names (IE username/repo_name) must be used.

If the whitelist is not empty, the blacklist is ignored.

Both lists must be given in the config but may be empty.

### Reporting Context

The reporting context is used to scope the build status. For example,
you could differentiate between statuses reported by linux, windows,
and mac build systems, or ci builders, security checkers, style checkers, etc.

### Publish Status

If publish_status is set to false, the build status will not be pushed to GitHub
