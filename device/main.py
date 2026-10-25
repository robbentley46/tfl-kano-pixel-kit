# Runs on boot, after boot.py. The app itself lives in app.py, which
# deploy.sh uploads precompiled as app.mpy - see app.py's docstring.
import app

app.main()
