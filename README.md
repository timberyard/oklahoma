oklahoma
========

Framework to check out and build branches using Oak

# Installation

```
./env_setup.sh
# Set up your config file, as specified below.
source ./env/bin/activate
python oklahoma.py /path/to/ci-run /path/to/config.yaml
deactivate
```

The repos will be checked out into subdirectoreis of the current working dir.

# $PWD/config.yaml

```
server: https://chicago.everbase.net
ca: /path/to/everbase.net.pem
user: username
token: OAUTH_TOKEN
skip_repos:
    - knowledge/books
    - everbase/builds
```

Access tokens can be generated in your GitHub account settings
under Applications. The token must grant access to "repos" and "private repos".
