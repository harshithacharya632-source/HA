# Run this ONCE (locally or via `python3 build_search_index.py` on your server)
# after deploying the ia_filterdb.py fix. It creates the text index that lets
# get_search_results() use fast, indexed search instead of a full collection
# scan. Safe to run more than once - it's a no-op if the index already exists.
#
# On a large existing collection this can take a while (Mongo has to build
# the index over every current document) and uses some CPU/IO on your DB, so
# run it once, outside of a request path, rather than on every bot restart.

from database.ia_filterdb import create_search_index

if __name__ == "__main__":
    create_search_index()
