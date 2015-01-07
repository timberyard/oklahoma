oklahoma
========

Framework to check out and build branches using Oak

# $PWD/config.yaml

```
server: https://chicago.everbase.net
ca: /path/to/everbase.net.pem
user: user
token: OAUTH_TOKEN
skip_repos:
    - knowledge/books
    - everbase/builds
```

Access tokens can be generated in your GitHub account settings
under Applications. The token must have grant access to private repos.
