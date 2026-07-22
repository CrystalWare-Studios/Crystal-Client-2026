[app]
title = Crystal Client
package.name = crystalclient
package.domain = studio.crystalware

source.dir = .
source.include_exts = py,png,jpg,jpeg,gif,html,css,js,json,txt,ttf,woff,woff2

version = 2.1.0
icon.filename = %(source.dir)s/icon.png

requirements = python3,flask,werkzeug==2.0.3,jinja2==3.1.6,markupsafe==3.0.3,itsdangerous==2.2.0,click==8.4.2,python-osc,pytz,requests==2.34.2,urllib3==2.7.0,charset-normalizer==3.4.9,idna==3.18,certifi==2026.6.17,packaging,plyer,websocket-client

orientation = landscape
fullscreen = 0

p4a.bootstrap = webview

p4a.branch = v2024.01.21

android.permissions = INTERNET,ACCESS_NETWORK_STATE

android.api = 33
android.minapi = 26
android.ndk_api = 26
android.archs = arm64-v8a
android.release_artifact = apk
android.numeric_version = 102610004

android.allow_backup = True

log_level = 2

[buildozer]
log_level = 2
warn_on_root = 0
